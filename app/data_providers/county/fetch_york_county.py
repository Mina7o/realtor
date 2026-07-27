"""Fetch York County, SC parcel data from ArcGIS REST endpoint.

Targets zip codes for Fort Mill area (29715, 29708, 29707).

Stores in deals.db york_parcels table, then matches to listings.
"""
import json
import os
import sys
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from db import get_conn

API_URL = (
    "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/arcgis/rest/services/"
    "Parcels/FeatureServer/0/query"
)

TARGET_ZIPS = ["29715", "29708", "29707"]
PAGE_SIZE = 2000

FIELDS = [
    "OBJECTID", "TAXMAPID", "ParcelID",
    "Owner1", "Owner2", "Owner3",
    "MailAddr1", "MailCity", "MailState", "MailZip",
    "PropertyAddress",
    "YearBuilt", "FinishedSQFT",
    "SalePrice", "DateSold",
    "AprTotVal", "AprLandVal", "AprBldgVal", "AprMiscVal",
    "GISSizeAC", "deededacres",
    "subdivision", "BldgTypeDesc", "LandUseDesc",
    "BuildingCount", "HOMESTEAD",
    "NeighborhoodDesc", "SchSpeTaxDist", "MuniTaxDist",
]

OUT_FIELDS = ",".join(FIELDS)


def ensure_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS york_parcels (
            objectid INTEGER PRIMARY KEY,
            taxmapid TEXT,
            parcelid TEXT,
            owner1 TEXT,
            owner2 TEXT,
            owner3 TEXT,
            mail_addr1 TEXT,
            mail_city TEXT,
            mail_state TEXT,
            mail_zip TEXT,
            property_address TEXT,
            year_built INTEGER,
            finished_sqft REAL,
            sale_price REAL,
            date_sold TEXT,
            apr_tot_val REAL,
            apr_land_val REAL,
            apr_bldg_val REAL,
            apr_misc_val REAL,
            gis_acres REAL,
            deeded_acres REAL,
            subdivision TEXT,
            bldg_type_desc TEXT,
            land_use_desc TEXT,
            building_count INTEGER,
            homestead TEXT,
            neighborhood_desc TEXT,
            sch_dist TEXT,
            muni_dist TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_york_parcels_addr
            ON york_parcels(property_address);
        CREATE INDEX IF NOT EXISTS idx_york_parcels_zip
            ON york_parcels(mail_zip);
    """)
    conn.commit()
    conn.close()
    print("  Table york_parcels ready")


def count_zip(zip_code):
    params = {
        "where": f"MailZip = '{zip_code}' AND PropertyAddress IS NOT NULL",
        "returnCountOnly": "true",
        "f": "json",
    }
    r = requests.get(API_URL, params=params, timeout=30)
    return r.json().get("count", 0)


def fetch_page(zip_code, offset, page_size=PAGE_SIZE):
    where = f"MailZip = '{zip_code}' AND PropertyAddress IS NOT NULL"
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
            a.get("OBJECTID"),
            a.get("TAXMAPID"),
            a.get("ParcelID"),
            a.get("Owner1"),
            a.get("Owner2"),
            a.get("Owner3"),
            a.get("MailAddr1"),
            a.get("MailCity"),
            a.get("MailState"),
            a.get("MailZip"),
            a.get("PropertyAddress"),
            a.get("YearBuilt"),
            a.get("FinishedSQFT"),
            a.get("SalePrice"),
            epoch_to_date(a.get("DateSold")),
            a.get("AprTotVal"),
            a.get("AprLandVal"),
            a.get("AprBldgVal"),
            a.get("AprMiscVal"),
            a.get("GISSizeAC"),
            a.get("deededacres"),
            a.get("subdivision"),
            a.get("BldgTypeDesc"),
            a.get("LandUseDesc"),
            a.get("BuildingCount"),
            a.get("HOMESTEAD"),
            a.get("NeighborhoodDesc"),
            a.get("SchSpeTaxDist"),
            a.get("MuniTaxDist"),
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO york_parcels
        (objectid, taxmapid, parcelid,
         owner1, owner2, owner3,
         mail_addr1, mail_city, mail_state, mail_zip,
         property_address,
         year_built, finished_sqft,
         sale_price, date_sold,
         apr_tot_val, apr_land_val, apr_bldg_val, apr_misc_val,
         gis_acres, deeded_acres,
         subdivision, bldg_type_desc, land_use_desc,
         building_count, homestead,
         neighborhood_desc, sch_dist, muni_dist)
        VALUES (?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def normalize_addr(addr):
    if not addr:
        return ""
    a = addr.strip().upper()
    a = a.replace(".", "")
    a = a.replace(",", "")
    a = a.replace("  ", " ")
    return a


def addr_match_score(listing_addr, parcel_addr):
    """Score 0-1 how well two addresses match."""
    la = normalize_addr(listing_addr)
    pa = normalize_addr(parcel_addr)
    if not la or not pa:
        return 0
    if la == pa:
        return 1.0
    if pa.endswith(la) or la.endswith(pa):
        return 0.9
    la_parts = la.split()
    pa_parts = pa.split()
    if len(la_parts) >= 2 and len(pa_parts) >= 2:
        if la_parts[0] == pa_parts[0] and la_parts[1] == pa_parts[1]:
            if len(la_parts) >= 3 and len(pa_parts) >= 3:
                if la_parts[2] == pa_parts[2]:
                    return 0.8
            return 0.6
    return 0


def match_listings():
    """Match Fort Mill area listings to York parcels by address."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_york (
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
        WHERE p.city IN ('Fort Mill', 'Tega Cay', 'Rock Hill', 'Lake Wylie')
    """).fetchall()
    for row in listings:
        lid, addr, city, state, zip_code = row["id"], row["address"], row["city"], row["state"], row["zip"]
        if not addr:
            continue
        street = addr.split(",")[0].strip()
        parcels = conn.execute("""
            SELECT objectid, property_address FROM york_parcels
            WHERE mail_zip = ? AND UPPER(property_address) LIKE ?
        """, (zip_code, f"%{normalize_addr(street)}%")).fetchall()

        best_score = 0
        best_pid = None
        for p in parcels:
            score = addr_match_score(street, p["property_address"])
            if score > best_score:
                best_score = score
                best_pid = str(p["objectid"])
        if best_pid and best_score >= 0.6:
            conn.execute("""
                INSERT OR IGNORE INTO listing_county_york (listing_id, pid, match_score)
                VALUES (?, ?, ?)
            """, (lid, best_pid, best_score))
            matched += 1
    conn.commit()
    conn.close()
    return matched


def main():
    print("Fetching York County, SC parcel data")
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
