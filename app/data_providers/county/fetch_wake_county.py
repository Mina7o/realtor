"""Fetch Wake County, NC parcel data from ArcGIS REST endpoint.
Targets Raleigh and surrounding towns (Cary, Apex, Holly Springs, etc.).
Stores in deals.db wake_parcels table.
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
    "https://maps.wakegov.com/arcgis/rest/services/"
    "Property/Parcels/MapServer/0/query"
)

TARGET_CITIES = [
    "RALEIGH", "CARY", "APEX", "HOLLY SPRINGS", "FUQUAY-VARINA",
    "GARNER", "WAKE FOREST", "KNIGHTDALE", "WENDELL",
    "ZEBULON", "ROLESVILLE", "MORRISVILLE",
]
PAGE_SIZE = 2000

FIELDS = [
    "PIN_NUM", "OWNER", "ADDR1", "ADDR2", "ADDR3",
    "SITE_ADDRESS", "CITY", "CITY_DECODE", "ZIPNUM",
    "DEED_ACRES", "BLDG_VAL", "LAND_VAL", "TOTAL_VALUE_ASSD",
    "HEATEDAREA", "YEAR_BUILT", "TOTSALPRICE", "SALE_DATE",
    "BILLCLASS", "BILLING_CLASS_DECODE", "PROPDESC",
    "DESIGNSTYL", "DESIGN_STYLE_DECODE", "UNITS",
    "TYPE_AND_USE", "TYPE_USE_DECODE", "LAND_CLASS_DECODE",
    "TOWNSHIP_DECODE", "OBJECTID", "EXEMPTDESC", "EXEMPTSTAT",
    "STNUM", "FULL_STREET_NAME",
]

OUT_FIELDS = ",".join(FIELDS)


def ensure_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wake_parcels (
            pin_num TEXT PRIMARY KEY,
            owner TEXT,
            addr1 TEXT, addr2 TEXT, addr3 TEXT,
            site_address TEXT,
            city TEXT, city_decode TEXT, zipnum TEXT,
            deed_acres REAL,
            bldg_val REAL, land_val REAL, total_value_assd REAL,
            heatedarea REAL,
            year_built INTEGER,
            totsalprice REAL, sale_date TEXT,
            billclass REAL, billing_class_decode TEXT,
            propdesc TEXT,
            designstyl TEXT, design_style_decode TEXT,
            units REAL,
            type_and_use TEXT, type_use_decode TEXT,
            land_class_decode TEXT,
            township_decode TEXT,
            objectid INTEGER,
            exemptdesc TEXT, exemptstat TEXT,
            stnum INTEGER, full_street_name TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_wake_site_addr
            ON wake_parcels(site_address);
    """)
    conn.commit()
    conn.close()


def fetch_page(where, offset=0):
    params = {
        "where": where,
        "outFields": OUT_FIELDS,
        "returnGeometry": "false",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "f": "json",
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
            return None, 0
        data = resp.json()
        if "error" in data:
            print(f"  API error: {data['error']}")
            return None, 0
        features = data.get("features", [])
        has_more = data.get("exceededTransferLimit", False)
        return features, has_more
    except Exception as e:
        print(f"  Request failed: {e}")
        return None, 0


def fetch_all():
    ensure_table()
    conn = get_conn()
    total_loaded = 0

    for city in TARGET_CITIES:
        where = f"UPPER(CITY_DECODE) = '{city}'"
        offset = 0
        page = 1
        city_count = 0

        print(f"\nFetching {city}...")
        while True:
            print(f"  Page {page} (offset {offset})...")
            features, has_more = fetch_page(where, offset)
            if features is None:
                break
            if not features:
                print(f"  No more results")
                break

            for f in features:
                attrs = f.get("attributes", {})
                if not attrs:
                    continue
                pin = str(attrs.get("PIN_NUM") or "")
                if not pin:
                    continue

                sale_date = attrs.get("SALE_DATE")
                if sale_date:
                    import datetime
                    try:
                        sale_date = datetime.datetime.fromtimestamp(
                            sale_date / 1000
                        ).strftime("%Y-%m-%d")
                    except (ValueError, OSError):
                        sale_date = None

                conn.execute("""
                    INSERT OR REPLACE INTO wake_parcels
                        (pin_num, owner, addr1, addr2, addr3,
                         site_address, city, city_decode, zipnum,
                         deed_acres, bldg_val, land_val, total_value_assd,
                         heatedarea, year_built,
                         totsalprice, sale_date,
                         billclass, billing_class_decode,
                         propdesc, designstyl, design_style_decode,
                         units, type_and_use, type_use_decode,
                         land_class_decode, township_decode,
                         objectid, exemptdesc, exemptstat,
                         stnum, full_street_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pin, attrs.get("OWNER"),
                    attrs.get("ADDR1"), attrs.get("ADDR2"), attrs.get("ADDR3"),
                    attrs.get("SITE_ADDRESS"),
                    attrs.get("CITY"), attrs.get("CITY_DECODE"), attrs.get("ZIPNUM"),
                    attrs.get("DEED_ACRES"),
                    attrs.get("BLDG_VAL"), attrs.get("LAND_VAL"), attrs.get("TOTAL_VALUE_ASSD"),
                    attrs.get("HEATEDAREA"),
                    attrs.get("YEAR_BUILT"),
                    attrs.get("TOTSALPRICE"), sale_date,
                    attrs.get("BILLCLASS"), attrs.get("BILLING_CLASS_DECODE"),
                    attrs.get("PROPDESC"),
                    attrs.get("DESIGNSTYL"), attrs.get("DESIGN_STYLE_DECODE"),
                    attrs.get("UNITS"),
                    attrs.get("TYPE_AND_USE"), attrs.get("TYPE_USE_DECODE"),
                    attrs.get("LAND_CLASS_DECODE"),
                    attrs.get("TOWNSHIP_DECODE"),
                    attrs.get("OBJECTID"),
                    attrs.get("EXEMPTDESC"), attrs.get("EXEMPTSTAT"),
                    attrs.get("STNUM"), attrs.get("FULL_STREET_NAME"),
                ))
                city_count += 1

            conn.commit()
            offset += len(features)
            page += 1

            if not has_more:
                break

            time.sleep(0.3)

        total_loaded += city_count
        print(f"  {city}: {city_count} parcels loaded")

    conn.close()
    print(f"\nTotal Wake County parcels loaded: {total_loaded}")


def match_listings():
    """Match listings to parcels by address normalization."""
    from db import normalize_address
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listing_county_wake (
            listing_id INTEGER PRIMARY KEY,
            property_id INTEGER,
            pin_num TEXT,
            site_address TEXT,
            match_score INTEGER,
            matched_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    known_cities = [r[0] for r in conn.execute(
        "SELECT DISTINCT city FROM properties WHERE city IS NOT NULL AND city != '' AND state = 'NC'"
    ).fetchall()]

    # Build parcel address lookup by normalized address
    parcels = conn.execute("""
        SELECT pin_num, site_address, UPPER(city_decode) as city, zipnum,
               stnum, full_street_name
        FROM wake_parcels
        WHERE site_address IS NOT NULL
    """).fetchall()

    parcel_by_norm = {}
    for p in parcels:
        addr = (p["site_address"] or "").strip().upper()
        city = p["city"] or ""
        zip_c = (p["zipnum"] or "")[:5]
        norm = normalize_address(addr, city, "NC", zip_c)
        if norm not in parcel_by_norm:
            parcel_by_norm[norm] = p["pin_num"]

    matched = 0
    listings = conn.execute("""
        SELECT l.id as listing_id, p.id as property_id,
               p.address, p.city, p.zip
        FROM listings l
        JOIN properties p ON l.property_id = p.id
    """).fetchall()

    for listing in listings:
        addr = (listing["address"] or "").strip().upper()
        city = (listing["city"] or "").strip().upper()
        zip_code = (listing["zip"] or "")[:5]
        norm = normalize_address(addr, city, "NC", zip_code)
        pin = parcel_by_norm.get(norm)
        if pin:
            conn.execute("""
                INSERT OR REPLACE INTO listing_county_wake
                    (listing_id, property_id, pin_num, site_address, match_score)
                VALUES (?, ?, ?, ?, 100)
            """, (listing["listing_id"], listing["property_id"], pin, addr))
            matched += 1

    conn.commit()
    conn.close()
    print(f"Matched {matched} listings to Wake County parcels")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Fetch Wake County, NC parcel data from ArcGIS"
    )
    parser.add_argument("--fetch", action="store_true",
                        help="Fetch parcel data from ArcGIS")
    parser.add_argument("--match", action="store_true",
                        help="Match listings to parcels")
    parser.add_argument("--all", action="store_true",
                        help="Fetch + Match")
    args = parser.parse_args()

    if args.all:
        args.fetch = True
        args.match = True

    if args.fetch or args.all:
        print("Fetching Wake County parcel data...")
        fetch_all()

    if args.match or args.all:
        print("\nMatching listings to parcels...")
        match_listings()

    if not args.fetch and not args.match and not args.all:
        print("Nothing to do. Use --fetch, --match, or --all")


if __name__ == "__main__":
    main()
