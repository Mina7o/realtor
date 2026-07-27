"""Fetch HIFLD substation data via public ArcGIS REST endpoint.

Source: Oregon State University / Oregon Explorer mirror of DHS HIFLD.
Endpoint: services1.arcgis.com/CD5mKowwN6nIaqd8/.../project_renewable_us_substations_2022
"""

import requests, json, sqlite3, os, math, sys, time
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))
STATIC = Path(os.path.expanduser("~/Documents/proj/realtor/static"))

ENDPOINT = (
    "https://services1.arcgis.com/CD5mKowwN6nIaqd8/arcgis/rest/services/"
    "project_renewable_us_substations_2022/FeatureServer/10/query"
)

TARGET_STATES = ["NC", "SC", "GA", "FL", "VA"]
MAX_PER_PAGE = 2000


def fetch_state(state):
    """Fetch all substations for a given state with pagination."""
    all_features = []
    offset = 0

    while True:
        params = {
            "where": f"STATE='{state}'",
            "outFields": "NAME,CITY,COUNTY,STATE,MAX_VOLT,MIN_VOLT,STATUS,LATITUDE,LONGITUDE,COUNTYFIPS",
            "returnGeometry": "true",
            "f": "geojson",
            "resultRecordCount": MAX_PER_PAGE,
            "resultOffset": offset,
        }
        r = requests.get(ENDPOINT, params=params, timeout=60)
        data = r.json()
        features = data.get("features", [])
        if not features:
            break
        all_features.extend(features)
        offset += MAX_PER_PAGE
        print(f"  {state}: fetched {len(all_features)} so far...")
        time.sleep(0.5)

    print(f"  {state}: {len(all_features)} total")
    return all_features


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_substation_proximity(dist_miles, max_voltage):
    if max_voltage >= 230:
        if dist_miles < 1: return 15
        if dist_miles < 3: return 12
        if dist_miles < 5: return 8
        if dist_miles < 10: return 5
    elif max_voltage >= 100:
        if dist_miles < 1: return 12
        if dist_miles < 3: return 8
        if dist_miles < 5: return 5
        if dist_miles < 10: return 3
    else:
        if dist_miles < 1: return 8
        if dist_miles < 3: return 5
        if dist_miles < 5: return 3
        if dist_miles < 10: return 1
    return 0


def main():
    states_to_fetch = sys.argv[1:] if len(sys.argv) > 1 else TARGET_STATES

    conn = sqlite3.connect(str(DB))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS substations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            city TEXT,
            county TEXT,
            state TEXT,
            fips TEXT,
            max_volt REAL,
            min_volt REAL,
            status TEXT,
            lat REAL,
            lng REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_substations_state ON substations(state)")

    existing = conn.execute("SELECT COUNT(*) FROM substations").fetchone()[0]
    if existing > 0:
        print(f"Database already has {existing} substations. Add --refresh to re-fetch.")
        # Still compute scores if we have substations but no scores yet
    else:
        all_features = []
        for state in states_to_fetch:
            feats = fetch_state(state)
            all_features.extend(feats)

        print(f"\nTotal substations fetched: {len(all_features)}")

        for f in all_features:
            p = f["properties"]
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [0, 0]) if geom else [0, 0]
            conn.execute("""
                INSERT INTO substations (name, city, county, state, fips, max_volt, min_volt, status, lat, lng)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                p.get("NAME"),
                p.get("CITY"),
                p.get("COUNTY"),
                p.get("STATE"),
                p.get("COUNTYFIPS"),
                p.get("MAX_VOLT") if p.get("MAX_VOLT") and p["MAX_VOLT"] > 0 else None,
                p.get("MIN_VOLT") if p.get("MIN_VOLT") and p["MIN_VOLT"] > 0 else None,
                p.get("STATUS"),
                p.get("LATITUDE"),
                p.get("LONGITUDE"),
            ))
        conn.commit()
        print(f"Stored {len(all_features)} substations in DB")

    # --- Score commercial_sites by substation proximity ---
    substations = conn.execute("""
        SELECT lat, lng, max_volt FROM substations
        WHERE lat IS NOT NULL AND lng IS NOT NULL
    """).fetchall()
    print(f"\nComputing proximity scores using {len(substations)} substations...")

    sites = conn.execute("""
        SELECT id, lat, lng FROM commercial_sites
        WHERE lat IS NOT NULL AND lng IS NOT NULL
    """).fetchall()

    changed = 0
    for sid, slat, slng in sites:
        best_dist = float("inf")
        best_volt = 0
        for ss_lat, ss_lng, ss_volt in substations:
            d = haversine(slat, slng, ss_lat, ss_lng)
            if d < best_dist:
                best_dist = d
                best_volt = ss_volt or 0

        if best_dist == float("inf"):
            continue

        s = score_substation_proximity(best_dist, best_volt)
        old = conn.execute(
            "SELECT score_substation FROM commercial_sites WHERE id=?", (sid,)
        ).fetchone()
        old_s = int(old[0]) if old and old[0] else -1

        if s != old_s:
            conn.execute(
                "UPDATE commercial_sites SET score_substation=? WHERE id=?",
                (s, sid)
            )
            changed += 1

    conn.commit()
    print(f"Updated substation scores for {changed} sites")

    # Recalc tiers with substation score factored in
    total_max = 100 + 15  # old max 100 + new substation 15
    tiers = conn.execute("""
        SELECT id, score_acreage, score_land_use, score_vacancy, score_owner,
               score_density, score_zoning, score_flood, score_econ_dev,
               score_substation
        FROM commercial_sites
    """).fetchall()

    tier_changed = 0
    for r in tiers:
        (sid, sa, slu, sv, so, sd, sz, sf, se, ss) = r
        sa = int(sa) if sa else 0
        slu = int(slu) if slu else 0
        sv = int(sv) if sv else 0
        sd = int(sd) if sd else 0
        so = int(so) if so else 0
        sz = int(sz) if sz else 0
        sf = int(sf) if sf else 0
        se = int(se) if se else 0
        ss = int(ss) if ss else 0

        total = min(sa + slu + sv + so + sd + sz + sf + se + ss, 100)
        tier = "A" if total >= 65 else ("B" if total >= 45 else ("C" if total >= 25 else "D"))

        old = conn.execute(
            "SELECT score_total, score_tier FROM commercial_sites WHERE id=?",
            (sid,)
        ).fetchone()
        if old and (int(old[0]) != total or old[1] != tier):
            conn.execute(
                "UPDATE commercial_sites SET score_total=?, score_tier=? WHERE id=?",
                (total, tier, sid)
            )
            tier_changed += 1

    conn.commit()
    conn.close()

    print(f"Recalculated tiers for {tier_changed} sites")


if __name__ == "__main__":
    main()
