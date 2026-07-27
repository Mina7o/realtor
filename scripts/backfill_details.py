"""Backfill property_details from existing Zillow JSON files."""
import json
import os
import sys
from db import get_conn, upsert_property, set_property_details
from db import get_conn as _get_conn

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")


def backfill_from_json(filepath):
    with open(filepath) as f:
        listings = json.load(f)
    loaded = 0
    skipped = 0
    for l in listings:
        addr = l.get("address_street") or l.get("address")
        city = l.get("city")
        state = l.get("state") or "NC"
        zip_code = l.get("zip")
        if not addr or not city:
            skipped += 1
            continue
        pid = upsert_property(addr, city, state, zip_code, None, None, None)
        set_property_details(pid,
            bedrooms=l.get("beds"),
            bathrooms=l.get("baths"),
            sqft=l.get("sqft"),
        )
        loaded += 1
    return loaded, skipped


def main():
    print("Backfilling property_details from Zillow JSON files...")
    total_loaded = 0
    total_skipped = 0
    if not os.path.isdir(OUTPUT_DIR):
        print(f"No output directory found at {OUTPUT_DIR}")
        sys.exit(1)
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        if not fname.startswith("zillow") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(OUTPUT_DIR, fname)
        loaded, skipped = backfill_from_json(fpath)
        total_loaded += loaded
        total_skipped += skipped
        print(f"  {fname}: {loaded} loaded, {skipped} skipped")
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM property_details").fetchone()[0]
    conn.close()
    print(f"\nDone. property_details now has {count} rows ({total_loaded} from backfill)")


if __name__ == "__main__":
    main()
