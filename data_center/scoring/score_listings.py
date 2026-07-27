"""Score LandAndFarm listings with A/B/C/D tiers.

Scoring (100 pts):
  acreage:  30  | price_per_acre: 25  | cluster:  20
  county:   15  | keywords:       10
"""

import sqlite3, json, math, os, sys
from pathlib import Path
from collections import defaultdict

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))

KEYWORDS = {
    'commercial', 'industrial', 'zoned', 'development',
    'highway', 'interstate', 'i-', 'utility', 'rail',
    'infrastructure', 'frontage', 'traffic', 'signal',
    'annexed', 'entitled', 'perc', 'sewer', 'water',
}

COUNTY_SCORES = {
    'Mecklenburg': 15,
    'Orange': 12,
    'Durham': 12,
    'Cabarrus': 10,
    'Iredell': 10,
    'Rowan': 10,
    'Guilford': 8,
    'Forsyth': 8,
    'Chatham': 6,
    'Johnston': 6,
}

TIER_CUTOFFS = [
    (65, 'A'),
    (45, 'B'),
    (25, 'C'),
]

def score_acreage(acres):
    if acres >= 200: return 30
    if acres >= 100: return 25
    if acres >= 50:  return 20
    if acres >= 30:  return 15
    if acres >= 20:  return 10
    if acres >= 10:  return 5
    return 0

def score_ppa(ppa):
    if ppa < 15000:  return 25
    if ppa < 25000:  return 20
    if ppa < 40000:  return 15
    if ppa < 80000:  return 10
    if ppa < 150000: return 5
    return 0

def score_county(county):
    return COUNTY_SCORES.get(county, 5)

def score_keywords(title, description):
    text = (title or '') + ' ' + (description or '')
    text_lower = text.lower()
    count = sum(1 for kw in KEYWORDS if kw in text_lower)
    return min(count * 3, 10)

def detect_clusters(listings):
    n = len(listings)
    if n == 0:
        return []
    clusters = []
    assigned = [False] * n
    for i in range(n):
        if assigned[i]:
            continue
        group = [i]
        assigned[i] = True
        queue = [i]
        while queue:
            cur = queue.pop(0)
            li = listings[cur]
            half_a = math.sqrt(li['acres'] * 4046.86) / 2 if li['acres'] > 0 else 50
            for j in range(n):
                if assigned[j] or j == cur:
                    continue
                lj = listings[j]
                half_b = math.sqrt(lj['acres'] * 4046.86) / 2 if lj['acres'] > 0 else 50
                gap = 50
                threshold = half_a + half_b + gap
                dx = (li['lng'] - lj['lng']) * 111320 * math.cos(math.radians((li['lat'] + lj['lat']) / 2))
                dy = (li['lat'] - lj['lat']) * 111320
                dist = math.hypot(dx, dy)
                if dist < threshold:
                    group.append(j)
                    assigned[j] = True
                    queue.append(j)
        if len(group) >= 2:
            clusters.append(group)
    return clusters

def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT pid, acres, price, county, title, description, lat, lng
        FROM landandfarm_listings
    """).fetchall()

    listings_data = []
    for r in rows:
        listings_data.append({
            'pid': r['pid'],
            'acres': float(r['acres'] or 0),
            'price': float(r['price'] or 0),
            'ppa': float(r['price'] or 0) / float(r['acres'] or 1) if (r['price'] and r['acres'] and float(r['acres']) > 0) else 999999,
            'county': r['county'] or '',
            'title': r['title'] or '',
            'description': r['description'] or '',
            'lat': float(r['lat'] or 0),
            'lng': float(r['lng'] or 0),
        })

    clusters = detect_clusters(listings_data)
    cluster_map = defaultdict(list)
    for group in clusters:
        for idx in group:
            cluster_map[idx].append(len(group) - 1)

    by_pid = {r['pid']: r for r in rows}

    conn.execute("""
        CREATE TABLE IF NOT EXISTS landandfarm_scores (
            pid TEXT PRIMARY KEY,
            score_total INTEGER,
            score_tier TEXT,
            score_acreage INTEGER DEFAULT 0,
            score_ppa INTEGER DEFAULT 0,
            score_county INTEGER DEFAULT 0,
            score_cluster INTEGER DEFAULT 0,
            score_keywords INTEGER DEFAULT 0,
            cluster_size INTEGER DEFAULT 1
        )
    """)

    changed = 0
    for i, ld in enumerate(listings_data):
        sa = score_acreage(ld['acres'])
        sp = score_ppa(ld['ppa'])
        sc = score_county(ld['county'])
        sk = score_keywords(ld['title'], ld['description'])

        cluster_extra = cluster_map.get(i, [])
        cluster_size = sum(cluster_extra) + 1 if cluster_extra else 1
        scl = min(cluster_size * 5, 20) if cluster_size >= 2 else 0

        total = sa + sp + sc + scl + sk
        tier = 'D'
        for cutoff, t in TIER_CUTOFFS:
            if total >= cutoff:
                tier = t
                break

        conn.execute("""
            INSERT OR REPLACE INTO landandfarm_scores
                (pid, score_total, score_tier, score_acreage, score_ppa, score_county, score_cluster, score_keywords, cluster_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ld['pid'], total, tier, sa, sp, sc, scl, sk, cluster_size))
        changed += 1

    conn.commit()

    tiers = conn.execute("""
        SELECT score_tier, COUNT(*) as cnt FROM landandfarm_scores GROUP BY score_tier ORDER BY score_tier
    """).fetchall()
    conn.close()

    print(f"Scored {changed} listings")
    for t in tiers:
        print(f"  Tier {t['score_tier']}: {t['cnt']}")

if __name__ == '__main__':
    main()
