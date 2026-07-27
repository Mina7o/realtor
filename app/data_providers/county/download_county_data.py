"""
Download county tax parcel data from known public sources.
Currently supports NC OneMap ArcGIS REST API for bulk export.
"""
import argparse
import csv
import json
import os
import sys
import time
import requests
from db import upsert_county, upsert_property, upsert_tax_record, get_conn

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

# NC OneMap field to our column mapping
FIELD_MAP = {
    'siteadd': 'address',
    'scity': 'city',
    'sstate': 'state',
    'szip': 'zip',
    'parval': 'mkt_val_total',
    'landval': 'mkt_val_land',
    'improvval': 'mkt_val_building',
    'saledate': 'sale_date',
    'saledatetx': 'sale_date_tx',
    'gisacres': 'gis_acres',
    'ownname': 'owner',
    'yearbuilt': 'year_built',
}

def fetch_esri_layer(url, where, out_fields, max_records=None):
    """Fetch all records from an Esri FeatureServer/MapServer layer using pagination."""
    offset = 0
    batch_size = 2000
    all_features = []
    total_fetched = None

    while True:
        params = {
            'where': where,
            'outFields': out_fields,
            'returnGeometry': 'false',
            'f': 'json',
            'resultOffset': offset,
            'resultRecordCount': batch_size,
        }
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                break
            data = resp.json()
        except Exception as e:
            print(f"  Request error: {e}")
            break

        if 'error' in data:
            print(f"  API error: {data['error']}")
            break

        features = data.get('features', [])
        if not features:
            break

        all_features.extend(features)
        offset += len(features)
        if total_fetched is None:
            total_fetched = data.get('total', len(features))
            if total_fetched and total_fetched > len(features):
                print(f"  Total records: {total_fetched}")

        print(f"  Fetched {len(all_features)} records...")
        if len(features) < batch_size:
            break
        if max_records and len(all_features) >= max_records:
            break
        time.sleep(0.5)

    return all_features

def parse_arcgis_date(val):
    """ArcGIS dates are ms since epoch"""
    if val is None:
        return None
    from datetime import datetime
    try:
        return datetime.utcfromtimestamp(val / 1000).strftime('%Y-%m-%d')
    except:
        return None

def load_nconemap_county(state, cntyname, max_records=None):
    """Download parcel data from NC OneMap for a specific county."""
    # Use the MapServer endpoint (public, no auth needed)
    url = "https://services.nconemap.gov/secure/rest/services/NC1Map_Parcels/MapServer/1/query"

    # First try with cntyname filter
    where = f"UPPER(cntyname) LIKE UPPER('%{cntyname}%')"
    # Also try with cntyfips
    print(f"Querying NC OneMap for {cntyname} County...")
    out_fields = ','.join([
        'siteadd', 'scity', 'sstate', 'szip',
        'parval', 'landval', 'improvval',
        'saledate', 'ownname', 'gisacres',
        'yearbuilt'
    ])

    # Try with simpler fields if that fails
    features = fetch_esri_layer(url, where, '*', max_records)

    if not features:
        # Try alternate filter: use cntyfips
        print("  Trying alternate query...")
        # Mecklenburg FIPS: 37119
        fips_map = {
            'Mecklenburg': '37119',
            'Wake': '37183',
            'Guilford': '37081',
            'Forsyth': '37067',
            'Buncombe': '37021',
        }
        fips = fips_map.get(cntyname, '')
        if fips:
            where = f"cntyfips='{fips}'"
            features = fetch_esri_layer(url, where, '*', max_records)

    if not features:
        print(f"  Could not fetch data for {cntyname} County.")
        print("  The NC OneMap service may require authentication for secure/rest endpoints.")
        print("  Try downloading directly from: https://www.nconemap.gov/pages/parcels")
        return 0

    # Process and store
    county_id = upsert_county(cntyname, state)
    count = 0
    errors = 0

    for feat in features:
        try:
            attrs = feat.get('attributes', {})
            addr = (attrs.get('siteadd') or '').strip()
            city = (attrs.get('scity') or '').strip()
            st = (attrs.get('sstate') or '').strip() or state
            zip_code = str(attrs.get('szip') or '').strip()
            if not addr or not city:
                errors += 1
                continue

            pid = upsert_property(addr, city, st, zip_code, county_id)

            parval = attrs.get('parval')
            landval = attrs.get('landval')
            improvval = attrs.get('improvval')
            sale_date = parse_arcgis_date(attrs.get('saledate'))
            year_built = attrs.get('yearbuilt')

            upsert_tax_record(
                property_id=pid,
                tax_year=2025,
                mkt_val_total=float(parval) if parval else None,
                mkt_val_land=float(landval) if landval else None,
                mkt_val_building=float(improvval) if improvval else None,
                sale_date=sale_date,
                year_built=int(year_built) if year_built else None,
            )
            count += 1
        except Exception as e:
            errors += 1

    print(f"  Loaded {count} records ({errors} errors/skipped)")
    return count

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="NC", help="State code")
    parser.add_argument("--county", help="County name (default: Mecklenburg)")
    parser.add_argument("--max", type=int, help="Max records to fetch")
    args = parser.parse_args()

    county = args.county or "Mecklenburg"
    print(f"Downloading tax data for {county} County, {args.state}...")
    count = load_nconemap_county(args.state, county, args.max)

    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM tax_records").fetchone()[0]
    print(f"\nTotal tax records in database: {total}")

if __name__ == "__main__":
    main()
