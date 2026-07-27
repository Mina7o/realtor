"""Compute Assembly Multiplier for commercial_sites.
A parcel gets +50 bonus if its neighbors (same owner or same zoning)
bring total contiguous acreage above 50 acres.

Run: python -m data_center.aggregation_score

Adds columns: score_assembly, has_assembly_bonus
Updates score_total and score_tier with the multiplier.
"""

import sqlite3, math
from pathlib import Path

DB = Path.home() / "Documents/proj/realtor/deals.db"
RADIUS_DEG = 0.004  # ~1400ft adjacency radius for large parcels


def compute_assembly_bonus(conn):
    sites = conn.execute("""
        SELECT id, owner_name, CAST(acres AS REAL), lat, lng, zoning,
               score_total, score_tier
        FROM commercial_sites
        WHERE lat IS NOT NULL
    """).fetchall()

    site_map = {s[0]: {"owner": (s[1] or "").upper().strip(),
                        "acres": s[2] or 0,
                        "lat": s[3], "lng": s[4],
                        "zoning": (s[5] or "").upper().strip(),
                        "score": s[6] or 0,
                        "tier": s[7] or "D"}
                for s in sites}
    ids = list(site_map.keys())

    # For each site, find neighbors in the same owner group or same zoning group
    # that sum to 50+ contiguous acres
    bonuses = {}  # site_id -> bonus (0 or 50)

    # Build a spatial index: group sites by coarse grid cell
    grid_size = RADIUS_DEG * 2
    grid = {}
    for sid, si in site_map.items():
        gx = int(si["lat"] / grid_size) if si["lat"] else 0
        gy = int(si["lng"] / grid_size) if si["lng"] else 0
        grid.setdefault((gx, gy), []).append(sid)

    for i, sid in enumerate(ids):
        if i % 1000 == 0:
            print(f"  Processing site {i}/{len(ids)}...")
        si = site_map[sid]
        if si["lat"] is None:
            bonuses[sid] = 0
            continue

        gx = int(si["lat"] / grid_size)
        gy = int(si["lng"] / grid_size)

        same_owner = {sid}
        same_zoning = {sid}

        # Check this cell and all 8 neighbors
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cell = grid.get((gx + dx, gy + dy), [])
                for tid in cell:
                    if tid == sid:
                        continue
                    tj = site_map[tid]
                    d = math.sqrt((si["lat"] - tj["lat"])**2 + (si["lng"] - tj["lng"])**2)
                    if d > RADIUS_DEG:
                        continue
                    if si["owner"] and si["owner"] == tj["owner"] and si["owner"] != "":
                        same_owner.add(tid)
                    elif si["zoning"] and si["zoning"] not in ("", "UNKNOWN", "?") and si["zoning"] == tj["zoning"]:
                        same_zoning.add(tid)

        owner_ac = sum(site_map[s]["acres"] for s in same_owner)
        zoning_ac = sum(site_map[s]["acres"] for s in same_zoning)
        bonus = 50 if (owner_ac >= 50 or zoning_ac >= 50) else 0
        bonuses[sid] = bonus

    return bonuses


def update_db(conn, bonuses):
    cur = conn.cursor()
    # Add columns if missing
    existing = [c[1] for c in cur.execute("PRAGMA table_info(commercial_sites)").fetchall()]
    if "score_assembly" not in existing:
        cur.execute("ALTER TABLE commercial_sites ADD COLUMN score_assembly INTEGER DEFAULT 0")
    if "has_assembly_bonus" not in existing:
        cur.execute("ALTER TABLE commercial_sites ADD COLUMN has_assembly_bonus INTEGER DEFAULT 0")

    changed = 0
    for sid, bonus in bonuses.items():
        row = cur.execute(
            "SELECT score_acreage, score_land_use, score_vacancy, score_owner, "
            "score_density, score_zoning, score_flood, score_econ_dev, score_total, score_tier "
            "FROM commercial_sites WHERE id=?", (sid,)
        ).fetchone()
        if not row:
            continue
        (sa, slu, sv, so, sd, sz, sf, se, old_total, old_tier) = row
        sa = int(sa or 0)
        slu = int(slu or 0)
        sv = int(sv or 0)
        sd = int(sd or 0)
        so = int(so or 0)
        sz = int(sz or 0)
        sf = int(sf or 0)
        se = int(se or 0)

        base = sa + slu + sv + so + sd + sz + sf + se
        total = min(base + bonus, 150)
        tier = "A" if total >= 100 else ("B" if total >= 70 else ("C" if total >= 40 else "D"))

        cur.execute(
            "UPDATE commercial_sites SET score_assembly=?, has_assembly_bonus=?, "
            "score_total=?, score_tier=? WHERE id=?",
            (bonus, 1 if bonus > 0 else 0, total, tier, sid)
        )
        changed += 1

    conn.commit()
    return changed


def main():
    print("Computing Assembly Multiplier...")
    conn = sqlite3.connect(str(DB))
    print("Loaded commercial_sites")
    bonuses = compute_assembly_bonus(conn)
    bonus_count = sum(1 for b in bonuses.values() if b > 0)
    print(f"  Sites with assembly bonus (+50): {bonus_count}")
    changed = update_db(conn, bonuses)
    conn.close()
    print(f"Updated {changed} sites with Assembly Multiplier scores")

    # Summary
    conn2 = sqlite3.connect(str(DB))
    tiers = conn2.execute(
        "SELECT score_tier, COUNT(*) FROM commercial_sites GROUP BY score_tier ORDER BY score_tier"
    ).fetchall()
    bonus_tiers = conn2.execute(
        "SELECT score_tier, COUNT(*) FROM commercial_sites WHERE has_assembly_bonus=1 GROUP BY score_tier"
    ).fetchall()
    conn2.close()
    print(f"\nTier distribution (all):")
    for t, c in tiers:
        print(f"  {t}: {c}")
    print(f"\nTier distribution (with bonus):")
    for t, c in bonus_tiers:
        print(f"  {t}: {c}")
    print(f"\nDone. A parcel display now shows assembly bonus in the score breakdown.")


if __name__ == "__main__":
    main()
