"""
Directive 32: Statewide Cadastral Breach
Download parcel data from Florida and Virginia statewide portals.

FL: services9.arcgis.com/Gh9awoU677aKree0/ArcGIS/rest/services/Florida_Statewide_Cadastral
VA: vgin.vdem.virginia.gov (VGIN) — download per-county GeoPackage
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen, Request

DB_PATH = Path(__file__).parent.parent / "deals.db"
CHUNK = 2000
USER_AGENT = "Mozilla/5.0"

FL_ENDPOINT = (
    "https://services9.arcgis.com/Gh9awoU677aKree0/ArcGIS/rest/services/"
    "Florida_Statewide_Cadastral/FeatureServer/0"
)

VA_ENDPOINT = None  # VGIN requires county-by-county approach

# Target counties for expansion
FL_TARGETS = ["Orange", "Hillsborough", "Duval", "Miami-Dade", "Broward",
              "Palm Beach", "Seminole", "Osceola", "Lake", "Polk"]
VA_TARGETS = ["Loudoun", "Prince William", "Fairfax", "Stafford",
              "Spotsylvania", "Culpeper", "Fauquier", "Rappahannock"]


def arcgis_query(url, offset=0, fields="*"):
    params = (
        f"where=1%3D1&outFields={fields}"
        f"&resultOffset={offset}&resultRecordCount={CHUNK}"
        f"&f=json&returnGeometry=false"
    )
    full = f"{url}/query?{params}"
    req = Request(full, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def get_fields(url):
    data = arcgis_query(url, offset=0)
    return [f["name"] for f in data.get("fields", [])]


def get_county_filter(county_name, fields):
    """Find the right county field and build where clause."""
    for fname in ("COUNTYNAME", "COUNTY", "CNTYNAME", "cnty_name", "county"):
        if fname in fields:
            return f"UPPER({fname})='{county_name.upper()}'"
    # FL uses CO_NO (county number, FIPS numeric without state prefix)
    if "CO_NO" in fields:
        fl_codes = {
            "orange": 95, "hillsborough": 57, "duval": 31, "miami-dade": 25,
            "broward": 11, "palm beach": 99, "seminole": 117,
            "osceola": 97, "lake": 69, "polk": 105,
        }
        code = fl_codes.get(county_name.lower())
        if code:
            return f"CO_NO={code}"
    return None


def download_fl_county(county_name):
    print(f"\n  FL: {county_name}")
    
    fields = get_fields(FL_ENDPOINT)
    where = get_county_filter(county_name, fields)
    if not where:
        print(f"    Cannot filter by county — no county field found")
        return 0
    
    conn = sqlite3.connect(str(DB_PATH))
    tbl = f"fl_parcels_{county_name.lower().replace(' ','_')}"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {tbl} (
            objectid INTEGER PRIMARY KEY,
            properties TEXT
        )
    """)
    
    total = 0
    offset = 0
    stall_count = 0
    while True:
        url = f"{FL_ENDPOINT}/query"
        params = (f"where={where.replace('=', '%3D')}"
                  f"&outFields=*&resultOffset={offset}"
                  f"&resultRecordCount={CHUNK}"
                  f"&f=json&returnGeometry=false")
        req = Request(f"{url}?{params}", headers={"User-Agent": USER_AGENT})
        
        try:
            with urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
        except Exception as e:
            print(f"    Error at offset {offset}: {e}")
            stall_count += 1
            if stall_count > 5:
                print(f"    Too many errors, aborting")
                break
            time.sleep(5)
            continue
        
        features = data.get("features", [])
        if not features:
            break
        stall_count = 0
        
        for f in features:
            attrs = f.get("attributes", {})
            oid = attrs.get("OBJECTID") or 0
            conn.execute(
                f"INSERT OR IGNORE INTO {tbl} (objectid, properties) VALUES (?,?)",
                (oid, json.dumps(attrs)),
            )
        
        conn.commit()
        total += len(features)
        offset += len(features)
        print(f"    {total} parcels...", end="\r")
        
        if len(features) < CHUNK or not data.get("exceededTransferLimit"):
            break
        time.sleep(0.3)
    
    print(f"\n    Total: {total} parcels")
    conn.close()
    return total


def discover_va_endpoints():
    """Discover VGIN parcel feature services for target VA counties."""
    print("\n  VA parcel data available via VGIN:")
    print("  VGIN Parcel Points: https://vgin.vdem.virginia.gov/search?q=parcels")
    print("  Download per-county GPKG from VGIN data portal")
    print("  Or use VA_Parcels FeatureServer (stcntyfips filter)")
    
    # Try the VA state-wide parcel service
    va_url = ("https://vgin.vdem.virginia.gov/arcgis/rest/services/"
              "VA_Parcels/MapServer")
    try:
        req = Request(va_url + "?f=json", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            print(f"  VA_Parcels service available: {data.get('serviceDescription','')[:100]}")
            return va_url
    except:
        print("  VA_Parcels service not accessible via REST")
        return None


def main():
    parser = argparse.ArgumentParser(description="Download FL/VA statewide parcels")
    parser.add_argument("--state", choices=["FL", "VA", "both"], default="FL",
                        help="State to download")
    parser.add_argument("--counties", nargs="+", default=None,
                        help="Specific counties (default: all targets)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    if args.state in ("FL", "both"):
        counties = args.counties or FL_TARGETS
        print(f"Downloading FL parcels for {len(counties)} counties")
        total = 0
        for c in counties:
            if args.dry_run:
                print(f"  Would download FL: {c}")
                continue
            cnt = download_fl_county(c)
            total += cnt
        if not args.dry_run:
            print(f"\nFL total: {total:,} parcels")
    
    if args.state in ("VA", "both"):
        print("\nVA parcel download:")
        va_url = discover_va_endpoints()
        if va_url and not args.dry_run:
            print("  VA data ready for per-county GeoPackage download")
        elif args.dry_run:
            print("  VA: would download per-county GeoPackage for:",
                  args.counties or VA_TARGETS)


if __name__ == "__main__":
    main()
