"""
Directive 28: ARC Georgia Infiltration
Download tax parcel data from Atlanta Regional Commission member counties.

ARC 11 counties: Cherokee, Clayton, Cobb, DeKalb, Douglas, Fayette,
                 Forsyth, Fulton, Gwinnett, Henry, Rockdale

Strategy: Use ArcGIS FeatureServer REST endpoints (no auth required)
to stream GeoJSON for available counties. Store in SQLite for local querying.

Fulton:  services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services/Tax_Parcels/FeatureServer/0
DeKalb:  services2.arcgis.com/IxVN2oUE9EYLSnPE/arcgis/rest/services/Tax_Parcels_2025/FeatureServer/0
"""

import json
import sqlite3
import time
import argparse
from pathlib import Path
from urllib.request import urlopen, Request

DB_PATH = Path(__file__).parent.parent / "deals.db"
CHUNK_SIZE = 2000

FEATURE_SERVERS = {
    "Fulton": "https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services/Tax_Parcels/FeatureServer/0",
    "DeKalb": "https://services2.arcgis.com/IxVN2oUE9EYLSnPE/arcgis/rest/services/Tax_Parcels_2025/FeatureServer/0",
}


def query_arcgis(url, where="1=1", out_fields="*", offset=0, limit=CHUNK_SIZE):
    params = (
        f"where={where}&outFields={out_fields}"
        f"&resultOffset={offset}&resultRecordCount={limit}"
        f"&f=json&returnGeometry=false"
    )
    full_url = f"{url}/query?{params}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    req = Request(full_url, headers=headers)
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def create_table(conn, county_name):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS arc_parcels_{county_name.lower()} (
            objectid INTEGER PRIMARY KEY,
            properties TEXT
        )
    """)
    conn.commit()


def download_county(county_name, server_url, dry_run=False):
    print(f"\n{'='*60}")
    print(f"County: {county_name}")
    print(f"Server: {server_url}")
    
    try:
        sample = query_arcgis(server_url, offset=0, limit=1)
    except Exception as e:
        print(f"  ERROR connecting: {e}")
        return 0
    
    field_names = [f["name"] for f in sample.get("fields", [])]
    print(f"  Fields ({len(field_names)}): {', '.join(field_names[:12])}...")
    
    total_count = 0
    offset = 0
    batch = 0
    
    if not dry_run:
        conn = sqlite3.connect(str(DB_PATH))
        create_table(conn, county_name)
    
    while True:
        try:
            data = query_arcgis(server_url, offset=offset, limit=CHUNK_SIZE)
            features = data.get("features", [])
            
            if not features:
                break
            
            batch += 1
            total_count += len(features)
            
            if not dry_run:
                rows = []
                for f in features:
                    attrs = f.get("attributes", {})
                    obj_id = attrs.get("OBJECTID") or 0
                    rows.append((obj_id, json.dumps(attrs)))
                
                conn.executemany(
                    f"INSERT OR REPLACE INTO arc_parcels_{county_name.lower()} "
                    f"(objectid, properties) VALUES (?, ?)",
                    rows,
                )
                conn.commit()
            
            offset += len(features)
            print(f"    Batch {batch}: {offset} records", end="\r")
            
            if not data.get("exceededTransferLimit"):
                break
                
        except Exception as e:
            print(f"\n    ERROR at offset {offset}: {e}")
            time.sleep(3)
            continue
    
    if not dry_run:
        conn.close()
    
    print(f"\n  Total: {total_count} parcels for {county_name}")
    return total_count


def summary():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'arc_parcels_%'")
    tables = cur.fetchall()
    print(f"\n{'='*60}")
    print("ARC Parcels Summary:")
    total = 0
    for (t,) in tables:
        cur = conn.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt:,} records")
        total += cnt
    print(f"  Total: {total:,} records")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Download ARC region tax parcels")
    parser.add_argument("--counties", nargs="+", default=list(FEATURE_SERVERS.keys()),
                        help="Counties to download (default: all available)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be downloaded without storing")
    parser.add_argument("--summary", action="store_true",
                        help="Show summary of downloaded data")
    args = parser.parse_args()
    
    if args.summary:
        summary()
        return
    
    total_all = 0
    for county in args.counties:
        if county not in FEATURE_SERVERS:
            print(f"Skipping {county}: no FeatureServer URL configured")
            continue
        cnt = download_county(county, FEATURE_SERVERS[county], args.dry_run)
        total_all += cnt
    
    print(f"\n{'='*60}")
    print(f"Total parcels downloaded: {total_all:,}")
    
    if not args.dry_run and total_all > 0:
        summary()


if __name__ == "__main__":
    main()
