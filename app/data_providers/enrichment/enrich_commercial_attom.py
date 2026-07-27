"""Enrich commercial_sites with ATTOM property/expandedprofile (zoning, landUse).
Adds attom_zoning_code, attom_land_use columns to commercial_sites.

Usage:
  python3 enrich_commercial_attom.py --limit 50
  python3 enrich_commercial_attom.py --limit 50 --apply
  python3 enrich_commercial_attom.py --apply
  python3 enrich_commercial_attom.py --apply --county union
  python3 enrich_commercial_attom.py --apply --county chatham
"""
import argparse, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests
from otel_utils import init_otel

API_KEY = os.getenv("ATTOM_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ATTOM_API_KEY environment variable not set")
BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
HEADERS = {"Accept": "application/json", "APIKey": API_KEY}
DELAY = 0.35
DB = Path.home() / "Documents/proj/realtor/deals.db"


def get_conn():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


def ensure_columns(conn):
    existing = [c[1] for c in conn.execute("PRAGMA table_info(commercial_sites)").fetchall()]
    for col in ("attom_zoning_code", "attom_land_use_code", "attom_land_use_desc", "attom_last_sale_price", "attom_last_sale_date"):
        if col not in existing:
            conn.execute(f"ALTER TABLE commercial_sites ADD COLUMN {col} TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attom_commercial_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            zoning_code TEXT,
            zoning_description TEXT,
            land_use_code TEXT,
            land_use_description TEXT,
            last_sale_price REAL,
            last_sale_date TEXT,
            last_sale_grantor TEXT,
            assessed_value REAL,
            market_value REAL,
            avm_value REAL,
            owner_name TEXT,
            corporate_indicator TEXT,
            absentee_status TEXT,
            lot_acres REAL,
            year_built INTEGER,
            sqft INTEGER,
            raw_json TEXT,
            fetched_at TEXT,
            UNIQUE(site_id)
        )
    """)
    conn.commit()


def call_expandedprofile(address1, address2):
    url = f"{BASE}/property/expandedprofile"
    params = {"address1": address1, "address2": address2}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except requests.exceptions.RequestException:
        pass
    return None


def clean_address(raw_addr):
    cleaned = re.sub(r'\s+(?:APT|UNIT|SUITE|#|LOT|TRACT|BLDG)\s*\w*\s*$', '', raw_addr.strip(), flags=re.I)
    cleaned = re.sub(r'\s+[A-Z]\s*$', '', cleaned)
    return cleaned


def main():
    tracer = init_otel("enrich_commercial_attom")
    conn = sqlite3.connect(str(DB_PATH))
    parser = argparse.ArgumentParser(description="Enrich commercial sites via ATTOM")
    parser.add_argument("--county", help="Focus on a specific county")
    parser.add_argument("--limit", type=int, default=100, help="Max properties")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be enriched")
    parser.add_argument("--skip-zoning", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    ensure_columns(conn)

    clauses = ["(acres IS NOT NULL AND CAST(acres AS REAL) > 0)",
                "(address IS NOT NULL AND address != '')",
                "(attom_zoning_code IS NULL OR attom_zoning_code = '')"]
    params = {}
    if args.county:
        clauses.append("LOWER(county) = :county")
        params['county'] = args.county.lower()

    where = " AND ".join(clauses)

    sites = conn.execute(f"""
        SELECT id, address, county, owner_city, owner_state
        FROM commercial_sites
        WHERE {where}
        ORDER BY CAST(acres AS REAL) DESC, id
    """, params).fetchall()

    if args.limit:
        sites = sites[:args.limit]

    if not sites:
        print("No sites to enrich")
        return

    print(f"Enriching {len(sites)} commercial sites via ATTOM expandedprofile...")
    success = 0
    skipped = 0
    start = time.time()

    for i, site in enumerate(sites):
        sid = site['id']
        raw_addr = clean_address(site['address'] or '')
        county = site['county'] or ''
        owner_state = site['owner_state'] or 'NC'
        if not raw_addr:
            skipped += 1
            continue

        # Build address2: city, state — use owner_city or county as fallback
        city_str = site['owner_city'] or county
        addr2 = f"{city_str}, {owner_state}"

        result = call_expandedprofile(raw_addr, addr2)
        api_ok = result and result.get("status", {}).get("code") == 0

        if not api_ok:
            result2 = call_expandedprofile(raw_addr, owner_state)
            api_ok = result2 and result2.get("status", {}).get("code") == 0
            if api_ok:
                result = result2

        if not api_ok:
            print(f"  [{i+1}/{len(sites)}] NOT FOUND id={sid}: {raw_addr[:40]:40s}")
            skipped += 1
            time.sleep(DELAY)
            continue

        prop = result["property"][0]
        zoning = prop.get("zoning", {}) or {}
        land_use = prop.get("landUse", {}) or {}
        assessment = prop.get("assessment", {}) or {}
        avm = prop.get("avm", {}) or {}
        owner = prop.get("owner", {}) or {}
        lot = prop.get("lot", {}) or {}
        summary = prop.get("summary", {}) or {}

        zoning_code = (zoning.get("zoningCode") or "").strip()
        zoning_desc = (zoning.get("zoningDescription") or "").strip()
        lu_code = (land_use.get("landUseCode") or "").strip()
        lu_desc = (land_use.get("landUseDescription") or "").strip()
        last_sale_price = summary.get("lastsaleprice")
        last_sale_date = summary.get("lastsaledate")
        last_sale_grantor = summary.get("lastsalegrantor")
        assessed_val = assessment.get("assessed", {}).get("assdttlvalue")
        market_val = assessment.get("market", {}).get("mktttlvalue")
        avm_amt = avm.get("amount", {}) if avm else {}
        avm_value = avm_amt.get("value") if avm_amt else None
        o1 = owner.get("owner1", {}) or {}
        owner_name = o1.get("fullname")
        corporate_ind = owner.get("corporateindicator")
        absentee_status = owner.get("absenteeownerstatus")
        lot_acres = lot.get("lotsize1")
        year_built = summary.get("yearbuilt")

        if not zoning_code and not lu_code:
            print(f"  [{i+1}/{len(sites)}] NO ZONING id={sid}: {raw_addr[:40]:40s}")
            skipped += 1
            time.sleep(DELAY)
            continue

        if args.apply:
            conn.execute("""
                UPDATE commercial_sites
                SET attom_zoning_code = ?,
                    attom_land_use_code = ?,
                    attom_land_use_desc = ?,
                    attom_last_sale_price = ?,
                    attom_last_sale_date = ?
                WHERE id = ?
            """, (zoning_code, lu_code, lu_desc, last_sale_price, last_sale_date, sid))

            conn.execute("""
                INSERT OR REPLACE INTO attom_commercial_cache
                    (site_id, zoning_code, zoning_description,
                     land_use_code, land_use_description,
                     last_sale_price, last_sale_date, last_sale_grantor,
                     assessed_value, market_value, avm_value,
                     owner_name, corporate_indicator, absentee_status,
                     lot_acres, year_built, sqft, raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sid, zoning_code, zoning_desc,
                lu_code, lu_desc,
                last_sale_price, last_sale_date, last_sale_grantor,
                assessed_val, market_val, avm_value,
                owner_name, corporate_ind, absentee_status,
                lot_acres, year_built, None,
                json.dumps(prop, default=str)[:10000],
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()

        status = f"zone={zoning_code or '—'}, lu={lu_code or '—'}"
        print(f"  [{i+1}/{len(sites)}] id={sid:5d} {raw_addr[:35]:35s} | {status}")
        success += 1
        time.sleep(DELAY)

    elapsed = time.time() - start
    print(f"\nDone: {success} enriched, {skipped} skipped in {elapsed:.0f}s")

    if args.apply:
        zoned = conn.execute(
            "SELECT COUNT(*) FROM commercial_sites WHERE attom_zoning_code IS NOT NULL AND attom_zoning_CODE != ''"
        ).fetchone()[0]
        no_zone = conn.execute(
            "SELECT COUNT(*) FROM commercial_sites"
        ).fetchone()[0] - zoned
        print(f"Total commercial_sites with zoning: {zoned}, without: {no_zone}")

    conn.close()


if __name__ == "__main__":
    main()
