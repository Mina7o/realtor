"""Bulk enrich listings with ATTOM API (assessed value, AVM, owner info).
Calls attomavm/detail for each listing property, stores in both tax_records and attom_cache.

Usage:
  python3 enrich_with_attom.py --limit 50       # dry run, show first 50
  python3 enrich_with_attom.py --limit 50 --apply   # actually save
  python3 enrich_with_attom.py --apply              # do all
"""
import argparse
import json
import time
import sys

import requests
from db import get_conn, upsert_tax_record

API_KEY = "eea3c86f1076e71fb9eabfecee0a8bf8"
BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
HEADERS = {"Accept": "application/json", "APIKey": API_KEY}

DELAY = 0.3


def enrich(conn, limit=None, apply=False):
    props = conn.execute("""
        SELECT DISTINCT p.id, p.address, p.city, p.state, p.zip
        FROM properties p
        JOIN listings l ON p.id = l.property_id
        WHERE NOT EXISTS (
            SELECT 1 FROM attom_cache a WHERE a.property_id = p.id
        )
        ORDER BY p.id
    """).fetchall()

    if limit:
        props = props[:limit]

    if not props:
        print("No properties to enrich.")
        return 0

    print(f"Enriching {len(props)} properties via ATTOM API...")
    success = 0
    skipped = 0
    errors = 0
    start = time.time()

    for i, pp in enumerate(props):
        pid = pp['id']
        raw_addr = (pp['address'] or '').split(',')[0].strip()
        addr2 = f"{pp['city']}, {pp['state']} {pp['zip']}".strip()

        # Try progressively cleaner addresses
        import re as _re
        candidates = [raw_addr]
        # Remove trailing unit/apt/suite
        cleaned = _re.sub(r'\s+(?:APT|UNIT|SUITE|#)\s*\w+\s*$', '', raw_addr, flags=_re.I)
        if cleaned != raw_addr:
            candidates.append(cleaned)
        # Remove single trailing letter (building/phase designation)
        cleaned2 = _re.sub(r'\s+[A-Z]\s*$', '', candidates[-1])
        if cleaned2 != candidates[-1]:
            candidates.append(cleaned2)

        result = None
        for addr1 in candidates:
            try:
                resp = requests.get(
                    f"{BASE}/attomavm/detail",
                    params={"address1": addr1, "address2": addr2},
                    headers=HEADERS,
                    timeout=15
                )
            except Exception as e:
                print(f"  [{i+1}/{len(props)}] ERROR id={pid}: {e}")
                continue

            if resp.status_code == 200:
                d = resp.json()
                if d['status']['code'] == 0:
                    result = d
                    break
            time.sleep(0.1)

        if result is None:
            print(f"  [{i+1}/{len(props)}] NOT FOUND id={pid}: {raw_addr[:30]:30s} (tried {len(candidates)} formats)")
            skipped += 1
            time.sleep(DELAY)
            continue

        d = result

        prop = d['property'][0]
        attom_id = prop['identifier']['attomId']
        summary = prop.get('summary', {})
        building = prop.get('building', {})
        owner = prop.get('owner', {})
        assessment = prop.get('assessment', {})
        avm = prop.get('avm', {}).get('amount', {})
        lot = prop.get('lot', {})

        o1 = owner.get('owner1', {}) or {}
        rooms = building.get('rooms', {}) or {}
        size = building.get('size', {}) or {}

        assessed_val = assessment.get('assessed', {}).get('assdttlvalue')
        market_val = assessment.get('market', {}).get('mktttlvalue')
        year_built = summary.get('yearbuilt')
        beds = rooms.get('beds')
        baths = rooms.get('bathstotal')
        sqft = size.get('universalsize') or size.get('livingsize')
        lot_acres = lot.get('lotsize1')
        prop_type = summary.get('propertyType')
        absentee = summary.get('absenteeInd')
        corporate = owner.get('corporateindicator')
        owner_name = o1.get('fullname')

        if apply:
            if assessed_val:
                upsert_tax_record(
                    property_id=pid,
                    tax_year=2025,
                    mkt_val_total=float(assessed_val) if assessed_val else None,
                    mkt_val_land=assessment.get('market', {}).get('mktlandvalue'),
                    year_built=int(year_built) if year_built else None,
                    sqft=float(sqft) if sqft else None,
                    bedrooms=int(beds) if beds else None,
                    bathrooms=float(baths) if baths else None,
                    lot_sqft=float(lot_acres) * 43560 if lot_acres else None,
                )

            conn.execute("""
                INSERT OR REPLACE INTO attom_cache
                (property_id, attom_id,
                 owner_name, absentee_status, corporate_indicator,
                 avm_value, avm_confidence,
                 assessed_value, market_value,
                 year_built, beds, baths, sqft, lot_acres,
                 property_type, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pid, attom_id,
                owner_name, absentee, corporate,
                avm.get('value'), avm.get('scr'),
                assessed_val, market_val,
                year_built, beds, baths, sqft, lot_acres,
                prop_type,
                json.dumps(prop, default=str)[:10000]
            ))
            conn.commit()

        status = f"${assessed_val or 'N/A':>8}"
        avm_v = f"${avm.get('value','N/A')}"
        absentee_str = absentee or ''
        print(f"  [{i+1}/{len(props)}] id={pid:5d} {raw_addr[:28]:28s} assessed={status} AVM={avm_v:>10} | {owner_name or '':20s} | {absentee_str[:15]}")
        success += 1
        time.sleep(DELAY)

    elapsed = time.time() - start
    print(f"\nDone: {success} enriched, {skipped} not found, {errors} errors in {elapsed:.0f}s")
    return success


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Max properties to enrich")
    parser.add_argument("--apply", action="store_true", help="Save results to DB")
    args = parser.parse_args()

    conn = get_conn()
    enrich(conn, limit=args.limit, apply=args.apply)
    conn.close()


if __name__ == "__main__":
    main()
