"""
Directive 29: Credit Bridge Fallback — Estated API
100 free credits for property enrichment (zoning, owner, land use).

Usage:
  python enrich_with_estated.py --api-key KEY [--addresses FILE]
  python enrich_with_estated.py --api-key KEY --county Rowan --limit 10
"""

import json
import sqlite3
import time
import argparse
from pathlib import Path
from urllib.request import urlopen, Request
from otel_utils import init_otel

DB_PATH = Path(__file__).parent / "deals.db"
ESTATED_BASE = "https://apis.estated.com/v1/property"
FREE_CREDITS = 100
CALLS = 0


def estated_lookup(api_key, address, city, state, zip_code=""):
    global CALLS
    if CALLS >= FREE_CREDITS:
        print("  Quota exhausted (100 credits)")
        return None
    
    params = (
        f"token={api_key}"
        f"&combined_address={address}, {city}, {state} {zip_code}"
    )
    url = f"{ESTATED_BASE}?{params}"
    
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            CALLS += 1
            return json.loads(resp.read())
    except Exception as e:
        print(f"    Estated error: {e}")
        return None


def extract_estated(data):
    result = {}
    d = data.get("data", data.get("result", {}))
    if not d or d.get("status") != "success":
        return result
    
    p = d.get("property", {})
    result["owner_name"] = (p.get("owner_name") or "").strip()
    result["zoning"] = (p.get("zoning") or "").strip()
    result["land_use"] = (p.get("land_use") or "").strip()
    result["land_use_code"] = str(p.get("land_use_code") or "")
    
    val = p.get("valuation", {})
    result["total_value"] = val.get("market")
    result["land_value"] = val.get("land")
    result["bldg_value"] = val.get("improvements")
    
    result["acres"] = p.get("lot_size_acres") or p.get("acres")
    
    bldg = p.get("building", {})
    if bldg:
        result["year_built"] = bldg.get("year_built")
        result["sqft"] = bldg.get("total_square_footage")
    
    sale = p.get("last_sale", {})
    if sale:
        result["last_sale_price"] = sale.get("price")
        result["last_sale_date"] = sale.get("date")
    
    return {k: v for k, v in result.items() if v is not None and v != ""}


def main():
    tracer = init_otel("enrich_estated")
    parser = argparse.ArgumentParser(description="Enrich properties via Estated API")
    parser.add_argument("--api-key", required=True, help="Estated API key")
    parser.add_argument("--county", help="Target county (enrich missing data)")
    parser.add_argument("--limit", type=int, default=50, help="Max lookups")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", help="Save results as JSON")
    args = parser.parse_args()
    
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, address, owner_city AS city, owner_state AS state, owner_name "
        "FROM commercial_sites WHERE county=? "
        "AND (owner_name IS NULL OR owner_name='' OR owner_name='—') "
        "LIMIT ?", (args.county, args.limit)
    ).fetchall() if args.county else []
    
    if not rows:
        print(f"No sites needing enrichment in {args.county or 'any county'}")
        conn.close()
        return
    
    print(f"Enriching {len(rows)} sites via Estated API ({FREE_CREDITS} credits)")
    results = []
    
    for i, (sid, addr, city, state, owner) in enumerate(rows):
        if CALLS >= FREE_CREDITS:
            break
        
        print(f"  [{i+1}/{len(rows)}] {addr or 'no addr'}...", end=" ")
        data = estated_lookup(args.api_key, addr, city, state)
        
        if not data:
            print("no data")
            continue
        
        extracted = extract_estated(data)
        if extracted:
            print(f"→ owner='{extracted.get('owner_name','')[:30]}' zoning='{extracted.get('zoning','')[:15]}'")
            results.append({"site_id": sid, **extracted})
            
            if not args.dry_run:
                updates = {}
                if extracted.get("owner_name"):
                    updates["owner_name"] = extracted["owner_name"]
                if extracted.get("zoning"):
                    updates["zoning"] = extracted["zoning"]
                if extracted.get("land_use"):
                    updates["land_use"] = extracted["land_use"]
                if extracted.get("total_value"):
                    updates["total_value"] = str(extracted["total_value"])
                
                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    vals = list(updates.values()) + [sid]
                    conn.execute(f"UPDATE commercial_sites SET {set_clause} WHERE id=?", vals)
                    conn.commit()
        else:
            print("no match")
        
        time.sleep(0.5)
    
    conn.close()
    print(f"\nEnriched {len(results)} sites ({CALLS} API calls used)")
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
