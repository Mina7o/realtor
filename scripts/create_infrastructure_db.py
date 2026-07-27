"""
Directive 31: HIFLD Power Grid Ingest
Load national transmission lines and substations into infrastructure.db
for local distance calculations instead of external ArcGIS calls.
"""

import json
import sqlite3
import argparse
from pathlib import Path
import numpy as np
from shapely.geometry import shape, Point
from shapely.ops import nearest_points
from shapely.strtree import STRtree
import pyproj

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = Path(__file__).parent / "infrastructure.db"
STATES_FILE = DATA_DIR / "us_states.geojson"

SUBS_FILE = DATA_DIR / "hifld_substations.geojson"
TRANS_FILE = DATA_DIR / "hifld_transmission.geojson"


def load_geojson(path):
    if not path.exists():
        print(f"  File not found: {path}")
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("features", [])


def load_state_boundaries():
    """Load US state boundaries and build a spatial index for point-in-state lookups."""
    if not STATES_FILE.exists():
        print(f"  ERROR: States boundary file not found: {STATES_FILE}")
        print("  Download from: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_1_states_provinces.geojson")
        return None, None

    with open(STATES_FILE) as f:
        data = json.load(f)

    state_shapes = []
    state_abbrevs = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        if props.get("iso_a2") != "US":
            continue
        abbrev = props.get("postal") or ""
        if not abbrev:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            poly = shape(geom)
            state_shapes.append(poly)
            state_abbrevs.append(abbrev)
        except Exception:
            continue

    tree = STRtree(state_shapes)
    print(f"  Loaded {len(state_abbrevs)} US state boundaries")
    return tree, (state_shapes, state_abbrevs)


def resolve_state(lng, lat, tree, state_index):
    """Return 2-letter state abbreviation for a coordinate, or empty string."""
    state_shapes, state_abbrevs = state_index
    pt = Point(lng, lat)
    indices = tree.query(pt)
    for i in indices:
        if state_shapes[i].contains(pt):
            return state_abbrevs[i]
    # Fallback: nearest geometry
    nearest = tree.nearest(pt)
    if nearest is not None:
        return state_abbrevs[nearest]
    return ""


def create_substations_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            name TEXT,
            type TEXT,
            status TEXT,
            max_volt REAL,
            min_volt REAL,
            lines INTEGER,
            city TEXT,
            state TEXT,
            county TEXT,
            countyfips TEXT,
            latitude REAL,
            longitude REAL,
            source TEXT,
            source_date TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sub_state ON substations(state)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sub_volt ON substations(max_volt)
    """)


def create_transmission_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transmission_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            type TEXT,
            status TEXT,
            voltage REAL,
            volt_class TEXT,
            owner TEXT,
            source TEXT,
            source_date TEXT,
            state TEXT,
            geometry_geojson TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trans_state_volt ON transmission_lines(state, voltage)
    """)


def ingest_substations(conn, features):
    cur = conn.cursor()
    count = 0
    for f in features:
        props = f.get("properties", {})
        attrs = f.get("attributes", props)

        if isinstance(attrs, dict) and "attributes" in attrs:
            attrs = attrs["attributes"]

        lat = attrs.get("LATITUDE") or attrs.get("latitude") or attrs.get("Latitude")
        lon = attrs.get("LONGITUDE") or attrs.get("longitude") or attrs.get("Longitude")

        if lat is None or lon is None:
            continue

        max_v = attrs.get("MAX_VOLT") or attrs.get("max_volt") or attrs.get("MAXVOLT")
        if max_v is not None:
            try:
                max_v = float(max_v)
            except (TypeError, ValueError):
                max_v = None

        min_v = attrs.get("MIN_VOLT") or attrs.get("min_volt")
        if min_v is not None:
            try:
                min_v = float(min_v)
            except (TypeError, ValueError):
                min_v = None

        lines = attrs.get("LINES") or attrs.get("lines")
        if lines is not None:
            try:
                lines = int(float(lines))
            except (TypeError, ValueError):
                lines = None

        cur.execute("""
            INSERT OR IGNORE INTO substations
            (source_id, name, type, status, max_volt, min_volt, lines,
             city, state, county, countyfips, latitude, longitude, source, source_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(attrs.get("ID", "")),
            str(attrs.get("NAME", "") or ""),
            str(attrs.get("TYPE", "") or ""),
            str(attrs.get("STATUS", "") or ""),
            max_v,
            min_v,
            lines,
            str(attrs.get("CITY", "") or ""),
            str(attrs.get("STATE", "") or ""),
            str(attrs.get("COUNTY", "") or ""),
            str(attrs.get("COUNTYFIPS", "") or str(attrs.get("CNTYFIPS", "") or "")),
            float(lat),
            float(lon),
            str(attrs.get("SOURCE", "") or ""),
            str(attrs.get("SOURCEDATE", "") or ""),
        ))
        count += 1
    conn.commit()
    return count


def ingest_transmission(conn, features, state_tree, state_index):
    cur = conn.cursor()
    count = 0
    for f in features:
        props = f.get("properties", {})
        voltage = props.get("VOLTAGE") or props.get("voltage") or props.get("VOLT_CLASS")
        if voltage is not None:
            try:
                voltage = float(voltage)
            except (TypeError, ValueError):
                voltage = None

        geom = f.get("geometry")
        geom_json = json.dumps(geom) if geom else None

        # Resolve state from first coordinate of the line
        state = ""
        if geom and state_tree:
            try:
                if geom["type"] == "LineString":
                    pt = geom["coordinates"][0]
                    state = resolve_state(pt[0], pt[1], state_tree, state_index)
                elif geom["type"] == "MultiLineString":
                    pt = geom["coordinates"][0][0]
                    state = resolve_state(pt[0], pt[1], state_tree, state_index)
            except (IndexError, TypeError, KeyError):
                pass

        cur.execute("""
            INSERT OR IGNORE INTO transmission_lines
            (source_id, type, status, voltage, volt_class, owner,
             source, source_date, state, geometry_geojson)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            str(props.get("ID", "")),
            str(props.get("TYPE", "") or ""),
            str(props.get("STATUS", "") or ""),
            voltage,
            str(props.get("VOLT_CLASS", "") or ""),
            str(props.get("OWNER", "") or ""),
            str(props.get("SOURCE", "") or ""),
            str(props.get("SOURCEDATE", "") or ""),
            state,
            geom_json,
        ))
        count += 1
        if count % 10000 == 0:
            print(f"    ... {count} lines ingested, committing ...")
            conn.commit()
    conn.commit()
    return count


def nearest_substation(lat, lng, cur):
    """Find nearest 230kV+ substation using Euclidean approximation."""
    cur.execute("""
        SELECT name, max_volt, latitude, longitude,
               ((latitude - ?) * (latitude - ?) + (longitude - ?) * (longitude - ?)) AS dist_sq
        FROM substations
        WHERE max_volt >= 230 AND status = 'IN SERVICE'
        ORDER BY dist_sq
        LIMIT 1
    """, (lat, lat, lng, lng))
    return cur.fetchone()


def main():
    parser = argparse.ArgumentParser(description="Create infrastructure.db from HIFLD data")
    parser.add_argument("--rebuild", action="store_true", help="Drop and recreate tables")
    args = parser.parse_args()

    print("Loading state boundaries for spatial lookup...")
    state_tree, state_index = load_state_boundaries()

    print("Loading HIFLD substations...")
    sub_features = load_geojson(SUBS_FILE)
    print(f"  {len(sub_features)} features")

    print("Loading HIFLD transmission lines...")
    trans_features = load_geojson(TRANS_FILE)
    print(f"  {len(trans_features)} features")

    conn = sqlite3.connect(str(DB_PATH))

    if args.rebuild:
        conn.execute("DROP TABLE IF EXISTS substations")
        conn.execute("DROP TABLE IF EXISTS transmission_lines")

    create_substations_table(conn)
    create_transmission_table(conn)

    print("Ingesting substations...")
    sub_count = ingest_substations(conn, sub_features)
    print(f"  {sub_count} substations inserted")

    print("Ingesting transmission lines (tagging state by coordinate)...")
    trans_count = ingest_transmission(conn, trans_features, state_tree, state_index)
    print(f"  {trans_count} transmission lines inserted")

    # Summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM substations")
    total_subs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM substations WHERE max_volt >= 230")
    hv_subs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM substations WHERE state='NC'")
    nc_subs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM substations WHERE state='NC' AND max_volt >= 230")
    nc_hv = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM transmission_lines")
    total_trans = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transmission_lines WHERE state='TX'")
    tx_trans = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transmission_lines WHERE state='TX' AND voltage >= 345")
    tx_hv_trans = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transmission_lines WHERE state='SC'")
    sc_trans = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transmission_lines WHERE state='SC' AND voltage >= 345")
    sc_hv_trans = cur.fetchone()[0]

    print(f"\n{'='*60}")
    print(f"infrastructure.db created at {DB_PATH}")
    print(f"  Total substations: {total_subs:,}")
    print(f"  230kV+ substations: {hv_subs:,}")
    print(f"  NC substations: {nc_subs:,}")
    print(f"  NC 230kV+ substations: {nc_hv:,}")
    print(f"  Total transmission lines: {total_trans:,}")
    print(f"  TX transmission lines: {tx_trans:,}")
    print(f"  TX 345kV+ transmission lines: {tx_hv_trans:,}")
    print(f"  SC transmission lines: {sc_trans:,}")
    print(f"  SC 345kV+ transmission lines: {sc_hv_trans:,}")

    conn.close()


if __name__ == "__main__":
    main()
