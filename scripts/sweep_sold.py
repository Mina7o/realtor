"""Sweep stale listings as REMOVED."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from logger_setup import setup_logging
from loguru import logger

setup_logging("sweep_sold")

import argparse, datetime, sqlite3
from db import get_conn

STALE_DAYS = {
    "zillow": 21,
    "fsbo": 30,
    "realtor.com": 21,
    "sellbyowner": 21,
    "landandfarm": 60,
}


def sweep(dry_run=False):
    conn = get_conn()
    try:
        total_marked = 0
        total_deleted_price = 0

        for source, days in STALE_DAYS.items():
            stale = conn.execute("""
                SELECT l.id, l.property_id, l.list_price, p.address, p.city, l.last_seen_at
                FROM listings l
                JOIN properties p ON l.property_id = p.id
                WHERE l.source = ?
                  AND l.last_seen_at IS NOT NULL
                  AND l.last_seen_at < datetime('now', ?)
                  AND (l.listing_status IS NULL OR l.listing_status NOT IN ('REMOVED','SOLD'))
            """, (source, f"-{days} days")).fetchall()

            if not stale:
                continue

            print(f"\n{source}: {len(stale)} stale listings (>={days}d)")
            for r in stale[:5]:
                print(f"  {r['address']:35s} {r['city']:20s} ${r['list_price']:>9,}  last_seen={r['last_seen_at']}")
            if len(stale) > 5:
                print(f"  ... and {len(stale) - 5} more")

            if not dry_run:
                ids = [r['id'] for r in stale]
                conn.execute(
                    "UPDATE listings SET listing_status='REMOVED', status_text='Not seen since ' || last_seen_at WHERE id IN ({})".format(
                        ','.join('?' * len(ids))
                    ), ids
                )
            total_marked += len(stale)

        # Age out stale price history (>90d)
        old_ph = conn.execute(
            "DELETE FROM price_history WHERE detected_at < datetime('now', '-90 days')"
        )
        total_deleted_price = old_ph.rowcount

        conn.commit()

        # Show totals
        remaining = conn.execute("""
            SELECT source, COUNT(*) FROM listings
            WHERE listing_status IS NULL OR listing_status NOT IN ('REMOVED','SOLD')
            GROUP BY source ORDER BY source
        """).fetchall()
        print(f"\n{'=' * 50}")
        if dry_run:
            print(f"DRY RUN — {total_marked} would be marked REMOVED")
        else:
            print(f"Marked {total_marked} listings as REMOVED, cleaned {total_deleted_price} old price history rows")
        print("Active remaining:")
        for r in remaining:
            print(f"  {r['source']:15s} {r[1]}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sweep stale listings as REMOVED")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()
    sweep(dry_run=args.dry_run)
