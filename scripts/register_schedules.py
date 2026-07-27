"""Register Temporal cron schedules for the realtor pipeline.

Usage:
    python -m scripts.register_schedules [--dry-run]

Registers:
  - daily-scrape-{mon..fri}: DailyScrapeWorkflow at 8 AM on each weekday
  - auto-heal: AutoHealWorkflow every 15 minutes

Run --dry-run first to see what would be registered without creating anything."""

import argparse
import asyncio
import json
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from temporalio.client import Client, Schedule, ScheduleSpec, SchedulePolicy, ScheduleActionStartWorkflow, ScheduleOverlapPolicy
from temporalio import workflow

from app.workflows.workflows import DailyScrapeWorkflow, AutoHealWorkflow

TEMPORAL_HOST = "localhost:7233"
NAMESPACE = "default"
SCRAPE_QUEUE = "realtor-scrape-queue"
HEAL_QUEUE = "realtor-heal-queue"

SCHEDULES = []

WEEKDAY_CRONS = {"mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5"}
for day, dow in WEEKDAY_CRONS.items():
    SCHEDULES.append({
        "id": f"daily-scrape-{day}",
        "workflow": DailyScrapeWorkflow.run,
        "args": [day],
        "task_queue": SCRAPE_QUEUE,
        "cron": f"0 8 * * {dow}",
        "description": f"Daily scrape for {day}",
    })

SCHEDULES.append({
    "id": "auto-heal",
    "workflow": AutoHealWorkflow.run,
    "args": [],
    "task_queue": HEAL_QUEUE,
    "cron": "*/15 * * * *",
    "description": "Auto-heal watchdog every 15 minutes",
})


async def list_existing(client):
    """List existing schedules."""
    existing = []
    schedules = await client.list_schedules()
    async for schedule in schedules:
        existing.append(schedule.id)
    return existing


async def register():
    parser = argparse.ArgumentParser(description="Register Temporal cron schedules")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be registered")
    parser.add_argument("--clean", action="store_true", help="Delete existing schedules before registering")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — would register the following schedules:")
        print()
        for s in SCHEDULES:
            print(f"  Schedule: {s['id']}")
            print(f"    Workflow: {s['workflow'].__name__}")
            print(f"    Args: {s['args']}")
            print(f"    Queue: {s['task_queue']}")
            print(f"    Cron: {s['cron']}")
            print()
        return 0

    client = await Client.connect(TEMPORAL_HOST, namespace=NAMESPACE)
    print(f"Connected to Temporal at {TEMPORAL_HOST}")

    existing = await list_existing(client)
    print(f"Existing schedules: {existing}")

    if args.clean:
        for sched_id in existing:
            print(f"  Deleting schedule: {sched_id}")
            try:
                handle = client.get_schedule_handle(sched_id)
                await handle.delete()
            except Exception as e:
                print(f"  Failed to delete {sched_id}: {e}")

    registered = 0
    for s in SCHEDULES:
        if s["id"] in existing and not args.clean:
            print(f"  Schedule '{s['id']}' already exists — skipping (use --clean to re-create)")
            continue

        try:
            await client.create_schedule(
                s["id"],
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        s["workflow"],
                        args=s["args"],
                        id=s["id"],
                        task_queue=s["task_queue"],
                    ),
                    spec=ScheduleSpec(
                        cron_expressions=[s["cron"]],
                    ),
                    policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                ),
            )
            print(f"  Registered: {s['id']} ({s['description']})")
            registered += 1
        except Exception as e:
            print(f"  Failed to register {s['id']}: {e}")

    print(f"\nDone. {registered} schedule(s) registered.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(register()))
