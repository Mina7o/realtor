"""Fetch Union County, NC parcel data from ArcGIS REST endpoint.

Targets zip codes for Waxhaw (28173), Weddington (28104),
Indian Trail (28079), and Monroe (28110, 28112).

Stores in deals.db union_parcels table, then runs
listing_county_match to connect to existing listings.
"""
import json
import os
import sys
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from db import get_conn

# Union County ArcGIS Parcels layer
API_URL = (
    "https://atlas.unioncountync.gov/server/rest/services/"
    "OperationalLayers/MapServer/215/query"
)

TARGET_ZIPS = ["28173", "28104", "28079", "28110", "28112"]
PAGE_SIZE = 2000

FIELDS = [
    "PID", "PHYSSTRADD",
    "CURR_NAME1", "CURR_NAME2",
    "CURR_CITY", "CURR_STATE", "CURR_ZIPCODE",
    "FMV_TOTAL", "FMV_LAND", "FMV_IMPRV",
    "TOTVAL",
    "mapped_acres", "gross_acres",
    "YEARBLT", "SQFT",
    "property_use",
    "subdivision",
    "STRUCTSTYLE", "STRUCTTYPE", "BLDQUAL_CODE", "DESC1_DESC",
    "s1_SALESAMT", "s1_SALEDATE", "s1_DEEDTYPE", "s1_grantor", "s1_grantor2",
    "s2_SALESAMT", "s2_SALEDATE", "s2_DEEDTYPE", "s2_grantor", "s2_grantor2",
    "s3_SALESAMT", "s3_SALEDATE", "s3_DEEDTYPE", "s3_grantor", "s3_grantor2",
    "NBHDNUM", "NBHDNAME", "LAND_TYPE", "LAND_CODE",
]

OUT_FIELDS = ",".join(FIELDS)


def ensure_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS union_parcels (
            pid TEXT PRIMARY KEY,
            physstradd TEXT,
            curr_name1 TEXT,
            curr_name2 TEXT,
            curr_city TEXT,
            curr_state TEXT,
            curr_zipcode TEXT,
            fmv_total REAL,
            fmv_land REAL,
            fmv_imprv REAL,
            totval REAL,
            mapped_acres REAL,
            gross_acres REAL,
            yearblt INTEGER,
            sqft REAL,
            property_use TEXT,
            subdivision TEXT,
            structstyle TEXT,
            structtype TEXT,
            bldqual_code TEXT,
            desc1_desc TEXT,
            s1_salesamt REAL,
            s1_saledate TEXT,
            s1_deedtype TEXT,
            s1_grantor TEXT,
            s1_grantor2 TEXT,
            s2_salesamt REAL,
            s2_saledate TEXT,
            s2_deedtype TEXT,
            s2_grantor TEXT,
            s2_grantor2 TEXT,
            s3_salesamt REAL,
            s3_saledate TEXT,
            s3_deedtype TEXT,
            s3_grantor TEXT,
            s3_grantor2 TEXT,
            nbhdnum TEXT,
            nbhdname TEXT,
            land_type TEXT,
            land_code TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_union_parcels_addr
            ON union_parcels(physstradd);
        CREATE INDEX IF NOT EXISTS idx_union_parcels_zip
            ON union_parcels(curr_zipcode);
    """)
    conn.commit()
    conn.close()
    print("  Table union_parcels ready")


def count_zip(zip_code):
    params = {
        "where": f"CURR_ZIPCODE='{zip_code}' AND PHYSSTRADD <> ''",
        "returnCountOnly": "true",
        "f": "json",
    }
    r = requests.get(API_URL, params=params, timeout=30)
    return r.json().get("count", 0)


def fetch_page(zip_code, offset, page_size=PAGE_SIZE):
    where = f"CURR_ZIPCODE='{zip_code}' AND PHYSSTRADD <> ''"
    params = {
        "where": where,
        "outFields": OUT_FIELDS,
        "returnGeometry": "false",
        "f": "json",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
    }
    r = requests.get(API_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def epoch_to_date(ms):
    if not ms or ms < 0:
        return None
    from datetime import datetime
    return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def store_batch(conn, features):
    rows = []
    for f in features:
        a = f["attributes"]
        rows.append((
            a.get("PID"),
            a.get("PHYSSTRADD"),
            a.get("CURR_NAME1"),
            a.get("CURR_NAME2"),
            a.get("CURR_CITY"),
            a.get("CURR_STATE"),
            a.get("CURR_ZIPCODE"),
            a.get("FMV_TOTAL"),
            a.get("FMV_LAND"),
            a.get("FMV_IMPRV"),
            a.get("TOTVAL"),
            a.get("mapped_acres"),
            a.get("gross_acres"),
            a.get("YEARBLT"),
            a.get("SQFT"),
            a.get("property_use"),
            a.get("subdivision"),
            a.get("STRUCTSTYLE"),
            a.get("STRUCTTYPE"),
            a.get("BLDQUAL_CODE"),
            a.get("DESC1_DESC"),
            a.get("s1_SALESAMT"),
            epoch_to_date(a.get("s1_SALEDATE")),
            a.get("s1_DEEDTYPE"),
            a.get("s1_grantor"),
            a.get("s1_grantor2"),
            a.get("s2_SALESAMT"),
            epoch_to_date(a.get("s2_SALEDATE")),
            a.get("s2_DEEDTYPE"),
            a.get("s2_grantor"),
            a.get("s2_grantor2"),
            a.get("s3_SALESAMT"),
            epoch_to_date(a.get("s3_SALEDATE")),
            a.get("s3_DEEDTYPE"),
            a.get("s3_grantor"),
            a.get("s3_grantor2"),
            a.get("NBHDNUM"),
            a.get("NBHDNAME"),
            a.get("LAND_TYPE"),
            a.get("LAND_CODE"),
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO union_parcels
        (pid, physstradd, curr_name1, curr_name2,
         curr_city, curr_state, curr_zipcode,
         fmv_total, fmv_land, fmv_imprv, totval,
         mapped_acres, gross_acres, yearblt, sqft,
         property_use, subdivision,
         structstyle, structtype, bldqual_code, desc1_desc,
         s1_salesamt, s1_saledate, s1_deedtype, s1_grantor, s1_grantor2,
         s2_salesamt, s2_saledate, s2_deedtype, s2_grantor, s2_grantor2,
         s3_salesamt, s3_saledate, s3_deedtype, s3_grantor, s3_grantor2,
         nbhdnum, nbhdname, land_type, land_code)
        VALUES (?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)
    """, rows)
    conn.commit()


def match_listings():
    """Match listings to county parcels by address.
    Uses listing_county_match table to record connections."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_union (
            listing_id INTEGER,
            pid TEXT,
            match_score REAL,
            PRIMARY KEY (listing_id, pid)
        )
    """)
    matched = 0
    listings = conn.execute("""
        SELECT l.id, p.address, p.city, p.state, p.zip
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        WHERE p.city IN ('Waxhaw', 'Weddington', 'Indian Trail', 'Monroe')
    """).fetchall()
    for row in listings:
        lid, addr, city, state, zip_code = row["id"], row["address"], row["city"], row["state"], row["zip"]
        if not addr:
            continue
        street = addr.split(",")[0].strip().upper()
        parcels = conn.execute("""
            SELECT pid, physstradd FROM union_parcels
            WHERE UPPER(physstradd) LIKE ?
        """, (f"%{street}%",)).fetchall()
        for p in parcels:
            conn.execute("""
                INSERT OR IGNORE INTO listing_county_union (listing_id, pid, match_score)
                VALUES (?, ?, 1.0)
            """, (lid, p["pid"]))
            matched += 1
    conn.commit()
    conn.close()
    return matched


def main():
    print("Fetching Union County, NC parcel data")
    print(f"  Target zips: {', '.join(TARGET_ZIPS)}")
    print()

    ensure_table()

    conn = get_conn()
    total_all = 0

    for zip_code in TARGET_ZIPS:
        total = count_zip(zip_code)
        print(f"\n{zip_code}: {total} parcels with addresses")
        offset = 0
        page = 0
        errors = 0

        while offset < total:
            try:
                data = fetch_page(zip_code, offset)
                features = data.get("features", [])
                if not features:
                    print(f"  No features at offset {offset}, stopping")
                    break
                store_batch(conn, features)
                offset += len(features)
                errors = 0
                page += 1
                pct = min(offset * 100 / total, 100)
                print(f"  Page {page:2d}: {offset:>6,}/{total:<6,} ({pct:5.1f}%) - {len(features):,} records")
            except Exception as e:
                errors += 1
                print(f"  Error at offset {offset}: {e}")
                if errors > 5:
                    print("  Too many errors, aborting this zip")
                    break
                time.sleep(5)

        total_all += offset

    conn.close()
    print(f"\nDownloaded {total_all:,} total parcels")

    print("\nMatching listings to parcels...")
    matched = match_listings()
    print(f"  {matched} listing-parcel connections created")

    print("\nDone! New market values and owner info should now be available.")
    print("Restart dashboard to pick up changes.")


if __name__ == "__main__":
    main()
