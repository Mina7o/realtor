#!/usr/bin/env python3
"""Fetch Overture Maps building footprints and land-use data for Southeast US states.

Usage:
    python scripts/fetch_overture.py                     # Fetch for all 3 states
    python scripts/fetch_overture.py --states NC         # Single state
    python scripts/fetch_overture.py --states NC,SC,GA   # Custom set
    python scripts/fetch_overture.py --format geoparquet # Save as GeoParquet
"""

import argparse
import json
import os
import sys
import time

# Dependencies: pip install overturemaps geopandas pyarrow
# Optional: pip install duckdb


# State bounding boxes (west, south, east, north) — generous buffers
STATE_BBOXES = {
    "NC": (-84.5, 33.5, -75.5, 36.5),
    "SC": (-83.5, 32.0, -78.5, 35.5),
    "GA": (-85.5, 30.5, -81.0, 35.5),
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "overture")


def fetch_buildings_overturemaps_py(bbox, output_path, fmt="geojson"):
    """Fetch building footprints using overturemaps Python SDK."""
    from overturemaps import geodataframe

    print(f"  Querying buildings with bbox={bbox}...")
    start = time.time()
    gdf = geodataframe("building", bbox=bbox)
    elapsed = time.time() - start
    print(f"  Got {len(gdf)} buildings in {elapsed:.1f}s")

    if fmt == "geoparquet":
        gdf.to_parquet(output_path)
    else:
        gdf.to_file(output_path, driver="GeoJSON")
    print(f"  Saved to {output_path}")
    return gdf


def fetch_landuse_overturemaps_py(bbox, output_path, fmt="geojson"):
    """Fetch land-use polygons using overturemaps Python SDK."""
    from overturemaps import geodataframe

    print(f"  Querying land_use with bbox={bbox}...")
    start = time.time()
    gdf = geodataframe("land_use", bbox=bbox)
    elapsed = time.time() - start
    print(f"  Got {len(gdf)} land-use features in {elapsed:.1f}s")

    if fmt == "geoparquet":
        gdf.to_parquet(output_path)
    else:
        gdf.to_file(output_path, driver="GeoJSON")
    print(f"  Saved to {output_path}")
    return gdf


def fetch_with_duckdb(bbox, theme_type, output_path, fmt="geojson"):
    """Fetch using DuckDB for more control over columns."""
    import duckdb

    west, south, east, north = bbox
    theme, ftype = theme_type.split("/")

    print(f"  Querying {theme_type} via DuckDB with bbox={bbox}...")
    start = time.time()

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")

    s3_path = (
        f"s3://overturemaps-us-west-2/release/2026-05-20.0/"
        f"theme={theme}/type={ftype}/*"
    )

    if ftype == "building":
        query = f"""
            COPY (
                SELECT id, height, num_floors, subtype, class,
                       has_parts, names, geometry
                FROM read_parquet('{s3_path}', filename=true, hive_partitioning=1)
                WHERE bbox.xmin < {east} AND bbox.xmax > {west}
                  AND bbox.ymin < {north} AND bbox.ymax > {south}
            ) TO '{output_path}'
        """
    elif ftype == "land_use":
        query = f"""
            COPY (
                SELECT id, subtype, class, names, geometry
                FROM read_parquet('{s3_path}', filename=true, hive_partitioning=1)
                WHERE bbox.xmin < {east} AND bbox.xmax > {west}
                  AND bbox.ymin < {north} AND bbox.ymax > {south}
            ) TO '{output_path}'
        """
    else:
        raise ValueError(f"Unknown type: {ftype}")

    if fmt == "geojson":
        query += " WITH (FORMAT GDAL, DRIVER 'GeoJSON')"
    elif fmt == "geoparquet":
        pass  # COPY already writes parquet
    elif fmt == "gpkg":
        query += " WITH (FORMAT GDAL, DRIVER 'GPKG')"

    con.execute(query)
    con.close()
    elapsed = time.time() - start
    print(f"  Done in {elapsed:.1f}s -> {output_path}")


def fetch_state(state_code, fmt="geojson", engine="sdk"):
    """Fetch both buildings and land-use for one state."""
    bbox = STATE_BBOXES[state_code]
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    buildings_path = os.path.join(OUTPUT_DIR, f"{state_code}_buildings.{fmt}")
    landuse_path = os.path.join(OUTPUT_DIR, f"{state_code}_land_use.{fmt}")

    if engine == "duckdb":
        fetch_with_duckdb(bbox, "buildings/building", buildings_path, fmt)
        fetch_with_duckdb(bbox, "base/land_use", landuse_path, fmt)
    else:
        fetch_buildings_overturemaps_py(bbox, buildings_path, fmt)
        fetch_landuse_overturemaps_py(bbox, landuse_path, fmt)


def bbox_for_states(state_codes):
    """Compute a combined bounding box from state bboxes."""
    west = min(STATE_BBOXES[s][0] for s in state_codes)
    south = min(STATE_BBOXES[s][1] for s in state_codes)
    east = max(STATE_BBOXES[s][2] for s in state_codes)
    north = max(STATE_BBOXES[s][3] for s in state_codes)
    return (west, south, east, north)


def fetch_combined(state_codes, fmt="geojson", engine="sdk"):
    """Fetch combined data for multiple states in one query."""
    bbox = bbox_for_states(state_codes)
    label = "".join(state_codes)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    buildings_path = os.path.join(OUTPUT_DIR, f"{label}_buildings.{fmt}")
    landuse_path = os.path.join(OUTPUT_DIR, f"{label}_land_use.{fmt}")

    print(f"\nFetching buildings for combined bbox {bbox}...")
    if engine == "duckdb":
        fetch_with_duckdb(bbox, "buildings/building", buildings_path, fmt)
    else:
        fetch_buildings_overturemaps_py(bbox, buildings_path, fmt)

    print(f"\nFetching land-use for combined bbox {bbox}...")
    if engine == "duckdb":
        fetch_with_duckdb(bbox, "base/land_use", landuse_path, fmt)
    else:
        fetch_landuse_overturemaps_py(bbox, landuse_path, fmt)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Overture Maps building footprints and land-use data"
    )
    parser.add_argument(
        "--states", default="NC,SC,GA",
        help="Comma-separated state codes (default: NC,SC,GA)"
    )
    parser.add_argument(
        "--format", choices=["geojson", "geoparquet", "gpkg"], default="geojson",
        help="Output format (default: geojson)"
    )
    parser.add_argument(
        "--engine", choices=["sdk", "duckdb"], default="sdk",
        help="Download engine: 'sdk' (overturemaps-py) or 'duckdb' (SQL)"
    )
    parser.add_argument(
        "--combined", action="store_true", default=True,
        help="Fetch all states in a single query (default: True)"
    )
    parser.add_argument(
        "--per-state", action="store_true",
        help="Fetch each state separately instead of combined"
    )
    args = parser.parse_args()

    state_codes = [s.strip().upper() for s in args.states.split(",")]
    for code in state_codes:
        if code not in STATE_BBOXES:
            print(f"Unknown state: {code}. Choose from: {list(STATE_BBOXES.keys())}")
            sys.exit(1)

    print(f"Overture Maps Fetch — States: {state_codes}, Format: {args.format}")
    print(f"Output directory: {OUTPUT_DIR}")

    if args.per_state:
        for code in state_codes:
            print(f"\n{'='*60}")
            print(f"Fetching {code}...")
            fetch_state(code, fmt=args.format, engine=args.engine)
    else:
        fetch_combined(state_codes, fmt=args.format, engine=args.engine)

    print(f"\nDone! Files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
