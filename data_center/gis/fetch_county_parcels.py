"""Fetch parcel data for target counties from available sources.

For NC:
  - Direct county ArcGIS server (if available) — richer data
  - NC OneMap fallback — statewide coverage

For other states:
  - TBD (ArcGIS, state portals, commercial data)
"""

import argparse
import sys
import os
import time
import sqlite3
import json
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

PAGE_SIZE = 2000

NC_ONEMAP_URL = (
    "https://services.nconemap.gov/secure/rest/services/"
    "NC1Map_Parcels/MapServer/0/query"
)

NC_ONEMAP_FIELDS = [
    "objectid", "parno", "ownname", "ownname2",
    "siteadd", "scity", "sstate", "szip",
    "gisacres", "parval", "landval", "improvval",
    "parusecode", "parusedesc",
    "saledate", "sourceref",
    "cntyname", "cntyfips",
]

NC_ONEMAP_FIELD_TYPES = {
    "objectid": "INTEGER",
    "parno": "TEXT",
    "ownname": "TEXT",
    "ownname2": "TEXT",
    "siteadd": "TEXT",
    "scity": "TEXT",
    "sstate": "TEXT",
    "szip": "TEXT",
    "gisacres": "REAL",
    "parval": "REAL",
    "landval": "REAL",
    "improvval": "REAL",
    "parusecode": "TEXT",
    "parusedesc": "TEXT",
    "saledate": "TEXT",
    "sourceref": "TEXT",
    "cntyname": "TEXT",
    "cntyfips": "TEXT",
}


def get_db_path():
    return os.path.join(SCRIPT_DIR, "..", "deals.db")


def ensure_raw_table(conn, table_name):
    """Create a standardized raw parcel table if not exists."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            source_oid TEXT PRIMARY KEY,
            source_county TEXT,
            source_state TEXT,
            source_name TEXT,
            parno TEXT,
            ownname TEXT,
            ownname2 TEXT,
            siteadd TEXT,
            scity TEXT,
            sstate TEXT,
            szip TEXT,
            gisacres REAL,
            parval REAL,
            landval REAL,
            improvval REAL,
            parusecode TEXT,
            parusedesc TEXT,
            saledate TEXT,
            sourceref TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_acres ON {table_name}(gisacres)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_own ON {table_name}(ownname)")


def fetch_nconemap_county(cntyname, min_acres=0, max_records=None):
    """Fetch parcels from NC OneMap for a single county.

    Returns list of dicts with NC_ONEMAP_FIELDS keys.
    """
    where = f"UPPER(cntyname) = '{cntyname.upper()}'"
    if min_acres > 0:
        where += f" AND gisacres >= {min_acres}"

    out_fields = ",".join(NC_ONEMAP_FIELDS)

    all_features = []
    offset = 0
    errors = 0

    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        try:
            r = requests.get(NC_ONEMAP_URL, params=params, timeout=120)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:200]}")
                break
            data = r.json()
        except Exception as e:
            errors += 1
            print(f"  Request error: {e}")
            if errors > 5:
                break
            time.sleep(3)
            continue

        if "error" in data:
            print(f"  API error: {data['error']}")
            break

        features = data.get("features", [])
        if not features:
            break

        for f in features:
            a = f.get("attributes", {})
            row = {k: a.get(k) for k in NC_ONEMAP_FIELDS}
            row["_gisacres_raw"] = a.get("gisacres")
            all_features.append(row)

        offset += len(features)
        pct = min(100, offset * 100 / max(1, data.get("count", offset)))
        print(f"  Fetched {len(all_features)} records...", end="\r")

        if len(features) < PAGE_SIZE:
            break
        if max_records and len(all_features) >= max_records:
            break

        time.sleep(0.3)

    print()
    return all_features


def store_parcels(conn, table_name, county, state, features, source_name=None):
    """Bulk insert features into the raw parcels table."""
    rows = []
    src = source_name or f"nconemap_{county}"
    for feat in features:
        oid = feat.get("objectid")
        if oid is None:
            continue
        source_oid = f"{county}_{oid}"

        raw_attrs = feat.pop("_raw_source", None)
        raw_json = json.dumps(raw_attrs or feat)

        rows.append((
            source_oid, county, state, src,
            feat.get("parno"),
            feat.get("ownname"),
            feat.get("ownname2"),
            feat.get("siteadd"),
            feat.get("scity"),
            feat.get("sstate") or state,
            feat.get("szip"),
            feat.get("gisacres"),
            feat.get("parval"),
            feat.get("landval"),
            feat.get("improvval"),
            feat.get("parusecode"),
            feat.get("parusedesc"),
            feat.get("saledate"),
            feat.get("sourceref"),
            raw_json,
        ))

    sql = f"""
        INSERT OR REPLACE INTO {table_name}
        (source_oid, source_county, source_state, source_name,
         parno, ownname, ownname2,
         siteadd, scity, sstate, szip,
         gisacres, parval, landval, improvval,
         parusecode, parusedesc, saledate, sourceref, raw_json)
        VALUES (?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?, ?)
    """
    cursor = conn.cursor()
    cursor.executemany(sql, rows)
    conn.commit()
    return len(rows)


def discover_fields(layer_url):
    """Get field names from an ArcGIS layer."""
    try:
        r = requests.get(f"{layer_url}?f=json", timeout=15)
        data = r.json()
        return [f["name"] for f in data.get("fields", [])]
    except:
        return []


def fetch_arcgis_layer(layer_url, where="1=1", out_fields="*", min_acres=0, acres_field="CALC_ACRES"):
    """Fetch features from an ArcGIS MapServer/FeatureServer layer.

    Tries to filter by minimum acres if the field exists.
    """
    if min_acres > 0:
        try:
            where_test = f"{acres_field} >= {min_acres}"
            params = {"where": where_test, "returnCountOnly": "true", "f": "json"}
            r = requests.get(f"{layer_url}/query", params=params, timeout=15)
            if r.status_code == 200 and "count" in r.json():
                where = where_test
        except:
            pass

    all_features = []
    offset = 0
    errors = 0

    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        try:
            r = requests.get(f"{layer_url}/query", params=params, timeout=120)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception as e:
            errors += 1
            if errors > 5:
                break
            time.sleep(3)
            continue

        if "error" in data:
            break

        features = data.get("features", [])
        if not features:
            break

        for f in features:
            all_features.append(f.get("attributes", {}))

        offset += len(features)
        print(f"  Fetched {len(all_features)} records...", end="\r")

        if len(features) < PAGE_SIZE:
            break
        time.sleep(0.3)

    print()
    return all_features


ARCGIS_FIELD_MAP = {
    "OWNER1": "ownname",
    "OWNER": "ownname",
    "full_owner_name": "ownname",
    "CURR_NAME1": "ownname",
    "OWNER1_LAST": "ownname",
    "OWNNAME": "ownname",
    "ownname": "ownname",
    "OWNNAME1": "ownname",
    "parno": "parno",
    "PIN": "parno",
    "pid": "parno",
    "PID": "parno",
    "siteadd": "siteadd",
    "situsaddress1": "siteadd",
    "PHYSSTRADD": "siteadd",
    "SITEADD": "siteadd",
    "ADDRESS1": "siteadd",
    "scity": "scity",
    "SCITY": "scity",
    "CURR_CITY": "scity",
    "CITY": "scity",
    "gisacres": "gisacres",
    "GISACRES": "gisacres",
    "CALC_ACRES": "gisacres",
    "num_totalac": "gisacres",
    "mapped_acres": "gisacres",
    "SIZE": "gisacres",
    "parval": "parval",
    "PARVAL": "parval",
    "amt_totalvalue": "parval",
    "FMV_TOTAL": "parval",
    "VALUATION": "parval",
    "landval": "landval",
    "LANDVALUE": "landval",
    "amt_landvalue": "landval",
    "FMV_LAND": "landval",
    "improvval": "improvval",
    "BLDGVALUE": "improvval",
    "amt_netbldgvalue": "improvval",
    "FMV_IMPRV": "improvval",
    "parusecode": "parusecode",
    "parusedesc": "parusedesc",
    "Zonings": "parusedesc",
    "PROPERTY_USE": "parusedesc",
    "txt_propertyuse_desc": "parusedesc",
    "property_use": "parusedesc",
    "cntyname": "cntyname",
    "COUNTYNAME": "cntyname",
}


def normalize_arcgis_record(attrs, county, state, source_name):
    """Map ArcGIS field names to our standard schema."""
    mapped = {
        "ownname": None, "ownname2": None, "parno": None,
        "siteadd": None, "scity": None, "sstate": state, "szip": None,
        "gisacres": None, "parval": None, "landval": None, "improvval": None,
        "parusecode": None, "parusedesc": None, "saledate": None, "sourceref": None,
        "cntyname": county, "cntyfips": None,
        "objectid": attrs.get("OBJECTID") or attrs.get("objectid"),
    }
    for arc_name, our_name in ARCGIS_FIELD_MAP.items():
        if arc_name in attrs and attrs[arc_name] is not None:
            val = attrs[arc_name]
            if our_name == "gisacres" and isinstance(val, (int, float)) and val > 1e9:
                continue
            if our_name == "gisacres" and isinstance(val, (int, float)) and val < 0:
                continue
            if mapped[our_name] is None:
                mapped[our_name] = val

    if mapped["ownname"] is None:
        parts = []
        for k in ["OWNER1_LAST", "OWNER1_FIRST", "ownname2"]:
            if k in attrs and attrs[k]:
                parts.append(str(attrs[k]))
        if parts:
            mapped["ownname"] = " ".join(parts)

    return mapped


def transform_acres(val):
    """Handle the Mecklenburg num_totalac is actually sqft quirk."""
    if val is None:
        return None
    try:
        v = float(val)
        return v
    except:
        return None


def fetch_county(county, state, min_acres=10, force=False, source=None):
    """Fetch parcel data for a single county.

    Returns (table_name, count) or (None, 0) on failure.
    """
    table_name = f"raw_{county.lower().replace(' ','_')}_{state.lower()}_parcels"
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    ensure_raw_table(conn, table_name)
    conn.close()

    # Check county_config for direct ArcGIS URL
    try:
        from data_center.gis.county_config import TARGET_COUNTIES
        county_key = f"{county.upper()}_{state.upper()}"
        if county_key not in TARGET_COUNTIES:
            county_key = county.upper()
        cfg = TARGET_COUNTIES.get(county_key, {})
        arcgis_url = cfg.get("arcgis_url")
    except ImportError:
        arcgis_url = None

    if arcgis_url and (source is None or source == "arcgis"):
        print(f"  Source: Direct ArcGIS ({arcgis_url})")
        fields = discover_fields(arcgis_url)
        acres_field = "CALC_ACRES" if "CALC_ACRES" in fields else "gisacres" if "gisacres" in fields else "SIZE"
        features = fetch_arcgis_layer(arcgis_url, min_acres=min_acres, acres_field=acres_field)

        if not features:
            print(f"  No data from ArcGIS")
            return None, 0

        mapped = []
        for attrs in features:
            m = normalize_arcgis_record(attrs, county, state, f"arcgis_{county}")
            m["objectid"] = attrs.get("OBJECTID") or attrs.get("objectid")
            m["_raw_source"] = attrs
            mapped.append(m)

        conn = sqlite3.connect(db_path)
        stored = store_parcels(conn, table_name, county, state, mapped, source_name=f"arcgis_{county}")
        conn.close()
        print(f"  Stored {stored} parcels in {table_name}")
        return table_name, stored

    if source == "nconemap" or (source is None and state == "NC"):
        print(f"  Source: NC OneMap")
        features = fetch_nconemap_county(county, min_acres=min_acres)
        if not features:
            print(f"  No data from NC OneMap")
            return None, 0

        conn = sqlite3.connect(db_path)
        stored = store_parcels(conn, table_name, county, state, features)
        conn.close()
        print(f"  Stored {stored} parcels in {table_name}")
        return table_name, stored

    else:
        print(f"  No data source available for {county}, {state}")
        return None, 0


def summary():
    """Print summary of all raw parcel tables."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'raw_%' ORDER BY name")
    tables = cursor.fetchall()
    print(f"\n=== Raw Parcel Tables ({len(tables)} total) ===\n")
    for (tname,) in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {tname}")
        count = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM {tname} WHERE gisacres >= 10 AND siteadd IS NOT NULL AND siteadd != ''")
        valid = cursor.fetchone()[0]
        cursor.execute(f"SELECT ROUND(AVG(gisacres), 1) FROM {tname} WHERE gisacres > 0")
        avg_ac = cursor.fetchone()[0] or 0
        print(f"  {tname:<45s} {count:>8,} total  {valid:>8,} w/addr>=10ac  avg {avg_ac:>8.1f}ac")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", help="County name (required)")
    parser.add_argument("--state", default="NC", help="State code")
    parser.add_argument("--min-acres", type=float, default=10, help="Minimum acres filter")
    parser.add_argument("--source", choices=["nconemap", "arcgis"], help="Force a specific data source")
    parser.add_argument("--summary", action="store_true", help="Show table summary")
    parser.add_argument("--batch", help="JSON file with list of counties to fetch")
    args = parser.parse_args()

    if args.summary:
        summary()
        return

    if args.batch:
        with open(args.batch) as f:
            batch = json.load(f)
        for item in batch:
            county = item.get("county")
            state = item.get("state", "NC")
            ma = item.get("min_acres", 10)
            src = item.get("source")
            print(f"\n{'='*60}")
            print(f"  County: {county}, {state}")
            print(f"{'='*60}")
            table, count = fetch_county(county, state, min_acres=ma, source=src)
            if count:
                print(f"  Done: {count} parcels -> {table}")
            else:
                print(f"  Failed or no data for {county}")
        print("\n=== Batch complete ===")
        summary()
        return

    if not args.county:
        parser.print_help()
        return

    print(f"Fetching parcels for {args.county}, {args.state}")
    print(f"  Min acres: {args.min_acres}")
    table, count = fetch_county(args.county, args.state, min_acres=args.min_acres, source=args.source)
    if count:
        print(f"\nDone: {count} parcels -> {table}")
    else:
        print(f"\nNo data fetched")


if __name__ == "__main__":
    main()
