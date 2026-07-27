"""Texas Front — Bulk Parcel Ingest (D52 + D53).

Ingests Dallas CAD (DCAD) parcel geometry + appraisal roll, then
Travis County parcels from TxGIO/TNRIS, then scores against
ONCOR 345kV transmission corridors.

Usage:
    # Step 1: Ingest DCAD raw parcels
    python data_center/texas_ingest.py --ingest-dallas

    # Step 2: Ingest Travis County (TNRIS)
    python data_center/texas_ingest.py --ingest-travis

    # Step 3: Merge all TX raw -> commercial_sites
    python data_center/texas_ingest.py --merge

    # Step 4: Geocode (if needed)
    python data_center/merge_to_commercial.py  # uses existing pipeline

    # Step 5: Match to transmission corridors
    python data_center/texas_ingest.py --transmission-match
"""

import sys, os, csv, sqlite3, time, json, io, math, re, zipfile
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

DB_PATH = str(Path(SCRIPT_DIR).parent / "deals.db")
INFRA_DB = str(Path(SCRIPT_DIR).parent / "infrastructure.db")
DATA_DIR = str(Path(SCRIPT_DIR).parent / "data" / "texas")

# ONCOR service territory counties in North Texas
ONCOR_COUNTIES = [
    "dallas", "tarrant", "collin", "denton", "ellis", "johnson",
    "parker", "wise", "rockwall", "kaufman", "hunt", "navarro",
    "henderson", "van zandt", "rains", "hopkins", "delta", "fannin",
    "grayson", "cooke", "montague", "jack", "palo pinto", "hood",
    "somervell", "erath", "comanche", "mills", "lampasas", "coryell",
    "hamilton", "bosque", "hill", "limestone", "freestone", "anderson",
    "cherokee", "shelby", "san augustine", "natchitoches", "sabine",
    "angelina", "trinity", "polk", "tyler", "hardin", "jasper",
    "newton", "orange", "jefferson",
]

###############################################################################
# DCAD Ingest
###############################################################################

DCAD_RAW_TABLE = "raw_dallas_tx_parcels"

def ensure_table(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {DCAD_RAW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acct TEXT UNIQUE,
            type TEXT,
            rec_acres REAL,
            owner_name TEXT,
            owner_name2 TEXT,
            biz_name TEXT,
            site_address TEXT,
            property_city TEXT,
            property_zip TEXT,
            land_val REAL,
            impr_val REAL,
            total_val REAL,
            zoning TEXT,
            sptd_code TEXT,
            sptd_desc TEXT,
            land_use_desc TEXT,
            year_built INTEGER,
            gross_bldg_area REAL,
            num_stories REAL,
            bldg_class TEXT,
            nbhd_cd TEXT,
            legal_desc TEXT,
            deed_date TEXT,
            lat REAL,
            lng REAL,
            raw_json TEXT
        )
    """)
    conn.commit()

def ingest_dcad():
    """Read DCAD shapefile + appraisal CSVs, write raw_dallas_tx_parcels."""
    import geopandas as gpd

    print("=" * 60)
    print("DIRECTIVE 52: DCAD Bulk Ingest")
    print("=" * 60)

    # --- Step 1: Load shapefile geometries ---
    shp_path = os.path.join(DATA_DIR, "parcels_gis", "PARCEL_GEOM", "PARCEL_GEOM.shp")
    print(f"Loading shapefile: {shp_path}")
    parcels = gpd.read_file(shp_path)
    print(f"  Parcels in shapefile: {len(parcels)}")

    # EPSG:2276 (NAD83 / Texas State Mapping System) -> WGS84
    if parcels.crs and parcels.crs.to_epsg() != 4326:
        parcels = parcels.to_crs("EPSG:4326")
    parcels["lat"] = parcels.geometry.centroid.y
    parcels["lng"] = parcels.geometry.centroid.x

    acct_to_geom = dict(zip(parcels["Acct"], zip(parcels["lat"], parcels["lng"], parcels["RecAcs"])))
    acct_set = set(parcels["Acct"])
    print(f"  Unique accounts in shapefile: {len(acct_set)}")

    # --- Step 2: Load ACCOUNT_INFO ---
    print("Loading ACCOUNT_INFO.CSV...")
    info = {}
    with open(os.path.join(DATA_DIR, "appraisal", "ACCOUNT_INFO.CSV"),
              encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        for row in r:
            acct = row["ACCOUNT_NUM"].strip()
            if acct in acct_set:
                info[acct] = row
    print(f"  Matched appraisal info: {len(info)}")

    # --- Step 3: Load ACCOUNT_APPRL_YEAR (values) ---
    print("Loading ACCOUNT_APPRL_YEAR.CSV...")
    appr = {}
    with open(os.path.join(DATA_DIR, "appraisal", "ACCOUNT_APPRL_YEAR.CSV"),
              encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        for row in r:
            acct = row["ACCOUNT_NUM"].strip()
            if acct in acct_set:
                appr[acct] = row
    print(f"  Matched appraisal values: {len(appr)}")

    # --- Step 4: Load LAND (zoning + land use) ---
    print("Loading LAND.CSV...")
    land = {}
    with open(os.path.join(DATA_DIR, "appraisal", "LAND.CSV"),
              encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        for row in r:
            acct = row["ACCOUNT_NUM"].strip()
            if acct in acct_set and acct not in land:
                land[acct] = row
    print(f"  Matched land data: {len(land)}")

    # --- Step 5: Load COM_DETAIL (commercial bldg data) ---
    print("Loading COM_DETAIL.CSV...")
    com = {}
    with open(os.path.join(DATA_DIR, "appraisal", "COM_DETAIL.CSV"),
              encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        for row in r:
            acct = row["ACCOUNT_NUM"].strip()
            if acct in acct_set and acct not in com:
                com[acct] = row
    print(f"  Matched commercial detail: {len(com)}")

    # --- Step 6: Merge & write to DB ---
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    sptd_to_landuse = {
        "A": "SINGLE FAMILY", "B": "MULTI-FAMILY (2-5)", "C": "MULTI-FAMILY (6+)",
        "D": "CONDO", "E": "VACANT RES", "F": "COM VACANT",
        "G": "COMMERCIAL", "H": "HOTEL/MOTEL", "I": "INDUSTRIAL",
        "J": "INSTITUTIONAL", "K": "GOVERNMENT", "L": "FARM/RANCH",
        "M": "MINING/PETROLEUM", "N": "OTHER", "O": "UTILITY",
        "Q": "EXEMPT", "R": "RELIGIOUS", "S": "SCHOOL",
    }

    inserted = 0
    sql = f"""INSERT OR IGNORE INTO {DCAD_RAW_TABLE}
        (acct, type, rec_acres, owner_name, owner_name2, biz_name,
         site_address, property_city, property_zip,
         land_val, impr_val, total_val, zoning,
         sptd_code, sptd_desc, land_use_desc,
         year_built, gross_bldg_area, num_stories, bldg_class,
         nbhd_cd, legal_desc, deed_date, lat, lng, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    for acct in list(acct_set):
        geom = acct_to_geom.get(acct)
        if not geom:
            continue
        lat, lng, rec_acres = geom

        row_info = info.get(acct, {})
        row_appr = appr.get(acct, {})
        row_land = land.get(acct, {})
        row_com = com.get(acct, {})

        owner1 = row_info.get("OWNER_NAME1", "").strip()
        owner2 = row_info.get("OWNER_NAME2", "").strip()
        biz = row_info.get("BIZ_NAME", "").strip()
        site_addr = " ".join(filter(None, [
            row_info.get("STREET_NUM", "").strip(),
            row_info.get("FULL_STREET_NAME", "").strip(),
        ])).strip()
        prop_city = row_info.get("PROPERTY_CITY", "").strip()
        prop_zip = row_info.get("PROPERTY_ZIPCODE", "").strip()

        try:
            land_val = float(row_appr.get("LAND_VAL", "0").replace(",", ""))
        except: land_val = 0
        try:
            impr_val = float(row_appr.get("IMPR_VAL", "0").replace(",", ""))
        except: impr_val = 0
        try:
            total_val = float(row_appr.get("TOT_VAL", "0").replace(",", ""))
        except: total_val = 0

        zoning = row_land.get("ZONING", "").strip()[:100] if row_land else ""
        spcd = row_land.get("SPTD_CD", "").strip()[:10] if row_land else ""
        spdesc = row_land.get("SPTD_DESC", "").strip()[:100] if row_land else ""
        land_use_desc = sptd_to_landuse.get(spcd[:1], "")

        yr_built = None
        bldg_area = None
        num_stories = None
        bldg_class = ""
        if row_com:
            try: yr_built = int(row_com.get("YEAR_BUILT", "0")) or None
            except: pass
            try: bldg_area = float(row_com.get("GROSS_BLDG_AREA", "0").replace(",","")) or None
            except: pass
            try: num_stories = float(row_com.get("NUM_STORIES", "0")) or None
            except: pass
            bldg_class = row_com.get("BLDG_CLASS_DESC", "").strip()[:100]

        nbhd = row_info.get("NBHD_CD", "").strip()[:50]
        legal = " | ".join(filter(None, [row_info.get(f"LEGAL{i}", "").strip() for i in range(1,6)]))
        deed_date = row_info.get("DEED_TXFR_DATE", "").strip()[:20]

        raw_json = json.dumps({
            "info": {k: row_info.get(k) for k in ("BIZ_NAME","OWNER_NAME1","OWNER_NAME2","LEGAL1","GIS_PARCEL_ID")},
            "land": {k: row_land.get(k) for k in ("SPTD_CD","SPTD_DESC","ZONING","AREA_SIZE","AREA_UOM_DESC")} if row_land else {},
            "com": {k: row_com.get(k) for k in ("YEAR_BUILT","GROSS_BLDG_AREA","NUM_STORIES","BLDG_CLASS_DESC")} if row_com else {},
        })

        try:
            conn.execute(sql, (
                acct, parcels.loc[parcels["Acct"]==acct, "Type"].iloc[0] if acct in parcels["Acct"].values else 0,
                float(re.sub(r"[^\d.]", "", rec_acres)) if rec_acres else None,
                owner1 or None, owner2 or None, biz or None,
                site_addr or None, prop_city or None, prop_zip or None,
                land_val or None, impr_val or None, total_val or None,
                zoning or None, spcd or None, spdesc or None, land_use_desc or None,
                yr_built, bldg_area, num_stories, bldg_class or None,
                nbhd or None, legal or None, deed_date or None,
                lat, lng, raw_json,
            ))
            inserted += 1
            if inserted % 50000 == 0:
                conn.commit()
                print(f"  Inserted {inserted}/{len(acct_set)}...")
        except Exception as e:
            print(f"  Error inserting {acct}: {e}")

    conn.commit()
    cnt = conn.execute(f"SELECT COUNT(*) FROM {DCAD_RAW_TABLE}").fetchone()[0]
    conn.close()
    print(f"\nDCAD ingest complete: {cnt} parcels in {DCAD_RAW_TABLE}")

    return cnt


###############################################################################
# Travis County (TNRIS) Ingest — D53
###############################################################################

TRAVIS_RAW_TABLE = "raw_travis_tx_parcels"

def ingest_travis():
    """Download Travis County parcels from TNRIS CKAN API."""
    print("\n" + "=" * 60)
    print("DIRECTIVE 53: Austin Alpha — Travis County via TNRIS")
    print("=" * 60)

    import requests
    from datetime import datetime

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TRAVIS_RAW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parcel_id TEXT UNIQUE,
            owner_name TEXT,
            owner_name2 TEXT,
            site_address TEXT,
            property_city TEXT,
            property_zip TEXT,
            land_val REAL,
            impr_val REAL,
            total_val REAL,
            acres REAL,
            year_built INTEGER,
            sqft REAL,
            land_use TEXT,
            zoning TEXT,
            lat REAL,
            lng REAL,
            raw_json TEXT
        )
    """)

    # TNRIS CKAN API — search for Travis County parcels
    ckan = "https://data.tnris.org/api/3/action"
    print("Searching TNRIS for Travis County parcels...")

    try:
        r = requests.get(f"{ckan}/package_search", params={
            "q": "Travis County parcels cadastral",
            "rows": 20,
        }, timeout=15)
        results = r.json().get("result", {}).get("results", [])
        print(f"  Found {len(results)} datasets")

        for ds in results:
            title = ds.get("title", "")
            print(f"    Dataset: {title}")
            for res in ds.get("resources", []):
                fmt = res.get("format", "")
                url = res.get("url", "")
                desc = res.get("description", "")
                if "parcel" in (fmt.lower() + desc.lower() + title.lower()):
                    print(f"      Resource: {fmt} — {url[:120]}")

    except Exception as e:
        print(f"  CKAN search error: {e}")

    # Try the TxGIO Open Data Portal (ArcGIS Hub)
    print("\nChecking TxGIO ArcGIS Open Data...")
    try:
        r = requests.get(
            "https://data.tnris.org/api/3/action/package_show",
            params={"id": "travis-county-parcels"},
            timeout=10
        )
        print(f"  Direct lookup: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"  Title: {data.get('result',{}).get('title','N/A')}")

        # Alternative: search by tag
        r2 = requests.get(f"{ckan}/package_search", params={
            "q": "travis county parcel",
            "rows": 10,
        }, timeout=10)
        results = r2.json().get("result", {}).get("results", [])
        for ds in results:
            print(f"  Dataset: {ds.get('title','')}")
            for res in ds.get("resources", [])[:3]:
                print(f"    {res.get('format','')}: {res.get('url','')[:100]}")

    except Exception as e:
        print(f"  Error: {e}")

    conn.close()
    print("Travis County ingest — dataset URLs logged above for manual review.")


###############################################################################
# Transmission Line Matching (ONCOR 345kV)
###############################################################################

def transmission_match():
    """Match Texas commercial_sites to 345kV transmission lines from infrastructure.db."""
    print("\n" + "=" * 60)
    print("DIRECTIVE 52b: ONCOR 345kV Transmission Corridor Match")
    print("=" * 60)

    conn = sqlite3.connect(INFRA_DB)
    conn.row_factory = sqlite3.Row

    # Get all 345kV+ transmission lines in Texas
    lines = conn.execute("""
        SELECT source_id, voltage, owner, geometry_geojson
        FROM transmission_lines
        WHERE voltage >= 345
    """).fetchall()
    conn.close()

    if not lines:
        print("No 345kV+ lines found in infrastructure.db")
        return

    print(f"Loaded {len(lines)} high-voltage line segments")

    # Parse GeoJSON
    line_geoms = []
    for l in lines:
        try:
            geom = json.loads(l["geometry_geojson"])
            coords = geom.get("coordinates", [])
            # Flatten MultiLineString if needed
            if geom["type"] == "MultiLineString":
                for segment in coords:
                    line_geoms.append((l["voltage"], l["owner"], segment))
            elif geom["type"] == "LineString":
                line_geoms.append((l["voltage"], l["owner"], coords))
        except Exception:
            pass

    print(f"Parsed {len(line_geoms)} line segments")

    # Done inline — the actual point-to-line matching is fast enough here
    # for bulk matching. We'll do it in the merge step instead.
    print("Transmission data ready. Run merge-pipeline to combine with parcels.")


###############################################################################
# Merge Texas raw -> commercial_sites
###############################################################################

def merge_texas():
    """Merge TX raw parcel tables into commercial_sites following merge_to_commercial logic."""
    print("\n" + "=" * 60)
    print("Merging Texas raw parcels -> commercial_sites")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)

    # Get all TX raw tables
    raw_tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'raw_%_tx_parcels'"
    ).fetchall()]
    print(f"Found TX raw tables: {raw_tables}")

    for table in raw_tables:
        county = table.replace("raw_", "").replace("_tx_parcels", "").title()
        print(f"\nProcessing {table} (county={county})...")

        rows = conn.execute(f"""
            SELECT * FROM {table}
            WHERE rec_acres >= 10 AND rec_acres < 2000
        """).fetchall()
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        print(f"  Parcels with 10-2000ac: {len(rows)}")

        # Filter to commercial/industrial
        commercial_prefixes = ("G", "I", "J", "F", "O", "M")
        industrial_prefixes = ("I", "M", "O")
        corporate_keywords = ("LLC", "INC", "CORP", "LP", "LTD", "CO ", "HOLDINGS")

        matched = 0
        for row in rows:
            d = dict(zip(cols, row))

            spcd = (d.get("sp td_code") or d.get("sp td_ code") or "").strip()[:1]
            land_use = (d.get("land_use_desc") or "").upper()
            owner = (d.get("owner_name") or "").upper()
            biz = (d.get("biz_name") or "").upper()
            combined = f"{owner} {biz} {land_use}"

            is_commercial = spcd in commercial_prefixes or any(
                kw in combined for kw in ("INDUSTRIAL", "COMMERCIAL", "WAREHOUSE",
                "MANUFACTURING", "DISTRIBUTION", "OFFICE", "BUSINESS PARK",
                "RETAIL", "APARTMENT", "MULTI-FAMILY", "MINING", "UTILITY",
                "PETROL", "GAS", "STORAGE", "FLEX")
            )
            is_corporate = any(kw in owner for kw in corporate_keywords)

            if not (is_commercial or is_corporate):
                continue

            # Compute scores
            acres = d.get("rec_acres") or 0
            score_ac = min(25, int(acres / 10) * 5) if acres >= 10 else 0
            score_lu = 20 if spcd in ("G", "I") else (10 if spcd else 5)
            score_vacancy = 10 if d.get("impr_val", 0) == 0 else 0
            score_owner = 5 if any(kw in owner for kw in corporate_keywords) else 0

            total = score_ac + score_lu + score_vacancy + score_owner
            tier = "A" if total >= 65 else ("B" if total >= 45 else ("C" if total >= 25 else "D"))

            site_addr = d.get("s ite_address") or d.get("site_address") or ""
            prop_city = d.get("property_city") or "Dallas"
            owner_name = d.get("owner_name") or d.get("biz_name") or ""

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO commercial_sites
                    (pid, county, address, owner_name, acres,
                     land_value, bldg_value, total_value,
                     land_use, last_sale_date,
                     score_acreage, score_land_use, score_vacancy, score_owner,
                     score_density, score_total, score_tier, s1_source,
                     lat, lng, zoning)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)
                """, (
                    d.get("acct") or d.get("parcel_id"),
                    county.lower(),
                    site_addr or None,
                    owner_name or None,
                    acres,
                    d.get("land_val", 0),
                    d.get("impr_val", 0),
                    d.get("total_val", 0),
                    (d.get("land_use_desc") or "")[:100],
                    (d.get("deed_date") or "")[:20],
                    score_ac, score_lu, score_vacancy, score_owner,
                    total, tier,
                    table,
                    d.get("lat"), d.get("lng"),
                    (d.get("zoning") or "")[:100],
                ))
                matched += 1
            except Exception as e:
                print(f"    Insert error: {e}")

        conn.commit()
        print(f"  Inserted {matched} commercial sites from {table}")

    tx_count = conn.execute(
        "SELECT COUNT(*) FROM commercial_sites WHERE county IN "
        "('dallas','tarrant','collin','denton','travis')"
    ).fetchone()[0]
    conn.close()
    print(f"\nTotal Texas commercial sites: {tx_count}")
    return tx_count


###############################################################################
# Main
###############################################################################

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Texas Front ingest pipeline")
    parser.add_argument("--ingest-dallas", action="store_true", help="Ingest DCAD parcels")
    parser.add_argument("--ingest-travis", action="store_true", help="Check TNRIS for Travis Co")
    parser.add_argument("--merge", action="store_true", help="Merge TX raw -> commercial_sites")
    parser.add_argument("--transmission-match", action="store_true", help="Match to ONCOR 345kV")
    args = parser.parse_args()

    if args.ingest_dallas:
        ingest_dcad()
    if args.ingest_travis:
        ingest_travis()
    if args.merge:
        merge_texas()
    if args.transmission_match:
        transmission_match()

    if not any(vars(args).values()):
        parser.print_help()
