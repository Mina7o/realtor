"""Directive 1: Power-First GIS Audit

Intersect A-Tier parcels with Duke Energy 230kV/500kV transmission + substation proximity.
Target counties: Rowan, Orange, Union, Chatham (NC).
Output: CSV of High-Probability Interconnect sites.
"""

import sqlite3, json, math, csv, os, sys
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))
POWER_GRID = Path(os.path.expanduser("~/Documents/proj/realtor/static/power_grid.json"))
OUTPUT = Path(os.path.expanduser("~/Documents/proj/realtor/output/high_probability_interconnect_sites.csv"))

TARGET_COUNTIES = ['rowan', 'orange', 'union', 'chatham']
SUBSTATION_MAX_MILES = 2.5
TRANSMISSION_MIN_KV = 230


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def point_to_line_distance(px, py, x1, y1, x2, y2):
    """Minimum distance (miles) from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return haversine(py, px, y1, x1)

    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    nx = x1 + t * dx
    ny = y1 + t * dy
    return haversine(py, px, ny, nx)


def main():
    conn = sqlite3.connect(str(DB))

    # --- Load substations (already in DB) ---
    subs = conn.execute("""
        SELECT lat, lng, max_volt, name FROM substations
        WHERE lat IS NOT NULL AND max_volt IS NOT NULL AND max_volt > 0
    """).fetchall()
    print(f"Loaded {len(subs)} substations")

    # --- Load transmission lines ---
    with open(POWER_GRID) as f:
        grid = json.load(f)

    lines = []
    for feat in grid.get("features", []):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue
        voltage_str = str(props.get("voltage", "0")).lower().replace("kv", "").strip()
        try:
            voltage = float(voltage_str) if voltage_str and voltage_str != "nan" else 0
        except ValueError:
            voltage = 0
        if voltage >= TRANSMISSION_MIN_KV:
            coords = geom["coordinates"]
            lines.append({"voltage": voltage, "coords": coords})
    print(f"Loaded {len(lines)} transmission line segments >= {TRANSMISSION_MIN_KV}kV")

    # --- Load A-tier parcels in target counties ---
    # Match both title-case and lowercase county names
    county_conditions = " OR ".join(
        f"(LOWER(county) = '{c.lower()}' OR county = '{c.capitalize()}')"
        for c in TARGET_COUNTIES
    )
    sites = conn.execute(f"""
        SELECT id, county, address, owner_name, acres, score_total, lat, lng,
               substation_distance_miles, zoning, power_provider
        FROM commercial_sites
        WHERE score_tier = 'A'
          AND ({county_conditions})
          AND lat IS NOT NULL
        ORDER BY score_total DESC
    """).fetchall()
    print(f"Loaded {len(sites)} A-tier sites in target counties")

    # --- Compute ---
    results = []
    for site in sites:
        sid, county, address, owner, acres, score, lat, lng, sub_dist, zoning, power = site

        if sub_dist is not None and sub_dist > SUBSTATION_MAX_MILES:
            continue

        nearest_sub = None
        nearest_sub_dist = float("inf")
        for slat, slng, sv, sname in subs:
            d = haversine(lat, lng, slat, slng)
            if d < nearest_sub_dist:
                nearest_sub_dist = d
                nearest_sub = {"name": sname, "voltage": sv, "dist_mi": round(d, 1)}

        if nearest_sub_dist > SUBSTATION_MAX_MILES:
            continue

        nearest_line = None
        nearest_line_dist = float("inf")
        for line in lines:
            coords = line["coords"]
            for i in range(len(coords) - 1):
                d = point_to_line_distance(lng, lat, coords[i][0], coords[i][1],
                                           coords[i+1][0], coords[i+1][1])
                if d < nearest_line_dist:
                    nearest_line_dist = d
                    nearest_line = {"voltage": line["voltage"], "dist_mi": round(d, 1)}

        results.append({
            "county": county,
            "address": address or "",
            "owner": (owner or "")[:60],
            "acres": round(float(acres), 1) if acres else 0,
            "score": int(score) if score else 0,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "substation_dist_mi": round(nearest_sub_dist, 1) if nearest_sub_dist < float("inf") else None,
            "substation_voltage_kv": round(nearest_sub["voltage"]) if nearest_sub else None,
            "transmission_dist_mi": round(nearest_line_dist, 1) if nearest_line_dist < float("inf") else None,
            "transmission_voltage_kv": round(nearest_line["voltage"]) if nearest_line else None,
            "zoning": zoning or "",
            "power_provider": power or "",
        })

    results.sort(key=lambda r: (r.get("transmission_dist_mi") or 999, r.get("substation_dist_mi") or 999))

    # --- Write CSV ---
    os.makedirs(str(OUTPUT.parent), exist_ok=True)
    fieldnames = ["county", "address", "owner", "acres", "score", "lat", "lng",
                  "substation_dist_mi", "substation_voltage_kv",
                  "transmission_dist_mi", "transmission_voltage_kv",
                  "zoning", "power_provider"]
    with open(OUTPUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)

    print(f"\n{'='*60}")
    print(f"DIRECTIVE 1 COMPLETE: High-Probability Interconnect Sites")
    print(f"{'='*60}")
    print(f"Output: {OUTPUT}")
    print(f"Sites found: {len(results)}")
    by_county = {}
    for r in results:
        by_county.setdefault(r["county"], 0)
        by_county[r["county"]] += 1
    for c, n in sorted(by_county.items()):
        print(f"  {c}: {n}")
    if results:
        print(f"\nTop 5 by transmission proximity:")
        for r in results[:5]:
            print(f"  {r['county']:>8} | {r['acres']:>6.1f}ac | sub {r['substation_dist_mi']}mi/{r['substation_voltage_kv']}kV | tx {r['transmission_dist_mi']}mi/{r['transmission_voltage_kv']}kV | {r['owner'][:35]}")

    conn.close()


if __name__ == "__main__":
    main()
