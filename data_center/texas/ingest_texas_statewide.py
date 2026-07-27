"""
Directive 63: Texas Statewide Parcel Ingest via ArcGIS StratMap Feature Service

Downloads the 2019 Texas Parcels StratMap filtering to >= 20 acres
with non-trivial owner names. Stores in deals.db.

Strategy: Parallel OBJECTID-range queries with Shape__Area filter on server side.
OBJECTIDs [1, 17.7M] split into 177 chunks of 100K. Each chunk paginated in parallel.
"""

import json
import sqlite3
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from shapely.geometry import shape as shapely_shape

DB_PATH = Path(__file__).parent.parent / "deals.db"
TABLE = "raw_tx_stratmap_parcels"
BASE_URL = "https://services1.arcgis.com/1mtXwieMId59thmg/arcgis/rest/services/2019_Texas_Parcels_StratMap/FeatureServer/0"

BATCH_SIZE = 2000
MIN_ACRES = 20
MAX_WORKERS = 8
SHAPE_AREA_THRESHOLD = 7e-6
MAX_OBJECTID = 17699595
CHUNK_SIZE = 100000


def ensure_table(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            objectid INTEGER UNIQUE,
            owner_name TEXT,
            acres REAL,
            lat REAL,
            lng REAL,
            geometry_geojson TEXT
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_acres ON {TABLE}(acres)")
    conn.commit()


def fetch_page(oid_start, oid_end, offset):
    where = f"Shape__Area > {SHAPE_AREA_THRESHOLD} AND OBJECTID BETWEEN {oid_start} AND {oid_end}"
    params = {
        "where": where,
        "resultOffset": offset,
        "resultRecordCount": BATCH_SIZE,
        "returnGeometry": "true",
        "outFields": "OBJECTID,OWNER_NAME,GIS_AREA",
        "f": "geojson",
    }
    r = requests.get(f"{BASE_URL}/query", params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_page_with_retry(oid_start, oid_end, offset, max_retries=5):
    for attempt in range(max_retries):
        try:
            return fetch_page(oid_start, oid_end, offset)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in (429, 503, 502) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 3)  # 8, 16, 32, 64, 128
                print(f"  (retry {attempt+1}/{max_retries} for OID {oid_start}-{oid_end} offset {offset}, waiting {wait}s)")
                time.sleep(wait)
            else:
                raise


def process_features(features):
    rows = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        if not geom:
            continue
        owner = (props.get("OWNER_NAME") or "").strip()
        if not owner:
            continue
        try:
            acres = float(props.get("GIS_AREA", 0) or 0)
        except (TypeError, ValueError):
            acres = 0
        if acres < MIN_ACRES:
            continue
        try:
            shape = shapely_shape(geom)
            centroid = shape.centroid
            geom_json = json.dumps(geom)
        except Exception:
            continue
        rows.append((
            props["OBJECTID"],
            owner,
            acres,
            round(centroid.y, 6),
            round(centroid.x, 6),
            geom_json,
        ))
    return rows


def download_objectid_range(oid_start, oid_end):
    offset = 0
    all_rows = []
    while True:
        data = fetch_page_with_retry(oid_start, oid_end, offset)
        features = data.get("features", [])
        if not features:
            break
        rows = process_features(features)
        all_rows.extend(rows)
        if not data.get("exceededTransferLimit", False):
            break
        offset += BATCH_SIZE
    return oid_start, all_rows


def main():
    conn = sqlite3.connect(str(DB_PATH))
    ensure_table(conn)

    # Find last OBJECTID already in DB
    cur = conn.cursor()
    cur.execute(f"SELECT COALESCE(MAX(objectid), 0) FROM {TABLE}")
    last_oid = cur.fetchone()[0]
    print(f"Last OBJECTID in DB: {last_oid:,}")

    # Build OBJECTID chunks starting from last_oid + 1
    chunks = []
    s = last_oid + 1
    while s <= MAX_OBJECTID:
        e = min(s + CHUNK_SIZE - 1, MAX_OBJECTID)
        chunks.append((s, e))
        s = e + 1
    print(f"Chunks to process: {len(chunks)} (OBJECTID {last_oid:,} to {MAX_OBJECTID:,})")

    inserted = 0
    t0 = time.time()
    completed = 0
    total_chunks = len(chunks)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_objectid_range, s, e): (s, e) for s, e in chunks}

        for future in as_completed(futures):
            oid_start, rows = future.result()
            completed += 1

            if rows:
                conn.executemany(
                    f"INSERT OR IGNORE INTO {TABLE} "
                    f"(objectid, owner_name, acres, lat, lng, geometry_geojson) "
                    f"VALUES (?,?,?,?,?,?)",
                    rows
                )
                conn.commit()
                inserted += len(rows)

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            if completed <= 20 or completed % 25 == 0 or completed == total_chunks:
                print(
                    f"  {completed}/{total_chunks} chunks ({elapsed:.0f}s, {rate:.1f}/s) "
                    f"inserted={inserted:,}"
                )

    elapsed = time.time() - t0
    print(f"\nDone: {inserted:,} parcels >= {MIN_ACRES}ac in {elapsed:.0f}s")

    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    print(f"Total in {TABLE}: {cur.fetchone()[0]:,}")

    cur.execute(f"SELECT MIN(acres), AVG(acres), MAX(acres) FROM {TABLE}")
    r = cur.fetchone()
    print(f"Acreage: {r[0]:.0f} – {r[1]:.0f} avg – {r[2]:,.0f} max")

    conn.close()


if __name__ == "__main__":
    main()
