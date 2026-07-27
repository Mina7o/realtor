"""
Directive 34: Overture Maps Joint
Download building footprints and land-use tags for the Southeast US
from the Overture Maps dataset (Meta/Amazon/Microsoft joint venture).

Overture data is on S3 as GeoParquet, no auth needed.
https://docs.overturemaps.org/getting-data/

Themes:
  - buildings: building footprints (~2.5B globally)
  - base: land use, land cover, water, etc.

Usage:
  pip install "overturemaps[sdk]"
  python data_center/fetch_overture.py --themes buildings,base --states NC,SC,GA
"""

import argparse
import json
import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "deals.db"
INFRA_DB = Path(__file__).parent.parent / "infrastructure.db"

TARGET_STATES = {"NC", "SC", "GA", "VA", "FL", "TN", "AL"}

# State bounding boxes (xmin=lon_min, ymin=lat_min, xmax=lon_max, ymax=lat_max)
STATE_BBOX = {
    "NC": (-84.43, 33.75, -75.38, 36.62),
    "SC": (-83.36, 32.03, -78.48, 35.22),
    "GA": (-85.61, 30.36, -80.84, 35.01),
    "VA": (-83.68, 36.54, -75.17, 39.47),
    "FL": (-87.63, 24.40, -79.97, 31.01),
    "TN": (-90.31, 34.98, -81.65, 36.68),
    "AL": (-88.47, 30.13, -84.89, 35.01),
}


def get_combined_bbox(states):
    xmins = [STATE_BBOX[s][0] for s in states]
    ymins = [STATE_BBOX[s][1] for s in states]
    xmaxs = [STATE_BBOX[s][2] for s in states]
    ymaxs = [STATE_BBOX[s][3] for s in states]
    return (min(xmins), min(ymins), max(xmaxs), max(ymaxs))


def fetch_overture_data(themes, bbox, output_dir):
    """Use overturemaps SDK to download data."""
    import overturemaps
    
    type_map = {
        "buildings": "building",
        "building": "building",
        "base": "land_use",
        "land_use": "land_use",
        "land_cover": "land_cover",
        "infrastructure": "infrastructure",
        "places": "place",
    }
    
    results = {}
    for theme in themes:
        ot = type_map.get(theme, theme)
        print(f"\n  Fetching Overture type: {ot} (from '{theme}')")
        try:
            gdf = overturemaps.geodataframe(ot, bbox=bbox)
            print(f"    Got {len(gdf)} features")
            
            out_path = Path(output_dir) / f"overture_{theme.replace('/', '_')}.parquet"
            gdf.to_parquet(out_path)
            print(f"    Saved to {out_path}")
            results[theme] = len(gdf)
        except Exception as e:
            print(f"    Error: {e}")
            results[theme] = 0
    
    return results


def load_land_use_to_db(infra_conn, parquet_path):
    """Load Overture land-use polygons into infrastructure.db."""
    import pandas as pd
    import geopandas as gpd
    
    gdf = gpd.read_parquet(parquet_path)
    
    infra_conn.execute("""
        CREATE TABLE IF NOT EXISTS overture_land_use (
            id TEXT,
            subtype TEXT,
            class TEXT,
            sources TEXT,
            geometry_geojson TEXT
        )
    """)
    
    targets = ("industrial", "commercial", "utility", "retail", "institutional", "developed")
    mask = gdf["subtype"].isin(targets) if "subtype" in gdf.columns else slice(None)
    subset = gdf[mask]
    
    cur = infra_conn.cursor()
    count = 0
    for _, row in subset.iterrows():
        import json
        geom = row.get("geometry")
        geom_json = geom.__geo_interface__ if hasattr(geom, "__geo_interface__") else None
        
        sources = row.get("sources", {})
        sources_str = json.dumps(sources) if isinstance(sources, (dict, list)) else str(sources)
        
        cur.execute(
            "INSERT INTO overture_land_use (id, subtype, class, sources, geometry_geojson) VALUES (?,?,?,?,?)",
            (
                str(row.get("id", "")),
                str(row.get("subtype", "")),
                str(row.get("class", "")),
                str(sources_str)[:500],
                geom_json,
            ),
        )
        count += 1
    infra_conn.commit()
    print(f"  {count} commercial/industrial land-use records stored")
    return count


def score_land_use_density(infra_conn, db_conn):
    """Score commercial sites by proximity to industrial/commercial land use."""
    # Load all industrial/commercial land-use centroids
    rows = infra_conn.execute(
        "SELECT geometry_geojson FROM overture_land_use WHERE geometry_geojson IS NOT NULL"
    ).fetchall()
    
    from shapely.geometry import shape
    from shapely.ops import nearest_points
    
    centroids = []
    for (geom_json,) in rows:
        try:
            geom = shape(json.loads(geom_json))
            centroids.append((geom.centroid.y, geom.centroid.x))
        except:
            pass
    
    print(f"  Scoring {len(centroids)} land-use polygons...")
    
    sites = db_conn.execute(
        "SELECT id, lat, lng FROM commercial_sites WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 3958.8
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    updated = 0
    for s in sites:
        lat, lng = float(s["lat"]), float(s["lng"])
        nearby = sum(1 for cy, cx in centroids if haversine(lat, lng, cy, cx) < 5)
        
        score = min(nearby, 10)
        db_conn.execute(
            "UPDATE commercial_sites SET score_land_use_density=? WHERE id=?",
            (score, s["id"]),
        )
        updated += 1
    
    db_conn.commit()
    print(f"  Scored {updated} sites")
    return updated


def main():
    parser = argparse.ArgumentParser(description="Fetch Overture Maps data")
    parser.add_argument("--themes", default="buildings,base",
                        help="Themes to fetch (comma-separated)")
    parser.add_argument("--states", default="NC,SC,GA",
                        help="Target states (comma-separated)")
    parser.add_argument("--output-dir", default="/home/euclid/Documents/proj/realtor/data",
                        help="Output directory for Parquet files")
    parser.add_argument("--load-db", action="store_true",
                        help="Load land-use data into infrastructure.db")
    parser.add_argument("--score", action="store_true",
                        help="Score sites with land-use proximity")
    args = parser.parse_args()
    
    themes = [t.strip() for t in args.themes.split(",")]
    states = [s.strip().upper() for s in args.states.split(",")]
    invalid = [s for s in states if s not in STATE_BBOX]
    if invalid:
        print(f"Unknown states: {invalid}")
        return
    
    bbox = get_combined_bbox(states)
    print(f"Bounding box: {bbox}")
    print(f"States: {states}")
    print(f"Themes: {themes}")
    
    Path(args.output_dir).mkdir(exist_ok=True)
    
    results = fetch_overture_data(themes, bbox, args.output_dir)
    
    for theme, count in results.items():
        print(f"  {theme}: {count:,} features")
    
    if args.load_db:
        print("\nLoading land-use data into infrastructure.db...")
        infra = sqlite3.connect(str(INFRA_DB))
        parquet_path = Path(args.output_dir) / "overture_base.parquet"
        if parquet_path.exists():
            load_land_use_to_db(infra, str(parquet_path))
        infra.close()
    
    if args.score:
        print("\nScoring sites...")
        try:
            infra = sqlite3.connect(str(INFRA_DB))
            db = sqlite3.connect(str(DB_PATH))
            try:
                db.execute("ALTER TABLE commercial_sites ADD COLUMN score_land_use_density INTEGER DEFAULT 0")
                db.commit()
            except sqlite3.OperationalError:
                pass
            score_land_use_density(infra, db)
            db.close()
            infra.close()
        except ImportError:
            print("  Need shapely: pip install shapely")


if __name__ == "__main__":
    main()
