"""
Directive 33: PeeringDB Network Overlay
Pull facility/peering point locations in the Southeast US
and add a Fiber Density score to commercial_sites.

PeeringDB API: https://peeringdb.com/apidocs
Endpoints:
  /api/fac  — facilities (data centers, colo, peering)
  /api/ix   — exchanges
  /api/net  — networks

Free, no auth required for GET requests.
Rate limit: ~10 req/s (be nice).
"""

import argparse
import json
import math
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen, Request

DB_PATH = Path(__file__).parent.parent / "deals.db"
INFRA_DB = Path(__file__).parent.parent / "infrastructure.db"

PEERING_BASE = "https://www.peeringdb.com/api"
TARGET_STATES = {"NC", "SC", "GA", "VA", "FL", "TN", "AL"}

HEADERS = {
    "User-Agent": "realtor-research/1.0 (commercial site scoring)",
    "Accept": "application/json",
}


def api_get(path, params=None):
    url = f"{PEERING_BASE}/{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == 2:
                print(f"    Error fetching {path}: {e}")
                return {"data": []}
            time.sleep(1)


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def create_facilities_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peering_facilities (
            id INTEGER PRIMARY KEY,
            name TEXT,
            org_name TEXT,
            address1 TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            latitude REAL,
            longitude REAL,
            npa_nxx TEXT,
            clli TEXT,
            tech_email TEXT,
            sales_email TEXT,
            networks_count INTEGER,
            exchanges_count INTEGER,
            carriers_count INTEGER,
            notes TEXT,
            properties_json TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_peering_state ON peering_facilities(state)
    """)


def fetch_all_facilities():
    print("Fetching all US facilities from PeeringDB...")
    data = api_get("fac", {"country__in": "US", "limit": 1000})
    facilities = data.get("data", [])
    print(f"  Got {len(facilities)} US facilities")
    return facilities


def ingest_facilities(conn, facilities):
    cur = conn.cursor()
    count = 0
    for f in facilities:
        state = (f.get("state") or "").upper()
        if state not in TARGET_STATES:
            continue
        
        lat = f.get("latitude")
        lng = f.get("longitude")
        if lat is None or lng is None:
            continue
        
        cur.execute("""
            INSERT OR REPLACE INTO peering_facilities
            (id, name, org_name, address1, city, state, country,
             latitude, longitude, npa_nxx, clli,
             tech_email, sales_email,
             networks_count, exchanges_count, carriers_count,
             notes, properties_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            f["id"],
            (f.get("name") or "")[:200],
            (f.get("org_name") or "")[:200],
            (f.get("address1") or "")[:200],
            (f.get("city") or "")[:100],
            state,
            f.get("country", "US"),
            float(lat),
            float(lng),
            (f.get("npa_nxx") or "")[:20],
            (f.get("clli") or "")[:20],
            (f.get("tech_email") or "")[:100],
            (f.get("sales_email") or "")[:100],
            f.get("net_count") or 0,
            f.get("ix_count") or 0,
            f.get("carrier_count") or 0,
            (f.get("notes") or "")[:500],
            json.dumps(f),
        ))
        count += 1
    conn.commit()
    return count


def score_fiber_density():
    """Score each commercial_site by proximity to peering facilities."""
    infra = sqlite3.connect(str(INFRA_DB))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    # Load all SE facilities
    infra.row_factory = sqlite3.Row
    facs = infra.execute(
        "SELECT id, name, latitude, longitude, networks_count "
        "FROM peering_facilities ORDER BY id"
    ).fetchall()
    infra.row_factory = None
    
    print(f"Scoring {len(facs)} facilities against commercial sites...")
    
    sites = conn.execute(
        "SELECT id, lat, lng FROM commercial_sites WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    
    updated = 0
    for i, s in enumerate(sites):
        lat, lng = float(s["lat"]), float(s["lng"])
        
        # Count facilities within 10 miles
        nearby = 0
        total_networks = 0
        for f in facs:
            d = haversine(lat, lng, float(f["latitude"]), float(f["longitude"]))
            if d <= 10:
                nearby += 1
                total_networks += f["networks_count"] or 0
        
        # Score: 0-15 based on facility density
        if nearby >= 5:
            fiber_score = 15
        elif nearby >= 3:
            fiber_score = 12
        elif nearby >= 2:
            fiber_score = 8
        elif nearby >= 1:
            fiber_score = 5
        else:
            fiber_score = 0
        
        conn.execute(
            "UPDATE commercial_sites SET score_fiber=? WHERE id=?",
            (fiber_score, s["id"]),
        )
        updated += 1
        
        if (i + 1) % 2000 == 0:
            conn.commit()
            print(f"  {i+1}/{len(sites)} scored")
    
    conn.commit()
    
    stats = conn.execute("""
        SELECT COUNT(*), ROUND(AVG(score_fiber), 1)
        FROM commercial_sites WHERE score_fiber IS NOT NULL
    """).fetchone()
    print(f"\n  Sites scored: {stats[0]}")
    print(f"  Average fiber score: {stats[1]}")
    
    infra.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch PeeringDB facilities")
    parser.add_argument("--fetch", action="store_true", help="Fetch facilities from PeeringDB")
    parser.add_argument("--score", action="store_true", help="Score sites against facilities")
    args = parser.parse_args()
    
    if not args.fetch and not args.score:
        args.fetch = True
        args.score = True
    
    infra = sqlite3.connect(str(INFRA_DB))
    
    if args.fetch:
        create_facilities_table(infra)
        facilities = fetch_all_facilities()
        count = ingest_facilities(infra, facilities)
        print(f"  {count} facilities in target states stored in infrastructure.db")
    
    infra.close()
    
    if args.score:
        # Ensure score_fiber column exists
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("ALTER TABLE commercial_sites ADD COLUMN score_fiber INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.close()
        score_fiber_density()


if __name__ == "__main__":
    main()
