"""Map power providers to all Texas commercial_sites via spatial join.

Sources:
  - COOP_DIST: 68 electric cooperatives (PUCT)
  - MUNI: 72 municipally-owned utilities (PUCT)
  - IOU: Oncor (default for unassigned parcels in our 5 counties)
"""

import json
import sqlite3
import sys
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

DB_PATH = Path(__file__).parent.parent / "deals.db"
DATA_DIR = Path(__file__).parent.parent / "data/texas"

# Our 5 Texas counties + their IOU default
IOU_DEFAULTS = {
    "dallas": "Oncor Electric Delivery",
    "travis": "Oncor Electric Delivery",
    "grayson": "Oncor Electric Delivery",
    "hood": "Oncor Electric Delivery",
    "navarro": "Oncor Electric Delivery",
}

TEXAS_COUNTIES = list(IOU_DEFAULTS.keys())

# The COOP/MUNI datasets use Web Mercator (3857).
# To do proper containment tests with our WGS84 lat/lng parcels,
# we must transform or use intersects with buffered points.
# We'll project the parcel points to 3857 for the spatial query.


def load_utility_polygons(feature_path, name_field):
    """Load GeoJSON features and return (geoms, names) in WGS84."""
    print(f"  Loading {feature_path.name}...")
    with open(feature_path) as f:
        data = json.load(f)

    geoms = []
    names = []
    n_invalid = 0
    n_fixed = 0
    for feat in data["features"]:
        try:
            g = shape(feat["geometry"])
            if g.is_empty:
                continue
            if not g.is_valid:
                n_invalid += 1
                g = g.buffer(0)
                if g.is_empty:
                    continue
                n_fixed += 1
            geoms.append(g)
            names.append(feat["properties"].get(name_field, "Unknown"))
        except Exception:
            continue
    if n_invalid:
        print(f"    Fixed {n_fixed}/{n_invalid} invalid polygons via buffer(0)")
    print(f"    Loaded {len(geoms)} polygons")
    print(f"    Loaded {len(geoms)} polygons")
    return geoms, names


def main():
    print("=== Loading utility boundary data ===")

    all_geoms = []
    all_names = []

    # COOP
    coop_path = DATA_DIR / "tx_coop_utility_boundaries.geojson"
    if coop_path.exists():
        g, n = load_utility_polygons(coop_path, "COMPANY_NAME")
        all_geoms.extend(g)
        all_names.extend(n)

    # MUNI
    muni_path = DATA_DIR / "tx_muni_utility_boundaries.geojson"
    if muni_path.exists():
        g, n = load_utility_polygons(muni_path, "COMPANY_NAME")
        all_geoms.extend(g)
        all_names.extend(n)

    print(f"\n  Total utility polygons: {len(all_geoms)}")
    print(f"  Unique utilities: {len(set(all_names))}")

    tree = STRtree(all_geoms)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get ALL Texas commercial_sites
    placeholders = ",".join("?" * len(TEXAS_COUNTIES))
    sites = conn.execute(f"""
        SELECT id, county, lat, lng, power_provider
        FROM commercial_sites
        WHERE county IN ({placeholders})
          AND lat IS NOT NULL AND lng IS NOT NULL
    """, TEXAS_COUNTIES).fetchall()

    print(f"\n=== Processing {len(sites)} Texas sites ===")

    matched_coop = 0
    matched_muni = 0
    matched_iou_default = 0
    unmatched = 0
    updated = 0
    already_set = 0

    for i, site in enumerate(sites):
        lat = site["lat"]
        lng = site["lng"]
        county = site["county"]

        if site["power_provider"] and site["power_provider"] != "—" and site["power_provider"].strip():
            already_set += 1
            continue

        pt = Point(lng, lat)

        # Query R-Tree for any utility polygon containing this point
        candidates = tree.query(pt)
        provider = None
        for idx in candidates:
            poly = all_geoms[idx]
            if poly.contains(pt) or poly.intersects(pt):
                provider = all_names[idx]
                if "COOP" in provider.upper() or "ELECTRIC" in provider.upper():
                    pass
                break

        if provider:
            conn.execute(
                "UPDATE commercial_sites SET power_provider = ? WHERE id = ?",
                (provider, site["id"]),
            )
            if "COOPERATIVE" in provider.upper() or any(
                kw in provider.upper() for kw in ["ELECTRIC COOP", "COOP"]
            ):
                matched_coop += 1
            else:
                matched_muni += 1
            updated += 1
        else:
            # Fall back to IOU default by county
            default_provider = IOU_DEFAULTS.get(county, "Oncor Electric Delivery")
            conn.execute(
                "UPDATE commercial_sites SET power_provider = ? WHERE id = ?",
                (default_provider, site["id"]),
            )
            matched_iou_default += 1
            updated += 1

        if i > 0 and i % 5000 == 0:
            print(f"    Progress: {i}/{len(sites)} (updated={updated})")

    conn.commit()

    print(f"\n=== Results ===")
    print(f"  Total Texas sites: {len(sites)}")
    print(f"  Already had provider: {already_set}")
    print(f"  Matched COOP: {matched_coop}")
    print(f"  Matched MUNI: {matched_muni}")
    print(f"  Matched IOU (default): {matched_iou_default}")
    print(f"  Updated: {updated}")

    print(f"\n=== Provider Distribution ===")
    provs = conn.execute(f"""
        SELECT power_provider, COUNT(*) as cnt
        FROM commercial_sites
        WHERE county IN ({placeholders})
        GROUP BY power_provider
        ORDER BY cnt DESC
    """, TEXAS_COUNTIES).fetchall()
    for p in provs:
        print(f"  {p['power_provider']:45s} {p['cnt']:>6d}")

    print(f"\n=== By County ===")
    for cty in TEXAS_COUNTIES:
        rows = conn.execute("""
            SELECT power_provider, COUNT(*) as cnt
            FROM commercial_sites
            WHERE county = ?
            GROUP BY power_provider
            ORDER BY cnt DESC
        """, (cty,)).fetchall()
        print(f"  {cty}:")
        for r in rows:
            print(f"    {r['power_provider']:45s} {r['cnt']:>6d}")

    conn.close()
    return updated


if __name__ == "__main__":
    main()
