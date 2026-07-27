"""Temporal activity implementations wrapping scraper/enrichment logic.

Each activity is a free function decorated with @activity.defn, invoked by
Temporal workflows running on realtor-scrape-queue or realtor-heal-queue.

The batch_id parameter is passed from the workflow to every activity so that
scraper subprocesses inherit OTEL_BATCH_ID for trace correlation."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import urllib.parse
import urllib.request
import urllib.error

from temporalio import activity

BASE_DIR = Path(__file__).parent
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"
PYTHON = VENV_PYTHON if VENV_PYTHON.exists() else sys.executable
LOKI_URL = "http://localhost:3100/loki/api/v1/query_range"


def _run_script(cmd_args, label, timeout=300, batch_id=None):
    """Run a Python script via subprocess and return the result dict."""
    env = os.environ.copy()
    if batch_id:
        env["OTEL_BATCH_ID"] = batch_id
    activity.logger.info(f"Running: {label} — {' '.join(cmd_args)}")
    try:
        r = subprocess.run(
            cmd_args,
            capture_output=True, text=True, timeout=timeout,
            cwd=BASE_DIR, env=env,
        )
        success = r.returncode == 0
        activity.logger.info(f"{label} exit={r.returncode} {'OK' if success else 'FAIL'}")
        return {
            "label": label,
            "success": success,
            "returncode": r.returncode,
            "stdout": r.stdout.strip()[-1000:],
            "stderr": r.stderr.strip()[-1000:],
        }
    except subprocess.TimeoutExpired:
        activity.logger.error(f"{label} timed out after {timeout}s")
        return {"label": label, "success": False, "returncode": -1, "error": "timeout"}
    except Exception as e:
        activity.logger.error(f"{label} exception: {e}")
        return {"label": label, "success": False, "returncode": -1, "error": str(e)}


# ---------------------------------------------------------------------------
# Scrape activities  (realtor-scrape-queue)
# ---------------------------------------------------------------------------

@activity.defn
async def scrape_city(city: str, state: str, batch_id: str = "", max_pages: int = 3) -> dict:
    """Scrape one Zillow city via fetch_zillow_crawl4ai.py."""
    cmd = [str(PYTHON), "app/data_providers/residential/fetch_zillow_crawl4ai.py",
           "--city", city, "--state", state,
           "--max-pages", str(max_pages), "--db"]
    return _run_script(cmd, f"Zillow {city}, {state}", batch_id=batch_id)


@activity.defn
async def run_fsbo(batch_id: str = "") -> dict:
    """Run fetch_fsbo.py."""
    cmd = [str(PYTHON), "app/data_providers/residential/fetch_fsbo.py", "--db"]
    return _run_script(cmd, "FSBO", batch_id=batch_id)


@activity.defn
async def run_land_and_farm(batch_id: str = "", state: str = "TX", min_acres: int = 5) -> dict:
    """Run LandAndFarm scraper."""
    cmd = [str(PYTHON), "-m", "data_center.commercial.fetch_landandfarm",
           "--state", state, "--min-acres", str(min_acres)]
    return _run_script(cmd, "LandAndFarm", batch_id=batch_id)


# ---------------------------------------------------------------------------
# Housekeeping activities  (realtor-scrape-queue)
# ---------------------------------------------------------------------------

@activity.defn
async def find_deals(batch_id: str = "") -> dict:
    """Run find_deals.py."""
    cmd = [str(PYTHON), "scripts/find_deals.py", "--json", "weekly_deals.json"]
    return _run_script(cmd, "Find deals", batch_id=batch_id)


@activity.defn
async def sweep_sold(batch_id: str = "") -> dict:
    """Run scripts/sweep_sold.py."""
    cmd = [str(PYTHON), "scripts/sweep_sold.py"]
    return _run_script(cmd, "Sweep sold", batch_id=batch_id)


@activity.defn
async def clean_city_data(batch_id: str = "") -> dict:
    """Run scripts/clean_city_data.py."""
    cmd = [str(PYTHON), "scripts/clean_city_data.py"]
    return _run_script(cmd, "Clean city names", batch_id=batch_id)


# ---------------------------------------------------------------------------
# Health / utility  (realtor-heal-queue)
# ---------------------------------------------------------------------------

@activity.defn
async def query_loki(query: str, minutes: int = 30, limit: int = 50) -> dict:
    """Query Loki for recent log entries."""
    params = urllib.parse.urlencode({
        "query": query,
        "limit": limit,
        "start": int((datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp()) * 1_000_000_000,
        "end": int(datetime.now(timezone.utc).timestamp()) * 1_000_000_000,
    })
    url = f"{LOKI_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        streams = data.get("data", {}).get("result", [])
        results = []
        for stream_obj in streams:
            labels = stream_obj.get("stream", stream_obj.get("metric", {}))
            module = labels.get("module", "unknown")
            values = stream_obj.get("values", [])
            for ts, msg in values:
                results.append({"module": module, "timestamp": ts, "message": msg})
        return {"success": True, "count": len(results), "results": results[:limit]}
    except Exception as e:
        activity.logger.error(f"Loki query failed: {e}")
        return {"success": False, "count": 0, "results": [], "error": str(e)}


@activity.defn
async def remediate_scraper(module: str, filename: str, batch_id: str = "") -> dict:
    """Re-run a failed scraper for a specific module."""
    scraper_map = {
        "fetch_zillow": [str(PYTHON), "app/data_providers/residential/fetch_zillow_crawl4ai.py", "--db", "--max-pages", "1"],
        "fetch_fsbo": [str(PYTHON), "app/data_providers/residential/fetch_fsbo.py", "--db"],
        "fetch_landandfarm": [str(PYTHON), "-m", "data_center.commercial.fetch_landandfarm", "--state", "TX", "--min-acres", "5"],
    }
    script = None
    for key, cmd in scraper_map.items():
        if key in filename or key in module:
            script = cmd
            break
    if not script:
        return {"action": "no_op", "reason": f"no scraper mapping for {module}/{filename}", "success": False}
    return _run_script(script, f"remediate {module}", batch_id=batch_id)


@activity.defn
async def log_health_pulse(batch_id: str = "") -> dict:
    """Emit health pulse to Loki via log_health_pulse.py."""
    cmd = [str(PYTHON), "app/utils/log_health_pulse.py"]
    return _run_script(cmd, "Health pulse", batch_id=batch_id)
