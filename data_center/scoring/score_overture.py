"""
Score commercial_sites against Overture Maps land-use data.
Adds score_land_use_proximity (0-10) based on distance to nearest
developed/commercial/industrial land-use polygon centroid.
"""
import json
import sqlite3
import math
from pathlib import Path

DB = Path(__file__).parent.parent / "deals.db"
INFRA = Path(__file__).parent.parent / "infrastructure.db"

# Distance thresholds in degrees (~111km per degree)
# 0.01° ~ 1.1km, 0.002° ~ 220m, 0.001° ~ 110m
SCORING = [
    (0.001, 10),   # within ~110m
    (0.002, 8),    # within ~220m
    (0.005, 5),    # within ~550m
    (0.01, 3),     # within ~1.1km
    (0.02, 1),     # within ~2.2km
]


def main():
    print("Loading overture land-use polygons...")
    conn = sqlite3.connect(str(INFRA))
    cur = conn.execute("SELECT id, geometry_geojson FROM overture_land_use")

    centroids = []
    for row in cur.fetchall():
        try:
            geom = json.loads(row[1])
            if geom["type"] == "Polygon":
                coords = geom["coordinates"][0]
            elif geom["type"] == "MultiPolygon":
                coords = geom["coordinates"][0][0]
            else:
                continue
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            centroids.append((cx, cy))
        except Exception:
            pass
    conn.close()
    print(f"  {len(centroids)} centroid(s) loaded")

    print("Loading commercial sites...")
    conn2 = sqlite3.connect(str(DB))
    cur2 = conn2.execute(
        "SELECT rowid, lng, lat FROM commercial_sites WHERE lat IS NOT NULL AND lng IS NOT NULL"
    )
    sites = cur2.fetchall()
    print(f"  {len(sites)} site(s) to score")

    scores = {}
    for idx, (rowid, lon, lat) in enumerate(sites):
        min_dist = float("inf")
        # Early exit at 0.001° for speed
        for cx, cy in centroids:
            d = math.sqrt((lon - cx) ** 2 + (lat - cy) ** 2)
            if d < min_dist:
                min_dist = d
                if min_dist < 0.001:
                    break

        score = 0
        for threshold, pts in SCORING:
            if min_dist <= threshold:
                score = pts
                break
        scores[rowid] = score

        if (idx + 1) % 2000 == 0:
            print(f"  Scored {idx + 1}/{len(sites)}...")

    # Add column if missing
    try:
        conn2.execute("ALTER TABLE commercial_sites ADD COLUMN score_land_use_proximity INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Update in batches
    conn2.execute("UPDATE commercial_sites SET score_land_use_proximity = 0")
    cur_upd = conn2.cursor()
    for rowid, score in scores.items():
        cur_upd.execute(
            "UPDATE commercial_sites SET score_land_use_proximity = ? WHERE rowid = ?",
            (score, rowid),
        )
    conn2.commit()

    avg = sum(scores.values()) / len(scores) if scores else 0
    distro = {}
    for v in scores.values():
        distro[v] = distro.get(v, 0) + 1

    print(f"\nResults:")
    print(f"  Avg score: {avg:.2f}/10")
    print(f"  Distribution:")
    for k in sorted(distro.keys(), reverse=True):
        print(f"    {k}: {distro[k]}")
    conn2.close()


if __name__ == "__main__":
    main()
