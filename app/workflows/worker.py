"""Temporal worker process for the realtor pipeline.

Registers workflow+activity implementations on two task queues:
  - realtor-scrape-queue  → DailyScrapeWorkflow + scrape/housekeeping activities
  - realtor-heal-queue    → AutoHealWorkflow + heal activities

Usage:
    python -m app.workflows.worker                          # run both queues
    python -m app.workflows.worker --queue scrape           # only scrape queue
    python -m app.workflows.worker --queue heal             # only heal queue
    python -m app.workflows.worker --dry-run                # log what would happen, don't connect
    python -m app.workflows.worker --dry-run --queue scrape # dry-run scrape only

Designed to run as a systemd/supervisor daemon on the host (not in Docker)."""

import argparse
import asyncio
import json
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

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
from app.workflows.workflows import DailyScrapeWorkflow, AutoHealWorkflow

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPORAL_HOST = "localhost:7233"
NAMESPACE = "default"
SCRAPE_QUEUE = "realtor-scrape-queue"
HEAL_QUEUE = "realtor-heal-queue"

SCRAPE_ACTIVITIES = [
    scrape_city,
    run_fsbo,
    run_land_and_farm,
    find_deals,
    sweep_sold,
    clean_city_data,
    log_health_pulse,
]

HEAL_ACTIVITIES = [
    query_loki,
    remediate_scraper,
    log_health_pulse,
]


async def connect(max_retries: int = 5, delay: float = 3.0) -> Client:
    """Connect to Temporal server with retries."""
    import time as sync_time

    for attempt in range(1, max_retries + 1):
        try:
            client = await Client.connect(TEMPORAL_HOST, namespace=NAMESPACE)
            print(f"Connected to Temporal at {TEMPORAL_HOST} (namespace={NAMESPACE})")
            return client
        except Exception as e:
            if attempt < max_retries:
                print(f"Connection attempt {attempt}/{max_retries} failed: {e} — retrying in {delay}s...")
                sync_time.sleep(delay)
            else:
                print(f"Failed to connect after {max_retries} attempts: {e}")
                raise


async def run_worker(client: Client, queue: str, workflows: list, activities: list):
    """Create and run a single Temporal Worker."""
    print(f"Starting worker on queue '{queue}' — {len(workflows)} workflow(s), {len(activities)} activity(s)")
    worker = Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await worker.run()


def dry_run_scrape():
    """Simulate a scrape day without connecting to Temporal."""
    from app.workflows.workflows import DAILY_CITIES, DAILY_SPECIAL

    day = "mon"
    print("=" * 60)
    print("DRY RUN — DailyScrapeWorkflow (simulated)")
    print("=" * 60)
    batch_id = "dry-run-batch"
    os.environ["OTEL_BATCH_ID"] = batch_id
    print(f"batch_id={batch_id}")
    print()

    cities = DAILY_CITIES.get(day, [])
    specials = DAILY_SPECIAL.get(day, [])
    total = 0

    print(f"[DRY] Would execute {len(cities)} Zillow city scrape(s) and {len(specials)} special(s)")
    for city, state in cities:
        print(f"  [DRY] scrape_city({city!r}, {state!r}, batch_id={batch_id})")
        total += 1
    for s in specials:
        print(f"  [DRY] run_{s}()")
        total += 1
    print(f"  [DRY] find_deals()")
    print(f"  [DRY] sweep_sold()")
    print(f"  [DRY] clean_city_data()")
    print(f"  [DRY] log_health_pulse()")
    total += 4

    print()
    print(f"Total activities this run: {total}")
    print("=" * 60)
    print("DRY RUN COMPLETE")
    print("=" * 60)
    return 0


def dry_run_heal():
    """Simulate an auto-heal cycle without connecting to Temporal."""
    print("=" * 60)
    print("DRY RUN — AutoHealWorkflow (simulated)")
    print("=" * 60)
    os.environ["OTEL_BATCH_ID"] = "dry-run-heal"
    print("batch_id=dry-run-heal")
    print()
    print("[DRY] query_loki('{job=\"realtor\"} | level=\"ERROR\"')")
    print("[DRY] → (simulated: no errors — healthy)")
    print("[DRY] log_health_pulse()")
    print()
    print("Result: HEALTH_OK (no errors found)")
    print("=" * 60)
    print("DRY RUN COMPLETE")
    print("=" * 60)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Temporal worker for realtor pipeline")
    parser.add_argument("--queue", choices=["scrape", "heal", "both"], default="both",
                        help="Which task queue(s) to listen on (default: both)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without connecting to Temporal")
    parser.add_argument("--day", default="mon",
                        help="Day label for dry-run (default: mon)")
    args = parser.parse_args()

    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    if args.dry_run:
        if args.queue in ("scrape", "both"):
            return dry_run_scrape()
        else:
            return dry_run_heal()

    async def _run():
        client = await connect()

        tasks = []
        if args.queue in ("scrape", "both"):
            tasks.append(asyncio.create_task(
                run_worker(client, SCRAPE_QUEUE, [DailyScrapeWorkflow], SCRAPE_ACTIVITIES)
            ))
        if args.queue in ("heal", "both"):
            tasks.append(asyncio.create_task(
                run_worker(client, HEAL_QUEUE, [AutoHealWorkflow], HEAL_ACTIVITIES)
            ))

        print(f"Workers running on {len(tasks)} queue(s). Press Ctrl+C to stop.")
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("\nShutting down...")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
