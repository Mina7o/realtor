"""Normalize city names in property & commercial tables."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from logger_setup import setup_logging
from loguru import logger

setup_logging("clean_city_data")

import argparse, sqlite3
from db import get_conn


def normalize_city(raw):
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r',\s*(NC|SC|TX|GA|VA|TN)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    cleaned = cleaned.title()

    FIXUPS = {
        "Rale": "Raleigh",
    }
    cleaned = FIXUPS.get(cleaned, cleaned)

    return cleaned


def scan_and_fix(conn, dry_run=False):
    changes = []

    tables = [
        ("properties", "city"),
        ("commercial_sites", "owner_city"),
    ]

    for table, column in tables:
        rows = conn.execute(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
        for r in rows:
            raw = r[column]
            fixed = normalize_city(raw)
            if fixed != raw:
                changes.append((table, r["id"], raw, fixed))

    if not changes:
        print("No dirty city names found.")
        return changes

    print(f"\nFound {len(changes)} records with dirty city names:\n")
    for table, rid, raw, fixed in changes:
        print(f"  {table:15s} id={rid:>6}  '{raw}'  →  '{fixed}'")

    if not dry_run:
        conn.execute("BEGIN TRANSACTION")
        for table, rid, raw, fixed in changes:
            col = "city" if table == "properties" else "owner_city"
            conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (fixed, rid))
        conn.commit()
        print(f"\nFixed {len(changes)} records.")
    else:
        print(f"\nDRY RUN — {len(changes)} records would be fixed.")

    return changes


def main():
    parser = argparse.ArgumentParser(description="Normalize city names across the database")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    conn = get_conn()
    try:
        scan_and_fix(conn, dry_run=args.dry_run)

        if not args.dry_run:
            distinct = conn.execute("""
                SELECT DISTINCT city FROM properties WHERE city IS NOT NULL ORDER BY city
            """).fetchall()
            print(f"\nDistinct cities after cleanup ({len(distinct)}):")
            for r in distinct:
                print(f"  {r[0]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
