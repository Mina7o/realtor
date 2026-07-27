"""D75: Austin Shortlist Refresh

Re-scores all Travis County sites with:
  - Correct Assembly Bonus (+50) only for parcels within 0.5mi of 345kV
  - Recalculated score_total from all score components
  - Reassigned score_tier based on new scores
  - Outputs Austin A-Tier Big Blocks list
"""

import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "deals.db"
OUTPUT_PATH = Path(__file__).parent.parent / "output/d75_austin_a_tier_big_blocks.csv"
COUNTY = "travis"

# Tier thresholds (matching existing convention)
TIER_THRESHOLDS = [
    ("A", 80),
    ("B", 60),
    ("C", 30),
    ("D", 0),
]

SCORE_FIELDS = [
    "score_acreage", "score_land_use", "score_vacancy", "score_owner",
    "score_density", "score_zoning", "score_flood", "score_econ_dev",
    "score_substation", "score_assembly", "score_fiber",
    "score_land_use_proximity", "score_transmission",
]


def get_tier(score):
    for tier, threshold in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "D"


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    print("[main] Resetting existing assembly bonus for Travis sites...")
    conn.execute(
        "UPDATE commercial_sites SET has_assembly_bonus = 0, score_assembly = 0 WHERE county = ?",
        (COUNTY,),
    )
    conn.commit()

    print("[main] Applying Assembly Bonus (+50) to parcels within 0.5mi of 345kV...")
    near_sites = conn.execute("""
        SELECT id FROM commercial_sites
        WHERE county = ?
          AND distance_transmission_miles IS NOT NULL
          AND CAST(distance_transmission_miles AS REAL) < 0.5
    """, (COUNTY,)).fetchall()
    n_near = len(near_sites)
    print(f"  {n_near} parcels within 0.5mi of 345kV")

    for site in near_sites:
        conn.execute(
            "UPDATE commercial_sites SET has_assembly_bonus = 1, score_assembly = 50 WHERE id = ?",
            (site["id"],),
        )
    conn.commit()

    print("[main] Recalculating score_total and tier for all Travis sites...")
    sites = conn.execute("""
        SELECT id FROM commercial_sites WHERE county = ?
    """, (COUNTY,)).fetchall()

    updated = 0
    for site in sites:
        row = conn.execute("""
            SELECT * FROM commercial_sites WHERE id = ?
        """, (site["id"],)).fetchone()

        total = 0
        for f in SCORE_FIELDS:
            v = row[f]
            if v is not None:
                try:
                    total += int(v)
                except (ValueError, TypeError):
                    pass

        tier = get_tier(total)
        conn.execute(
            "UPDATE commercial_sites SET score_total = ?, score_tier = ? WHERE id = ?",
            (total, tier, site["id"]),
        )
        updated += 1

    conn.commit()
    print(f"  {updated} sites rescored")

    print("[main] Generating Austin A-Tier Big Blocks list...")
    a_tier = conn.execute("""
        SELECT id, address, owner_name,
               CAST(acres AS REAL) as acres,
               zoning, attom_zoning_code, attom_land_use_desc,
               score_total, score_tier, score_assembly,
               CAST(distance_transmission_miles AS REAL) as distance_transmission_miles,
               lat, lng, has_assembly_bonus,
               power_provider
        FROM commercial_sites
        WHERE county = ?
          AND score_tier = 'A'
        ORDER BY score_total DESC, acres DESC
    """, (COUNTY,)).fetchall()

    print(f"  Austin A-Tier: {len(a_tier)} sites")

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "address", "owner_name", "acres", "zoning",
            "attom_zoning_code", "attom_land_use_desc",
            "score_total", "score_tier", "score_assembly",
            "distance_transmission_miles", "power_provider",
            "has_assembly_bonus", "lat", "lng",
        ])
        for s in a_tier:
            writer.writerow([
                s["id"], s["address"], s["owner_name"], s["acres"],
                s["zoning"], s["attom_zoning_code"],
                s["attom_land_use_desc"],
                s["score_total"], s["score_tier"], s["score_assembly"],
                s["distance_transmission_miles"], s["power_provider"],
                s["has_assembly_bonus"], s["lat"], s["lng"],
            ])

    print(f"  Written to {OUTPUT_PATH}")

    # Summary
    print("\n=== Austin Post-Refresh Summary ===")
    zoning_breakdown = conn.execute("""
        SELECT zoning, COUNT(*) as cnt,
               ROUND(SUM(CAST(acres AS REAL)), 0) as total_ac
        FROM commercial_sites
        WHERE county = ? AND score_tier = 'A'
        GROUP BY zoning ORDER BY cnt DESC
    """, (COUNTY,)).fetchall()
    for z in zoning_breakdown:
        print(f"  {z['zoning'] or 'UNKNOWN':15s} {z['cnt']:>5d} parcels  {z['total_ac']:>8,.0f} ac")

    tier_counts = conn.execute("""
        SELECT score_tier, COUNT(*) as c FROM commercial_sites
        WHERE county = ? GROUP BY score_tier ORDER BY score_tier
    """, (COUNTY,)).fetchall()
    print("\n  Tier distribution:")
    for t in tier_counts:
        print(f"    {t['score_tier']}: {t['c']}")

    near_final = conn.execute("""
        SELECT COUNT(*) as c FROM commercial_sites
        WHERE county = ? AND has_assembly_bonus = 1
    """, (COUNTY,)).fetchone()
    print(f"\n  Parcels with Assembly Bonus: {near_final['c']}")

    conn.close()


if __name__ == "__main__":
    main()
