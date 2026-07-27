"""
Directive 31: Update scoring to use local infrastructure.db
instead of external ArcGIS calls.

Calculates substation proximity and transmission density
for all commercial_sites using local infrastructure.db.
"""

import math
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "deals.db"
INFRA_DB = Path(__file__).parent.parent / "infrastructure.db"


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_proximity(dist_miles, max_voltage):
    if dist_miles is None or max_voltage is None:
        return 0
    if dist_miles <= 1:
        base = 25
    elif dist_miles <= 3:
        base = 20
    elif dist_miles <= 5:
        base = 15
    elif dist_miles <= 10:
        base = 10
    elif dist_miles <= 20:
        base = 5
    else:
        base = 0
    if max_voltage >= 500:
        base = min(base + 10, 35)
    elif max_voltage >= 345:
        base = min(base + 5, 30)
    return min(base, 35)


def score_transmission_density(lat, lng, infra_conn, radius_miles=5):
    """Count nearby 230kV+ transmission lines within radius."""
    dist_deg = radius_miles / 69.0
    cur = infra_conn.execute("""
        SELECT voltage, geometry_geojson FROM transmission_lines
        WHERE voltage >= 230
          AND geometry_geojson IS NOT NULL
    """)
    
    count = 0
    for row in cur:
        vol = row[0] or 0
        if vol < 230:
            continue
        count += 1
    
    if count > 100:
        return 10
    elif count > 50:
        return 8
    elif count > 20:
        return 5
    elif count > 10:
        return 3
    return 1


def update_site_scoring():
    infra_conn = sqlite3.connect(str(INFRA_DB))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute(
        "SELECT id, lat, lng, substation_distance_miles, score_substation "
        "FROM commercial_sites WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    
    print(f"Scoring {len(rows)} sites against local infrastructure.db...")
    
    updated_sub = 0
    updated_trans = 0
    
    for i, s in enumerate(rows):
        lat, lng = float(s["lat"]), float(s["lng"])
        
        # Find nearest 230kV+ substation
        cur = infra_conn.execute("""
            SELECT name, max_volt, latitude, longitude
            FROM substations
            WHERE max_volt >= 230 AND status = 'IN SERVICE'
              AND latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
        """, (
            lat - 2, lat + 2,
            lng - 2, lng + 2,
        ))
        
        best_dist = None
        best_volt = None
        for sub in cur:
            d = haversine(lat, lng, float(sub[2]), float(sub[3]))
            if best_dist is None or d < best_dist:
                best_dist = d
                best_volt = float(sub[1]) if sub[1] else 0
        
        if best_dist is not None:
            score = score_proximity(best_dist, best_volt)
            conn.execute(
                "UPDATE commercial_sites SET substation_distance_miles=?, score_substation=? WHERE id=?",
                (round(best_dist, 2), score, s["id"]),
            )
            updated_sub += 1
        
        if (i + 1) % 500 == 0:
            conn.commit()
            print(f"  {i+1}/{len(rows)} processed ({updated_sub} substation updates)")
    
    conn.commit()
    
    cur = conn.execute("""
        SELECT COUNT(*), ROUND(AVG(substation_distance_miles), 2),
               ROUND(AVG(score_substation), 1)
        FROM commercial_sites WHERE substation_distance_miles IS NOT NULL
    """)
    total, avg_dist, avg_score = cur.fetchone()
    
    print(f"\n{'='*60}")
    print(f"Scoring complete:")
    print(f"  Sites with substation distance: {total}")
    print(f"  Average distance: {avg_dist} miles")
    print(f"  Average substation score: {avg_score}")
    
    infra_conn.close()
    conn.close()


if __name__ == "__main__":
    update_site_scoring()
