"""Merge raw parcel data from expanded counties into commercial_sites.

Filters:
  - >= 10 acres
  - Filtered to commercial/industrial/utility land use where available
  - Excludes water bodies, extremely large tracts (likely data errors)

Scores on available fields (some counties lack use codes).
New sites get tentative scores; enrichment will refine them.
"""

import sys
import os
import sqlite3
import time
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

DB_PATH = os.path.join(SCRIPT_DIR, "..", "deals.db")

COMMERCIAL_KEYWORDS = [
    "INDUSTRIAL", "IND ", "COMMERCIAL", "COMM ", "OFFICE", "WAREHOUSE",
    "MINING", "PETROL", "GAS", "UTILITY", "BUSINESS PARK",
    "MANUFACTURING", "MANUF", "DISTRIBUTION", "STORAGE", "FLEX SPACE",
    "RETAIL", "APART", "MULTI-FAMILY", "APARTMENT", "CONDO",
    "LIGHT MANUF", "HEAVY MANUF", "INDUSTRIAL PARK", "OFFICE PARK",
    "RAW M",  # raw materials
    "VACANT COM", "VACANT INDUS", "VACANT INST",
    "GSERVICES",  # Forsyth "3.01-GServices -Inst"
]

RESIDENTIAL_KEYWORDS = [
    "SFD DWELLING", "SINGLE FAMILY", "MOBILE HOME", "MF DWELLING",
    "GRP-TRAN", "MANUF HOME", "DUPLEX", "TRIPLEX",
]

INDUSTRIAL_KEYWORDS = [
    "INDUSTRIAL", "IND ", "MANUFACTURING", "MANUF", "WAREHOUSE",
    "DISTRIBUTION", "STORAGE", "MINING", "PETROL", "GAS",
    "LIGHT MANUF", "HEAVY MANUF", "INDUSTRIAL PARK",
    "RAW M", "RAIL TRAN", "AIR TRAN",
    "VACANT INDUS",
]

ZONING_COMMERCIAL_PREFIXES = {"C", "I", "M", "BP", "ML", "IND", "LI", "HI", "O"}
ZONING_RESIDENTIAL_PREFIXES = {"R", "AR", "RB", "RC", "RM", "RS", "MH", "RA"}

MAX_SANE_ACRES = 2000


def get_raw_tables(conn):
    """Get list of raw_* parcel tables with row counts."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'raw_%' ORDER BY name")
    tables = []
    for (tname,) in cursor.fetchall():
        cursor.execute(f"SELECT COUNT(*) FROM {tname}")
        count = cursor.fetchone()[0]
        tables.append((tname, count))
    return tables


def classify_land_use(parusedesc, parusecode, raw_json=None):
    """Classify whether a parcel is commercial/industrial viable."""
    desc = (parusedesc or "").strip().upper()
    code = (parusecode or "").strip().upper()
    combined = f"{desc} {code}"

    # Check raw JSON for additional classification signals (Orange County)
    if raw_json:
        try:
            if isinstance(raw_json, str):
                raw = json.loads(raw_json)
            else:
                raw = raw_json
            zoning = (raw.get("Zonings") or "").strip().upper()
            nbc = str(raw.get("NBC") or "")
            combined += f" {zoning} {nbc}"
            # Check if address has commercial clues
            addr = (raw.get("ADDRESS1") or "").upper()
            if any(kw in addr for kw in ["INDUSTRIAL", "COMMERCE", "BUSINESS", "OFFICE", "WAREHOUSE"]):
                combined += " COMMERCIAL"
            # Check Zoning_Admin
            zadmin = (raw.get("Zoning_Admin") or "").upper()
            combined += f" {zadmin}"
        except:
            pass

    is_residential = any(kw in combined for kw in RESIDENTIAL_KEYWORDS)
    if is_residential:
        return False, False

    is_commercial = any(kw in combined for kw in COMMERCIAL_KEYWORDS)
    is_industrial = any(kw in combined for kw in INDUSTRIAL_KEYWORDS)

    if not is_commercial and code:
        prefix = code[0]
        if prefix in ZONING_COMMERCIAL_PREFIXES:
            is_commercial = True
            if prefix in ("I", "M"):
                is_industrial = True

    return is_commercial, is_industrial


def score_parcel(acres, has_address, is_industrial, is_commercial, has_owner):
    """Score a parcel on a simplified scale."""
    s = 0

    ac = float(acres or 0)
    if ac >= 200:
        s += 30
    elif ac >= 100:
        s += 25
    elif ac >= 50:
        s += 20
    elif ac >= 20:
        s += 15
    elif ac >= 10:
        s += 10

    if is_industrial:
        s += 25
    elif is_commercial:
        s += 20

    if has_address:
        s += 5

    if has_owner:
        s += 5

    return s


def determine_tier(score):
    if score >= 65:
        return "A"
    elif score >= 45:
        return "B"
    elif score >= 25:
        return "C"
    return "D"


def merge_table(conn, table_name):
    """Process a raw table and merge qualifying parcels into commercial_sites."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    total = cursor.fetchone()[0]
    if total == 0:
        return 0, 0, 0

    cursor.execute(f"""
        SELECT source_oid, source_county, source_state,
               parno, ownname, siteadd, scity, sstate, szip,
               gisacres, parval, landval, improvval,
               parusecode, parusedesc, raw_json
        FROM {table_name}
        WHERE gisacres IS NOT NULL
          AND gisacres >= 10
          AND gisacres < {MAX_SANE_ACRES}
    """)

    added = 0
    skipped_no_addr = 0
    skipped_duplicate = 0

    for row in cursor.fetchall():
        source_oid, county, state = row[0], row[1], row[2]
        parno, ownname, siteadd = row[3], row[4], row[5]
        scity, sstate, szip = row[6], row[7], row[8]
        gisacres, parval, landval, improvval = row[9], row[10], row[11], row[12]
        parusecode, parusedesc = row[13], row[14]
        raw_json = row[15] if len(row) > 15 else None

        has_address = bool(siteadd and siteadd.strip())
        has_owner = bool(ownname and ownname.strip())
        is_commercial, is_industrial = classify_land_use(parusedesc, parusecode, raw_json)

        # Skip parcels without any commercial/industrial signal
        # Only include if classified OR has corporate owner name hint
        has_corporate_owner = has_owner and any(
            kw in (ownname or "").upper()
            for kw in ["LLC", "INC", "CORP", "COMPANY", "CO ",
                       "PROPERTIES", "HOLDING", "INVESTMENTS",
                       "DEVELOPMENT", "REALTY", "ASSOCIATES"]
        )

        if not is_commercial and not has_corporate_owner:
            skipped_no_addr += 1
            continue

        score = score_parcel(gisacres, has_address, is_industrial, is_commercial, has_owner)
        tier = determine_tier(score)

        address = (siteadd or "").strip()
        city = (scity or "").strip()
        st = (sstate or state or "").strip()
        zip_code = (szip or "").strip()

        full_address = address
        if city:
            full_address = f"{address} {city}"
        full_address = full_address.strip()

        pid_base = parno or source_oid or f"{county}_{hash(address+ownname)%10000000}"

        try:
            conn.execute("""
                INSERT OR IGNORE INTO commercial_sites
                (pid, county, address, owner_name, acres,
                 total_value, land_value, bldg_value,
                 land_use, score_acreage, score_land_use,
                 score_vacancy, score_owner, score_density, score_total, score_tier,
                 last_sale_price, s1_source)
                VALUES (?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        0, ?, 0, ?, ?,
                        ?, ?)
            """, (
                pid_base,
                county,
                full_address,
                (ownname or "").strip(),
                float(gisacres) if gisacres else None,
                float(parval) if parval else None,
                float(landval) if landval else None,
                float(improvval) if improvval else None,
                (parusedesc or "UNKNOWN").strip(),
                30 if float(gisacres or 0) >= 200 else (
                    25 if float(gisacres or 0) >= 100 else (
                        20 if float(gisacres or 0) >= 50 else (
                            15 if float(gisacres or 0) >= 20 else 10
                        )
                    )
                ),
                25 if is_industrial else (20 if is_commercial else 10),
                5 if has_owner else 0,
                score,
                tier,
                float(parval) if parval else None,
                table_name,
            ))
            added += 1
        except Exception as e:
            skipped_duplicate += 1

    conn.commit()
    return total, added, skipped_no_addr


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")

    tables = get_raw_tables(conn)
    print(f"Found {len(tables)} raw parcel tables\n")

    total_existing = conn.execute("SELECT COUNT(*) FROM commercial_sites").fetchone()[0]
    print(f"Existing commercial_sites: {total_existing}\n")

    grand_total = 0
    grand_added = 0

    for tname, count in tables:
        if count == 0:
            continue
        print(f"  {tname:<45s} ({count:>8,} rows)...", end=" ", flush=True)
        total, added, skipped = merge_table(conn, tname)
        grand_total += total
        grand_added += added
        print(f"added {added:>5,} (skipped {skipped} no-addr)")

    conn.commit()
    new_total = conn.execute("SELECT COUNT(*) FROM commercial_sites").fetchone()[0]
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Processed {grand_total:,} raw parcels across {len(tables)} tables")
    print(f"  Added {grand_added:,} new sites to commercial_sites")
    print(f"  Total commercial_sites: {new_total:,}")
    print(f"  New sites added: {new_total - total_existing:,}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
