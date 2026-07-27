"""
Directive 29: Credit Bridge Fallback — HomeSage API
500 free credits for property enrichment.

Usage:
  python enrich_with_homesage.py --api-key KEY [--county Rowan] [--limit 50]
  python enrich_with_homesage.py --api-key KEY --addresses FILE

HomeSage endpoint: POST /v1/property/enrich
Returns: owner_name, zoning, land_use, assessed_value, last_sale
"""

import json
import sqlite3
import time
import argparse
from pathlib import Path
from urllib.request import urlopen, Request
from otel_utils import init_otel

DB_PATH = Path(__file__).parent / "deals.db"
FREECREDITS = 500
CALLS = 0


def enrich(api_key, address, city, state, zip_code=""):
    global CALLS
    if CALLS >= FREECREDITS:
        print("  Quota exhausted (500 credits)")
        return None

    payload = json.dumps({
        "address": address.strip() if address else "",
        "city": city.strip() if city else "",
        "state": state.strip() if state else "",
        "zip_code": zip_code.strip() if zip_code else "",
    }).encode()

    req = Request(
        "https://api.homesage.com/v1/property/enrich",
        data=payload,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urlopen(req, timeout=15) as resp:
            CALLS += 1
            return json.loads(resp.read())
    except Exception as e:
        return None


HOMESAGE_TARGETS = {
    "owner_name": ("owner", "ownerName", "owner_name"),
    "zoning": ("zoning", "zoningCode", "zoning_code"),
    "land_use": ("landUse", "landUseDescription", "land_use"),
    "land_use_code": ("landUseCode", "landUseStandardCode"),
    "total_value": ("assessedValue", "marketValue", "totalValue"),
    "land_value": ("landValue", "landAssessedValue"),
    "bldg_value": ("improvementValue", "buildingValue"),
    "acres": ("lotSizeAcres", "acres"),
    "last_sale_price": ("lastSalePrice", "salePrice"),
    "last_sale_date": ("lastSaleDate", "saleDate"),
    "year_built": ("yearBuilt", "buildingYearBuilt"),
    "sqft": ("squareFootage", "totalSquareFootage"),
}


def extract(data):
    """Recursively search response for known target keys."""
    result = {}

    def _search(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_lower = k.lower()
                for target_key, aliases in HOMESAGE_TARGETS.items():
                    if target_key in result:
                        continue
                    if any(a.lower() == k_lower for a in aliases):
                        if v is not None and str(v).strip():
                            result[target_key] = v
                _search(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for item in obj[:3]:
                _search(item, path)

    _search(data)
    return result


def main():
    tracer = init_otel("enrich_homesage")
    parser = argparse.ArgumentParser(description="Enrich via HomeSage API")
    parser.add_argument("--api-key", required=True, help="HomeSage API key")
    parser.add_argument("--county", help="Target county for enrichment")
    parser.add_argument("--limit", type=int, default=100, help="Max lookups")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, address, owner_city AS city, owner_state AS state, "
        "owner_name, zoning, land_use "
        "FROM commercial_sites WHERE county=? "
        "AND (zoning IS NULL OR zoning='' OR zoning='—') "
        "LIMIT ?", (args.county, args.limit)
    ).fetchall() if args.county else []

    if not rows:
        print(f"No sites needing zoning in {args.county}")
        conn.close()
        return

    print(f"Enriching {len(rows)} sites via HomeSage API (500 credits)")
    enriched = 0

    for i, (sid, addr, city, state, *_) in enumerate(rows):
        if CALLS >= FREECREDITS:
            break

        print(f"  [{i+1}/{len(rows)}] {addr or 'no addr'}...", end=" ")
        data = enrich(api_key=args.api_key, address=addr, city=city, state=state)

        if not data:
            print("no data")
            continue

        extracted = extract(data)
        if extracted:
            enriched += 1
            z = extracted.get("zoning", "")
            o = extracted.get("owner_name", "")
            print(f"→ zoning='{str(z)[:15]}' owner='{str(o)[:20]}'")

            if not args.dry_run:
                updates = {}
                if extracted.get("zoning"):
                    updates["zoning"] = str(extracted["zoning"])
                if extracted.get("owner_name"):
                    updates["owner_name"] = str(extracted["owner_name"])
                if extracted.get("land_use"):
                    updates["land_use"] = str(extracted["land_use"])
                if extracted.get("total_value"):
                    updates["total_value"] = str(extracted["total_value"])

                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    conn.execute(
                        f"UPDATE commercial_sites SET {set_clause} WHERE id=?",
                        list(updates.values()) + [sid],
                    )
                    conn.commit()
        else:
            print("no match")

        time.sleep(0.3)

    conn.close()
    print(f"\nEnriched {enriched} sites ({CALLS} API calls)")


if __name__ == "__main__":
    main()
