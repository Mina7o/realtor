"""Read data/status.json and write a structured health pulse to Loki's log pipeline.
Run after each scrape to make health status visible in Grafana."""
import sys
sys.path.insert(0, __import__('os').path.abspath(__import__('os').path.join(__import__('os').path.dirname(__file__), '..', '..')))

import json
from pathlib import Path

from logger_setup import setup_logging
from loguru import logger

setup_logging("health_pulse")

STATUS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "status.json"
DETAIL_LOG = Path(__file__).resolve().parent.parent.parent / "logs" / "health_pulse.log"


def main():
    if not STATUS_PATH.exists():
        logger.warning("No status.json yet — skipping health pulse")
        return

    data = json.loads(STATUS_PATH.read_text())

    success = data.get("success", False)
    errors = data.get("errors", 0)
    finished_at = data.get("finished_at", "unknown")
    job = data.get("job", "?")
    total_steps = data.get("total_steps", 0)
    day = data.get("day", "?")

    if success and errors == 0:
        logger.info(
            "HEALTH_OK | job={job} day={day} errors={errors} steps={total_steps} finished={finished_at}",
            job=job, day=day, errors=errors, total_steps=total_steps, finished_at=finished_at,
        )
    elif errors > 0:
        logger.error(
            "HEALTH_DEGRADED | job={job} day={day} errors={errors} steps={total_steps} finished={finished_at}",
            job=job, day=day, errors=errors, total_steps=total_steps, finished_at=finished_at,
        )
    else:
        logger.warning(
            "HEALTH_UNKNOWN | job={job} day={day} errors={errors} steps={total_steps} finished={finished_at}",
            job=job, day=day, errors=errors, total_steps=total_steps, finished_at=finished_at,
        )


if __name__ == "__main__":
    sys.exit(main())
