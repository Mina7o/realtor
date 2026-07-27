"""
Directive 65: The "Power-Snap"

Execute the 345kV+ spatial match (D60 logic) against the full 188K-parcel dataset.
Store results in tx_site_interconnects table.
Generate output/d65_texas_sovereign_shortlist.csv.
"""

import csv
import json
import sqlite3
import time
from pathlib import Path

from shapely.geometry import shape as shapely_shape, Point
from rtree import index as rtree_index

DB_PATH = Path(__file__).parent.parent / "deals.db"
INFRA_DB = Path(__file__).parent.parent / "infrastructure.db"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
TABLE = "raw_tx_stratmap_parcels"
INTERCONNECT_TABLE = "tx_site_interconnects"
DEG_TO_MI = 53  # Conservative conversion for Texas (~30°N)


def load_parcels(conn):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT rowid, objectid, owner_name, acres, lat, lng,
               tx_normalized_zoning, zoning_power_synergy
        FROM {TABLE}
        WHERE lat IS NOT NULL AND lng IS NOT NULL
    """)
    parcels = cur.fetchall()
    print(f"Loaded {len(parcels):,} parcels with coordinates")
    return parcels


def load_transmission_lines():
    conn = sqlite3.connect(str(INFRA_DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT rowid, geometry_geojson, voltage, owner
        FROM transmission_lines
        WHERE state = 'TX' AND voltage >= 345
    """)
    lines = cur.fetchall()
    conn.close()
    print(f"Loaded {len(lines):,} TX 345kV+ transmission lines")
    return lines


def ensure_interconnect_table(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {INTERCONNECT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parcel_objectid INTEGER,
            line_rowid INTEGER,
            distance_miles REAL,
            voltage INTEGER,
            owner TEXT,
            UNIQUE(parcel_objectid, line_rowid)
        )
    """)
    conn.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{INTERCONNECT_TABLE}_parcel
        ON {INTERCONNECT_TABLE}(parcel_objectid)
    """)
    conn.commit()


def main():
    conn = sqlite3.connect(str(DB_PATH))
    ensure_interconnect_table(conn)

    parcels = load_parcels(conn)
    lines = load_transmission_lines()

    # Build R-Tree
    t0 = time.time()
    idx = rtree_index.Index()
    line_geoms = {}
    for line_rowid, geom_json, voltage, owner in lines:
        try:
            geom = json.loads(geom_json)
            shape = shapely_shape(geom)
            line_geoms[line_rowid] = (shape, voltage, owner)
            idx.insert(line_rowid, shape.bounds)
        except Exception as e:
            print(f"  Skipping line {line_rowid}: {e}")

    print(f"R-Tree built: {len(line_geoms)} segments in {time.time()-t0:.1f}s")

    # Spatial match
    matches = []
    t0 = time.time()
    for i, (rowid, oid, name, acres, lat, lng, zoning, synergy) in enumerate(parcels):
        pt = Point(lng, lat)
        nearby = list(idx.intersection(pt.bounds))

        best = None
        best_dist = float('inf')
        for line_rowid in nearby:
            item = line_geoms.get(line_rowid)
            if item is None:
                continue
            shape, voltage, owner = item
            dist_deg = pt.distance(shape)
            dist_mi = dist_deg * DEG_TO_MI
            if dist_mi < best_dist:
                best_dist = dist_mi
                best = (line_rowid, voltage, owner)

        if best is not None:
            matches.append((oid, best[0], round(best_dist, 4), best[1], best[2]))

        if (i + 1) % 10000 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  {i+1:,}/{len(parcels):,} ({elapsed:.0f}s, {rate:.0f}/s) — {len(matches):,} matches found")

    elapsed = time.time() - t0
    print(f"Spatial match complete: {len(matches):,} matches in {elapsed:.0f}s")

    # Store in tx_site_interconnects
    conn.executemany(
        f"INSERT OR IGNORE INTO {INTERCONNECT_TABLE} "
        f"(parcel_objectid, line_rowid, distance_miles, voltage, owner) "
        f"VALUES (?,?,?,?,?)",
        matches
    )
    conn.commit()
    print(f"Stored in {INTERCONNECT_TABLE}")

    # ── Generate sovereign shortlist ──
    # Criteria: >= 20ac, within 0.5mi of 345kV+ (alpha-tier)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            p.objectid,
            p.owner_name,
            ROUND(p.acres, 1) as acres,
            ROUND(p.lat, 5) as lat,
            ROUND(p.lng, 5) as lng,
            p.tx_normalized_zoning,
            p.zoning_power_synergy,
            ROUND(MIN(t.distance_miles), 4) as dist_mi,
            t.voltage,
            t.owner as line_owner
        FROM {TABLE} p
        JOIN {INTERCONNECT_TABLE} t ON t.parcel_objectid = p.objectid
        WHERE p.acres >= 20
          AND t.distance_miles <= 0.5
        GROUP BY p.objectid
        ORDER BY t.distance_miles ASC
    """)
    shortlist = cur.fetchall()
    print(f"\nShortlist (within 0.5mi of 345kV+): {len(shortlist):,} parcels")

    # Write CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "d65_texas_sovereign_shortlist.csv"
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'objectid', 'owner_name', 'acres', 'lat', 'lng',
            'zoning', 'zoning_power_synergy', 'dist_345kv_mi',
            'voltage', 'line_owner'
        ])
        for row in shortlist:
            writer.writerow(row)

    print(f"Written to {csv_path}")

    # Summary by zoning
    cur.execute(f"""
        SELECT p.tx_normalized_zoning, COUNT(*), ROUND(AVG(p.acres)),
               ROUND(SUM(p.acres)), ROUND(AVG(t.distance_miles), 3)
        FROM {TABLE} p
        JOIN {INTERCONNECT_TABLE} t ON t.parcel_objectid = p.objectid
        WHERE p.acres >= 20 AND t.distance_miles <= 0.5
        GROUP BY p.tx_normalized_zoning
        ORDER BY COUNT(*) DESC
    """)
    print(f"\n{'Zoning':15s} {'Count':>8s} {'AvgAc':>6s} {'TotalAc':>10s} {'AvgDist':>8s}")
    print('-' * 50)
    for r in cur.fetchall():
        print(f"{r[0] or 'UNK':15s} {r[1]:>8,} {r[2]:>6.0f} {r[3]:>10,.0f} {r[4]:>8.3f}")

    conn.close()


if __name__ == "__main__":
    main()
