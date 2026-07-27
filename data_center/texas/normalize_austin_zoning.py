"""D74: Austin Zoning Normalizer

Spatial join between commercial_sites (Travis County parcels) and
Austin zoning polygons. Maps Austin's zoning codes into our 4-tier
[INDUSTRIAL, COMMERCIAL, AGRICULTURAL, RESIDENTIAL] schema.
"""

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

DB_PATH = Path(__file__).parent.parent / "deals.db"
ZONING_PATH = Path(__file__).parent.parent / "data/texas/austin_zoning.geojson"
COUNTY = "travis"

AUSTIN_ZONING_MAP = {
    "AG": "AGRICULTURAL",
    "AV": "AGRICULTURAL",
    "ERC": "AGRICULTURAL",
    "LA": "AGRICULTURAL",
    "CS": "COMMERCIAL",
    "GR": "COMMERCIAL",
    "LR": "COMMERCIAL",
    "MR": "COMMERCIAL",
    "CR": "COMMERCIAL",
    "DMU": "COMMERCIAL",
    "CBD": "COMMERCIAL",
    "W/LO": "COMMERCIAL",
    "GO": "COMMERCIAL",
    "LO": "COMMERCIAL",
    "NBG": "COMMERCIAL",
    "PUD": "COMMERCIAL",
    "TOD": "COMMERCIAL",
    "CH": "COMMERCIAL",
    "P": "COMMERCIAL",
    "UNZ": "COMMERCIAL",
    "I": "INDUSTRIAL",
    "IP": "INDUSTRIAL",
    "LI": "INDUSTRIAL",
    "MI": "INDUSTRIAL",
    "R&D": "INDUSTRIAL",
    "SF": "RESIDENTIAL",
    "MF": "RESIDENTIAL",
    "MH": "RESIDENTIAL",
    "RR": "RESIDENTIAL",
    "DR": "RESIDENTIAL",
    "NO": "RESIDENTIAL",
    "TND": "RESIDENTIAL",
}


def load_zoning():
    """Load Austin zoning polygons and build R-Tree index."""
    print("[load_zoning] Loading Austin zoning GeoJSON...")
    with open(ZONING_PATH) as f:
        data = json.load(f)

    geoms = []
    props = []
    skipped = 0
    for feat in data["features"]:
        try:
            g = shape(feat["geometry"])
            if g.is_valid and not g.is_empty:
                geoms.append(g)
                props.append(feat["properties"])
            else:
                skipped += 1
        except Exception:
            skipped += 1

    tree = STRtree(geoms)
    n = len(geoms)
    print(f"[load_zoning] {n} valid polygons loaded ({skipped} skipped)")
    return tree, geoms, props, n


def classify_zoning(zoning_base):
    """Map Austin zoning_base to our 4-tier schema."""
    base = (zoning_base or "").strip().upper()
    for prefix in sorted(AUSTIN_ZONING_MAP.keys(), key=len, reverse=True):
        if base.startswith(prefix):
            return AUSTIN_ZONING_MAP[prefix]
    if base:
        print(f"[classify] Unrecognized code '{base}' — defaulting to COMMERCIAL")
    return None


def main():
    tree, geoms, props, n_geoms = load_zoning()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    sites = conn.execute("""
        SELECT id, lat, lng, attom_zoning_code, attom_land_use_desc
        FROM commercial_sites
        WHERE county = ?
          AND lat IS NOT NULL AND lng IS NOT NULL
    """, (COUNTY,)).fetchall()
    total = len(sites)
    print(f"[main] {total} Travis County sites to process")

    updated = 0
    matched = 0
    unmatched = 0
    codes_found = set()

    for i, site in enumerate(sites):
        lat = site["lat"]
        lng = site["lng"]
        pt = Point(lng, lat)

        candidates = tree.query(pt)
        zoning_base = None
        for idx in candidates:
            poly = geoms[idx]
            if poly.contains(pt) or poly.intersects(pt):
                zoning_base = props[idx].get("zoning_base", "")
                break

        if not zoning_base:
            unmatched += 1
            continue

        codes_found.add(zoning_base)
        tier = classify_zoning(zoning_base)
        if tier is None:
            unmatched += 1
            continue

        conn.execute(
            "UPDATE commercial_sites SET zoning = ?, attom_zoning_code = ? WHERE id = ?",
            (tier, zoning_base, site["id"]),
        )
        updated += 1
        matched += 1

        if i % 500 == 0 and i > 0:
            print(f"[main] Progress: {i}/{total} (matched={matched}, unmatched={unmatched})")

    conn.commit()

    unmatched_total = total - matched
    print(f"\n[main] DONE")
    print(f"  Sites processed: {total}")
    print(f"  Matched: {matched}")
    print(f"  Unmatched (not in Austin city limits / zoning gaps): {unmatched_total}")
    print(f"  Rows updated: {updated}")
    print(f"  Unique zoning codes used: {len(codes_found)}")
    for code in sorted(codes_found):
        count = conn.execute(
            "SELECT COUNT(*) as c FROM commercial_sites WHERE county = ? AND attom_zoning_code = ?",
            (COUNTY, code),
        ).fetchone()
        tier = conn.execute(
            "SELECT zoning FROM commercial_sites WHERE county = ? AND attom_zoning_code = ? LIMIT 1",
            (COUNTY, code),
        ).fetchone()
        print(f"    {code:>6s} → {str(tier['zoning']):15s} ({count['c']} parcels)")

    conn.close()
    return matched, unmatched_total


if __name__ == "__main__":
    main()
