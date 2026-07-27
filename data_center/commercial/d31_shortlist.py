"""
D31: The HIFLD Power Ingest — Transmission Line Proximity
Filter A-Tier Big Blocks (≥100ac, score_tier='A') for those within 1.0mi of a 230kV+ transmission line.
Creates shortlist and updates DB with score_transmission column.
"""
import json
import math
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "deals.db"
INFRA = Path(__file__).parent.parent / "infrastructure.db"
SHORTLIST_PATH = Path(__file__).parent.parent / "data/shortlist_d31.csv"

# 1.0 mile in degrees (approx)
MILE_DEG = 1.0 / 69.0


def point_to_line_dist(px, py, line_coords):
    """Minimum distance from point (px,py) to a LineString."""
    min_d = float("inf")
    for i in range(len(line_coords) - 1):
        x1, y1 = line_coords[i]
        x2, y2 = line_coords[i + 1]
        d = _point_segment_dist(px, py, x1, y1, x2, y2)
        if d < min_d:
            min_d = d
    return math.sqrt(min_d)


def _point_segment_dist(px, py, x1, y1, x2, y2):
    """Squared distance from point to line segment."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    if t < 0:
        return (px - x1) ** 2 + (py - y1) ** 2
    elif t > 1:
        return (px - x2) ** 2 + (py - y2) ** 2
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2)


def load_hv_lines():
    """Load HV transmission lines from infrastructure.db."""
    conn = sqlite3.connect(str(INFRA))
    cur = conn.execute(
        "SELECT voltage, geometry_geojson FROM transmission_lines WHERE voltage >= 230"
    )
    lines = []
    for voltage, geom_json in cur.fetchall():
        try:
            geom = json.loads(geom_json)
        except Exception:
            continue
        if geom["type"] == "LineString":
            coords = geom["coordinates"]
            # Bounding box for fast filtering
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            lines.append((voltage, coords, bbox))
        elif geom["type"] == "MultiLineString":
            for part in geom["coordinates"]:
                xs = [c[0] for c in part]
                ys = [c[1] for c in part]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                lines.append((voltage, part, bbox))
    conn.close()
    return lines


def main():
    print("Loading HV transmission lines...")
    hv_lines = load_hv_lines()
    print(f"  {len(hv_lines)} 230kV+ line segment(s) loaded")

    print("Loading A-Tier Big Blocks...")
    conn = sqlite3.connect(str(DB))
    cur = conn.execute(
        """SELECT rowid, address, county, owner_name, lat, lng,
                  CAST(acres AS REAL) as ac, score_substation, score_fiber
           FROM commercial_sites
           WHERE score_tier = 'A' AND CAST(acres AS REAL) >= 100
             AND lat IS NOT NULL AND lng IS NOT NULL
           ORDER BY CAST(acres AS REAL) DESC"""
    )
    sites = cur.fetchall()
    print(f"  {len(sites)} site(s) to check")

    results = []
    for idx, (rowid, address, county, owner, lat, lng, acres, sub_score, fiber_score) in enumerate(
        sites
    ):
        min_dist_mi = float("inf")
        closest_voltage = 0

        # Quick bbox filter: only check lines within ~2 miles of the site
        bbox_lat_min = lat - 2 * MILE_DEG
        bbox_lat_max = lat + 2 * MILE_DEG
        bbox_lon_min = lng - 2 * MILE_DEG
        bbox_lon_max = lng + 2 * MILE_DEG

        for voltage, coords, (bx1, by1, bx2, by2) in hv_lines:
            # Bounding box overlap check
            if bx2 < bbox_lon_min or bx1 > bbox_lon_max:
                continue
            if by2 < bbox_lat_min or by1 > bbox_lat_max:
                continue

            d_deg = point_to_line_dist(lng, lat, coords)
            d_mi = d_deg / MILE_DEG
            if d_mi < min_dist_mi:
                min_dist_mi = d_mi
                closest_voltage = voltage
                if min_dist_mi < 0.1:  # Early exit if very close
                    break

        within_1mi = min_dist_mi <= 1.0
        results.append(
            (rowid, address, county, owner, acres, min_dist_mi, closest_voltage, within_1mi)
        )

        if (idx + 1) % 100 == 0:
            print(f"  Processed {idx + 1}/{len(sites)}...")

    # Add transmission score column
    try:
        conn.execute(
            "ALTER TABLE commercial_sites ADD COLUMN score_transmission INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE commercial_sites ADD COLUMN distance_transmission_miles REAL"
        )
    except sqlite3.OperationalError:
        pass

    # Update DB
    upd = conn.cursor()
    for rowid, *_, dist_mi, voltage, within in results:
        score = 0
        if within:
            if dist_mi <= 0.25:
                score = 35
            elif dist_mi <= 0.5:
                score = 25
            elif dist_mi <= 1.0:
                score = 15
        upd.execute(
            "UPDATE commercial_sites SET score_transmission = ?, distance_transmission_miles = ? WHERE rowid = ?",
            (score, round(dist_mi, 3), rowid),
        )
    conn.commit()

    # Print shortlist
    shortlist = [r for r in results if r[7]]
    shortlist.sort(key=lambda r: r[4], reverse=True)  # sort by acres desc

    print(f"\n=== D31 SHORTLIST: {len(shortlist)} sites within 1.0mi of 230kV+ line ===\n")
    print(f"{'Acres':>8s}  {'County':12s}  {'Dist(mi)':8s}  {'Owner':40s}")
    print("-" * 72)
    for r in shortlist[:50]:
        print(f"{r[4]:>8.1f}  {r[2]:12s}  {r[5]:>7.2f}   {r[3][:40]:40s}")
    if len(shortlist) > 50:
        print(f"... and {len(shortlist) - 50} more")

    # Save full shortlist to CSV
    with open(SHORTLIST_PATH, "w") as f:
        f.write("rowid,address,county,owner,acres,dist_mi,voltage_kv\n")
        for r in shortlist:
            f.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]:.1f},{r[5]:.3f},{r[6]:.0f}\n")
    print(f"\nFull shortlist saved to {SHORTLIST_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
