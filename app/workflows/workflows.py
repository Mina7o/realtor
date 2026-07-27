"""Temporal workflow definitions for the realtor scrape + auto-heal pipeline.

DailyScrapeWorkflow
  - Accepts a day_label ("mon".."fri") and executes the city scrapes +
    special scrapers + housekeeping for that day, sequentially with delays.
  - Exposes query handlers so Temporal Web (localhost:8233) shows live state.

AutoHealWorkflow
  - Runs on a cron schedule (every 15 min), queries Loki for errors, and
    attempts remediation via activities.

Each workflow generates a batch_id from the Temporal run_id for trace
correlation across all activity subprocesses."""

import asyncio
import random

from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        scrape_city,
        run_fsbo,
        run_land_and_farm,
        find_deals,
        sweep_sold,
        clean_city_data,
        query_loki,
        remediate_scraper,
        log_health_pulse,
    )

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DAILY_CITIES = {
    "mon": [("Fort Mill", "SC"), ("Weddington", "NC"), ("Waxhaw", "NC")],
    "tue": [("Indian Trail", "NC"), ("Monroe", "NC")],
    "wed": [("Austin", "TX"), ("San Antonio", "TX")],
    "thu": [("Dallas", "TX"), ("Fort Worth", "TX")],
    "fri": [("Houston", "TX")],
    "sat": [],
    "sun": [],
}

DAILY_SPECIAL = {
    "mon": ["fsbo"],
    "wed": ["landandfarm"],
    "fri": ["landandfarm"],
}

ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=120),
    backoff_coefficient=2.0,
    maximum_attempts=5,
)

SCRAPE_TIMEOUT = timedelta(seconds=600)
HEAL_TIMEOUT = timedelta(seconds=300)


# ---------------------------------------------------------------------------
# DailyScrapeWorkflow
# ---------------------------------------------------------------------------

@workflow.defn
class DailyScrapeWorkflow:
    """Sequential daily scrape: city Zillow scrapes → special scrapers → housekeeping."""

    def __init__(self):
        self._completed = []
        self._current_step = ""
        self._error_count = 0
        self._batch_id = ""

    @workflow.run
    async def run(self, day_label: str) -> dict:
        """Execute the scrape batch for a given weekday label."""
        if day_label not in WEEKDAYS:
            return {"success": False, "error": f"invalid day: {day_label}"}

        self._batch_id = workflow.info().run_id[:12]

        cities = DAILY_CITIES.get(day_label, [])
        specials = DAILY_SPECIAL.get(day_label, [])
        errors = 0

        # -- Zillow cities --
        for city, state in cities:
            self._current_step = f"Zillow {city}, {state}"
            workflow.logger.info(f"[{self._batch_id}] {self._current_step}")
            result = await workflow.execute_activity(
                scrape_city,
                args=[city, state, self._batch_id, 3],
                start_to_close_timeout=SCRAPE_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            if not result.get("success"):
                errors += 1
            self._completed.append(self._current_step)
            await asyncio.sleep(5)

        # -- Special scrapers --
        for s in specials:
            if s == "fsbo":
                self._current_step = "FSBO"
                workflow.logger.info(f"[{self._batch_id}] {self._current_step}")
                result = await workflow.execute_activity(
                    run_fsbo,
                    args=[self._batch_id],
                    start_to_close_timeout=SCRAPE_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
            elif s == "landandfarm":
                self._current_step = "LandAndFarm"
                workflow.logger.info(f"[{self._batch_id}] {self._current_step}")
                result = await workflow.execute_activity(
                    run_land_and_farm,
                    args=[self._batch_id, "TX", 5],
                    start_to_close_timeout=SCRAPE_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
            else:
                continue
            if not result.get("success"):
                errors += 1
            self._completed.append(self._current_step)
            await asyncio.sleep(5)

        # -- Housekeeping --
        for hk_step in ["find_deals", "sweep_sold", "clean_city_data"]:
            activity_fn = {"find_deals": find_deals, "sweep_sold": sweep_sold, "clean_city_data": clean_city_data}[hk_step]
            self._current_step = hk_step.replace("_", " ").title()
            workflow.logger.info(f"[{self._batch_id}] {self._current_step}")
            result = await workflow.execute_activity(
                activity_fn,
                args=[self._batch_id],
                start_to_close_timeout=SCRAPE_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            if not result.get("success"):
                errors += 1
            self._completed.append(self._current_step)
            await asyncio.sleep(3)

        self._error_count = errors
        success = errors == 0

        # Health pulse at end
        await workflow.execute_activity(
            log_health_pulse,
            args=[self._batch_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info(f"[{self._batch_id}] {'OK' if success else f'{errors} error(s)'}")
        return {"success": success, "errors": errors, "batch_id": self._batch_id, "day": day_label}

    @workflow.query
    def progress(self) -> dict:
        """Query handler — visible in Temporal Web UI."""
        return {
            "batch_id": self._batch_id,
            "current_step": self._current_step,
            "completed": self._completed,
            "error_count": self._error_count,
        }


# ---------------------------------------------------------------------------
# AutoHealWorkflow
# ---------------------------------------------------------------------------

@workflow.defn
class AutoHealWorkflow:
    """Periodic auto-heal: query Loki → diagnose → remediate.

    Designed to run every 15 min via Temporal cron schedule."""

    def __init__(self):
        self._batch_id = ""
        self._last_result = {}

    @workflow.run
    async def run(self) -> dict:
        """Run one auto-heal cycle."""
        self._batch_id = workflow.info().run_id[:12]
        workflow.logger.info(f"[{self._batch_id}] Auto-heal cycle started")

        # 1. Query Loki for recent errors
        loki_query_str = '{job="realtor"} | level="ERROR"'
        loki_result = await workflow.execute_activity(
            query_loki,
            args=[loki_query_str, 30, 20],
            start_to_close_timeout=timedelta(seconds=30),
        )
        errors = loki_result.get("results", [])
        if not errors:
            workflow.logger.info(f"[{self._batch_id}] No recent errors — healthy")
            self._last_result = {"success": True, "errors_found": 0}
            return {"success": True, "errors_found": 0, "batch_id": self._batch_id}

        workflow.logger.warning(f"[{self._batch_id}] {len(errors)} error(s) found")
        remediated = []
        unresolved = []
        seen_modules = set()

        for err in errors:
            module = err.get("module", "unknown")
            if module in seen_modules:
                continue
            seen_modules.add(module)
            msg = err.get("message", "")
            filename = ""

            # Attempt remediation for scraper errors
            result = await workflow.execute_activity(
                remediate_scraper,
                args=[module, filename, self._batch_id],
                start_to_close_timeout=HEAL_TIMEOUT,
            )
            if result.get("success"):
                remediated.append(module)
                workflow.logger.info(f"[{self._batch_id}] Remediated: {module}")
            else:
                unresolved.append(module)
                workflow.logger.warning(f"[{self._batch_id}] Unresolved: {module}")

        # Health pulse
        await workflow.execute_activity(
            log_health_pulse,
            args=[self._batch_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        result = {
            "success": len(unresolved) == 0,
            "errors_found": len(seen_modules),
            "remediated": remediated,
            "unresolved": unresolved,
            "batch_id": self._batch_id,
        }
        self._last_result = result
        workflow.logger.info(f"[{self._batch_id}] heal done — {len(remediated)} healed, {len(unresolved)} unresolved")
        return result

    @workflow.query
    def last_result(self) -> dict:
        """Query handler for Temporal Web UI."""
        return self._last_result
