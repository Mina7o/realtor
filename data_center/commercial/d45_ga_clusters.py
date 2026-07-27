"""
D45: ARC Georgia — Assembly Multiplier & Top 3 Clusters
Extract commercial/industrial parcels from DeKalb/Fulton,
fetch geometry centroids from ArcGIS, find 100+ ac clusters.
"""
import json
import sqlite3
import math
import time
from pathlib import Path
from urllib.request import urlopen, Request

DB = Path(__file__).parent.parent / "deals.db"
MIN_CLUSTER_AC = 100
CLUSTER_RADIUS_MI = 0.5

DEKALB_FS = ("https://services2.arcgis.com/IxVN2oUE9EYLSnPE/arcgis/rest/"
             "services/Tax_Parcels_2025/FeatureServer/0")
FULTON_FS = ("https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/"
             "services/Tax_Parcels/FeatureServer/0")


def haversine(lon1, lat1, lon2, lat2):
    R = 3959
    dlon = math.radians(lon2 - lon1)
    dlat = math.radians(lat2 - lat1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def fetch_centroids(oids, fs_url, label=""):
    """Fetch geometry centroids for a list of OBJECTIDs from ArcGIS FeatureServer."""
    from urllib.parse import urlencode
    centroids = {}
    batch_size = 100
    for i in range(0, len(oids), batch_size):
        batch = oids[i:i+batch_size]
        where = "OBJECTID IN (" + ",".join(str(o) for o in batch) + ")"
        body = {
            "where": where,
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
            "resultRecordCount": str(batch_size),
        }
        data = urlencode(body).encode()
        try:
            req = Request(f"{fs_url}/query", data=data, headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"})
            resp = urlopen(req, timeout=60)
            result = json.loads(resp.read())
            for feat in result.get("features", []):
                attrs = feat.get("attributes", {})
                oid = attrs.get("OBJECTID")
                geom = feat.get("geometry", {})
                rings = geom.get("rings")
                if rings:
                    ring = rings[0]
                    xs = [c[0] for c in ring]
                    ys = [c[1] for c in ring]
                    cx = sum(xs) / len(xs)
                    cy = sum(ys) / len(ys)
                    centroids[oid] = (cx, cy)
        except Exception as e:
            print(f"    Error batch {i}: {e}")
            time.sleep(3)
        if (i // batch_size) % 5 == 0:
            print(f"  {label}: {i + len(batch)}/{len(oids)} centroids fetched ({len(centroids)} total)...")
    return centroids


def extract_ga_parcels():
    """Extract commercial/industrial parcels from ARC DeKalb and Fulton tables."""
    conn = sqlite3.connect(str(DB))
    
    all_parcels = []
    
    # Land use codes for commercial/industrial
    target_landuse = {'101', '102', '103', '104', '105', '106', '201', '202', '301', '302', '401', '402', '403', '501', '502', '503', '601', '602', '603', '701', '702', '801', '802', '901', '902'}
    target_zoning_keywords = ('C', 'I', 'M', 'O', 'BUSINESS', 'INDUSTRIAL', 'COMMERCIAL', 'OFFICE', 'MIXED')
    
    # DeKalb
    cur = conn.execute("SELECT properties FROM arc_parcels_dekalb")
    dekalb_parcels = []
    dekalb_oids = []
    for row in cur.fetchall():
        p = json.loads(row[0])
        acres = p.get('Shape__Area', 0) / 43560 if p.get('Shape__Area') else 0
        lu = p.get('LANDUSECODE', '')
        zoning = (p.get('ZONING') or '').upper()
        is_commercial = lu in target_landuse or any(k in zoning for k in target_zoning_keywords)
        if not is_commercial or acres < 5:
            continue
        dekalb_parcels.append({
            'source': 'DeKalb',
            'objectid': p['OBJECTID'],
            'parcel_id': p.get('ParcelID'),
            'acres': acres,
            'zoning': zoning,
            'assessed_value': p.get('ASSESSED_VALUE', 0),
            'lat': p.get('CENTROID_Y'),
            'lng': p.get('CENTROID_X'),
        })
        dekalb_oids.append(p['OBJECTID'])
    
    print(f"DeKalb: {len(dekalb_parcels)} commercial parcels >=5ac")
    
    # Fetch centroids for DeKalb parcels
    print("  Fetching DeKalb centroids from ArcGIS...")
    centroids = fetch_centroids(dekalb_oids, DEKALB_FS, "DeKalb")
    for p in dekalb_parcels:
        if p['objectid'] in centroids:
            p['lng'], p['lat'] = centroids[p['objectid']]
    all_parcels.extend(dekalb_parcels)
    
    # Fulton
    cur = conn.execute("SELECT properties FROM arc_parcels_fulton")
    fulton_oids = []
    for row in cur.fetchall():
        p = json.loads(row[0])
        acres = p.get('LandAcres') or (p.get('Shape__Area', 0) / 43560)
        lu = (p.get('LUCode') or '')
        zoning = (p.get('ClassCode') or '').upper()
        is_commercial = lu in target_landuse or any(k in zoning for k in target_zoning_keywords)
        if not is_commercial or not acres or acres < 5:
            continue
        all_parcels.append({
            'source': 'Fulton',
            'objectid': p['OBJECTID'],
            'parcel_id': p.get('ParcelID'),
            'acres': acres,
            'zoning': zoning,
            'assessed_value': 0,
            'address': p.get('Address', ''),
            'lat': None,
            'lng': None,
        })
        fulton_oids.append(p['OBJECTID'])
    
    print(f"Fulton: {len(fulton_oids)} commercial parcels >=5ac")
    
    # Fetch Fulton centroids
    if fulton_oids:
        print("  Fetching Fulton centroids from ArcGIS...")
        fcentroids = fetch_centroids(fulton_oids, FULTON_FS, "Fulton")
        for p in all_parcels:
            if p['source'] == 'Fulton' and p['objectid'] in fcentroids:
                p['lng'], p['lat'] = fcentroids[p['objectid']]
    
    conn.close()
    return all_parcels


def cluster_parcels(parcels):
    """Group parcels into clusters by proximity (0.5mi radius)."""
    geo = [p for p in parcels if p['lat'] and p['lng']]
    clusters = []
    assigned = set()
    
    for i, p in enumerate(geo):
        if i in assigned:
            continue
        cluster = [p]
        assigned.add(i)
        for j, q in enumerate(geo):
            if j in assigned:
                continue
            dist = haversine(p['lng'], p['lat'], q['lng'], q['lat'])
            if dist <= CLUSTER_RADIUS_MI:
                cluster.append(q)
                assigned.add(j)
        
        total = sum(c['acres'] for c in cluster)
        if total >= MIN_CLUSTER_AC:
            lats = [c['lat'] for c in cluster]
            lngs = [c['lng'] for c in cluster]
            clusters.append({
                'total_acres': total,
                'parcel_count': len(cluster),
                'avg_lat': sum(lats) / len(lats),
                'avg_lng': sum(lngs) / len(lngs),
                'parcels': cluster,
            })
    
    clusters.sort(key=lambda c: c['total_acres'], reverse=True)
    return clusters


def save_clusters(clusters, db_path=None):
    """Save clusters to infrastructure.db for webapp use."""
    if db_path is None:
        db_path = Path(__file__).parent.parent / "infrastructure.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS ga_clusters")
    conn.execute("""
        CREATE TABLE ga_clusters (
            rank INTEGER PRIMARY KEY,
            total_acres REAL,
            parcel_count INTEGER,
            avg_lat REAL,
            avg_lng REAL,
            county TEXT,
            top_zoning TEXT,
            top_parcel_id TEXT,
            top_parcel_acres REAL,
            top_assessed_value REAL
        )
    """)
    for i, c in enumerate(clusters):
        top = sorted(c['parcels'], key=lambda x: x['acres'], reverse=True)[0]
        county = c['parcels'][0]['source']
        conn.execute("""
            INSERT INTO ga_clusters (rank, total_acres, parcel_count, avg_lat, avg_lng,
                                     county, top_zoning, top_parcel_id, top_parcel_acres,
                                     top_assessed_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (i+1, c['total_acres'], c['parcel_count'], c['avg_lat'], c['avg_lng'],
              county, top.get('zoning', ''), top.get('parcel_id', ''), top['acres'],
              top.get('assessed_value', 0)))
    conn.commit()
    conn.close()
    print(f"Saved {len(clusters)} clusters to {db_path}")


def main():
    print("Extracting GA commercial parcels...")
    parcels = extract_ga_parcels()
    print(f"Total GA commercial parcels >=5ac: {len(parcels)}")
    geo_count = len([p for p in parcels if p['lat'] and p['lng']])
    print(f"  With centroids: {geo_count}")
    
    if geo_count == 0:
        print("\nNo parcels with centroids — cannot cluster. Aborting.")
        return
    
    print("\nClustering...")
    clusters = cluster_parcels(parcels)
    big = [c for c in clusters if c['total_acres'] >= 100]
    
    print(f"\n=== D45: TOP GA CLUSTERS (>=100ac) ===\n")
    print(f"{'Rank':5s} {'Acres':>8s} {'Parcels':>8s} {'Source':10s} {'Lat':>10s} {'Lng':>10s} {'Avg Ac':>8s}")
    print("-" * 65)
    for i, c in enumerate(big[:10]):
        avg = c['total_acres'] / c['parcel_count']
        src = c['parcels'][0]['source']
        print(f"{i+1:5d} {c['total_acres']:>8.1f} {c['parcel_count']:>8d} {src:10s} {c['avg_lat']:>10.5f} {c['avg_lng']:>10.5f} {avg:>8.1f}")
    
    print(f"\nTotal clusters >=100ac: {len(big)}")
    
    print(f"\n=== TOP 3 CLUSTERS ===\n")
    for i, c in enumerate(big[:3]):
        top_parcels = sorted(c['parcels'], key=lambda x: x['acres'], reverse=True)
        print(f"Cluster {i+1}: {c['total_acres']:.1f}ac — {c['parcel_count']} parcels")
        print(f"  Center: {c['avg_lat']:.5f}, {c['avg_lng']:.5f}")
        print(f"  Top parcels:")
        for p in top_parcels[:5]:
            val = f"${p.get('assessed_value', 0):,}" if p.get('assessed_value') else "N/A"
            addr = p.get('address', '') or p.get('parcel_id', '')
            print(f"    {addr:25s} | {p['acres']:7.1f}ac | {p.get('zoning', ''):12s} | {val:>12s}")
        print()
    
    save_clusters(big)


if __name__ == "__main__":
    main()
