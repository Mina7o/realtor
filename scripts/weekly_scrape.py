"""Daily scrape orchestrator: distributes Zillow cities across Mon-Fri.
Each day runs its assigned Zillow cities + housekeeping (deals, sweep, cleanup).

Schedule (cron: 0 8 * * 1-5):
  Mon: Fort Mill, Weddington, Waxhaw + FSBO
  Tue: Indian Trail, Monroe
  Wed: Austin, San Antonio + LandAndFarm
  Thu: Dallas, Fort Worth
  Fri: Houston + LandAndFarm
  Sat/Sun: no scraper run

Logs to: logs/weekly_scrape_YYYY-MM-DD.log (rotated, 7-day retention)
"""
import sys
sys.path.insert(0, __import__('os').path.abspath(__import__('os').path.join(__import__('os').path.dirname(__file__), '..')))

import datetime
import json
import random
import subprocess
import os
import time
import uuid
from pathlib import Path

from logger_setup import setup_logging
from loguru import logger

setup_logging("weekly_scrape")

SCRIPT_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = SCRIPT_DIR / "venv" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

STEP_LOG = []

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
    "mon": [("FSBO", [PYTHON, "app/data_providers/residential/fetch_fsbo.py", "--db"])],
    "wed": [("LandAndFarm", [PYTHON, "-m", "data_center.commercial.fetch_landandfarm", "--state", "TX", "--min-acres", "5"])],
    "fri": [("LandAndFarm", [PYTHON, "-m", "data_center.commercial.fetch_landandfarm", "--state", "TX", "--min-acres", "5"])],
}


def step(name, status, detail=""):
    STEP_LOG.append({"step": name, "status": status, "detail": detail, "time": datetime.datetime.now().isoformat()})


def pause():
    delay = random.uniform(3.0, 8.0)
    logger.info(f"Cooling {delay:.1f}s...")
    time.sleep(delay)


def run(cmd, label, wait=True):
    logger.info(f"Starting: {label}")
    logger.debug(f"Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            logger.debug(f"[stdout] {line}")
    if result.returncode != 0:
        logger.error(f"FAILED (exit {result.returncode}) — {label}")
        step(label, "FAILED", f"exit_code={result.returncode}")
        for line in result.stderr.strip().split("\n"):
            line = line.strip()
            if line:
                logger.error(f"[stderr] {line}")
    else:
        if result.stderr.strip():
            for line in result.stderr.strip().split("\n"):
                line = line.strip()
                if line:
                    logger.warning(f"[stderr] {line}")
        logger.success(f"OK — {label}")
        step(label, "OK")
    if wait:
        pause()
    return result.returncode


def run_daily_batch(day_label):
    errors = 0
    cities = DAILY_CITIES.get(day_label, [])
    specials = DAILY_SPECIAL.get(day_label, [])

    if cities:
        logger.info(f"--- Zillow Cities ({day_label}) ---")
        for city, state in cities:
            c = run([
                PYTHON, "app/data_providers/residential/fetch_zillow_crawl4ai.py",
                "--city", city, "--state", state,
                "--max-pages", "3", "--db",
            ], f"Zillow {city}, {state}")
            if c != 0:
                errors += 1

    for label, cmd in specials:
        logger.info(f"--- {label} ---")
        c = run(cmd, label, wait=(label != "LandAndFarm"))
        if c != 0:
            errors += 1

    return errors


def run_housekeeping():
    errors = 0

    logger.info("--- Deal Finding ---")
    c = run([PYTHON, "scripts/find_deals.py", "--json", "weekly_deals.json"], "Find deals")
    if c != 0:
        errors += 1

    logger.info("--- Sweep Sold ---")
    c = run([PYTHON, "scripts/sweep_sold.py"], "Sweep sold")
    if c != 0:
        errors += 1

    c = run([PYTHON, "scripts/clean_city_data.py"], "Clean city names")
    if c != 0:
        errors += 1

    return errors


def main():
    weekday = datetime.date.today().weekday()
    day_label = WEEKDAYS[weekday]

    batch_id = uuid.uuid4().hex[:12]
    os.environ["OTEL_BATCH_ID"] = batch_id
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    logger.info("=" * 60)
    logger.info(f"DAILY SCRAPE STARTED — {day_label.upper()} (batch={batch_id})")
    logger.info(f"OTEL_BATCH_ID={batch_id}")
    logger.info("=" * 60)

    scrape_errors = run_daily_batch(day_label)
    hk_errors = run_housekeeping()

    errors = scrape_errors + hk_errors

    status = {
        "job": "daily_scrape",
        "day": day_label,
        "success": errors == 0,
        "errors": errors,
        "finished_at": datetime.datetime.now().isoformat(),
        "steps": STEP_LOG,
    }
    status_path = SCRIPT_DIR / "data" / "status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2))

    run([PYTHON, "app/utils/log_health_pulse.py"], "Health pulse", wait=False)

    run([PYTHON, "app/workflows/auto_heal.py"], "Auto-heal", wait=False)

    status["batch_id"] = batch_id
    status_path.write_text(json.dumps(status, indent=2))

    failed_steps = [s for s in STEP_LOG if s["status"] == "FAILED"]
    if failed_steps:
        logger.error(f"Failed steps ({len(failed_steps)}):")
        for s in failed_steps:
            logger.error(f"  - {s['step']}: {s['detail']}")

    logger.info("=" * 60)
    if errors:
        logger.warning(f"DAILY SCRAPE FINISHED with {errors} error(s)")
    else:
        logger.info("DAILY SCRAPE COMPLETED SUCCESSFULLY")
    logger.info(f"Log saved to logs/weekly_scrape.log")
    logger.info(f"Batch: {batch_id}")
    logger.info("=" * 60)
    return errors


if __name__ == "__main__":
    sys.exit(main())
