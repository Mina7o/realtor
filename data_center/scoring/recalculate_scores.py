"""Recalculate commercial_site scores incorporating zoning, flood, econ_dev data.
Run after enrich_sites.py has populated the enrichment columns.

New scoring (total still out of 100):
  acreage: 25  | land_use: 25 | vacancy: 10 | owner_type: 10 | density: 5
  zoning:  15  | flood: 5     | econ_dev: 5
"""

import sqlite3
import os
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))


def calc_zoning_score(zoning):
    if not zoning:
        return 0
    z = zoning.upper().strip()
    industrial = any(kw in z for kw in ("IND", "I-", "ML-", "LI", "HI", "M-1", "M-2", "CBI"))
    commercial = any(kw in z for kw in ("C-", "COM", "OFC", "B-", "NS", "NC", "N1", "N2"))
    econ_dev = any(kw in z for kw in ("ED-", "REDA", "EDE"))
    if industrial: return 15
    if commercial: return 8
    if econ_dev: return 10
    return 0


def calc_flood_score(flood_zone):
    if not flood_zone:
        return 5
    f = flood_zone.upper()
    if "FLOODWAY" in f or "AE" in f or "AH" in f or "100" in f:
        return 0
    return 3


def calc_econ_score(econ_dev):
    if not econ_dev:
        return 0
    return 5


def main():
    conn = sqlite3.connect(str(DB))
    rows = conn.execute("""
        SELECT id, score_acreage, score_land_use, score_vacancy, score_owner, score_density,
               score_zoning, score_flood, score_econ_dev,
               zoning, flood_zone, econ_dev_zone
        FROM commercial_sites
    """).fetchall()

    changed = 0
    for r in rows:
        (sid, sa, slu, sv, so, sd, sz_old, sf_old, se_old,
         zoning, flood, econ) = r

        sz = calc_zoning_score(zoning)
        sf = calc_flood_score(flood)
        se = calc_econ_score(econ)

        # Keep old sub-scores but use new total (rescaling legacy scores)
        # Legacy: ac 30 + lu 30 + vac 20 + own 10 + den 10 = 100
        # New:    ac 25 + lu 25 + vac 10 + own 10 + den 5 + zone 15 + flood 5 + econ 5 = 100
        # Rescale: ac_new=sa*25/30, lu_new=slu*25/30, vac_new=sv*10/20, den_new=sd*5/10
        sa = int(sa) if sa else 0
        slu = int(slu) if slu else 0
        sv = int(sv) if sv else 0
        sd = int(sd) if sd else 0
        so = int(so) if so else 0
        sa_scaled = round(sa * 25 / 30)
        slu_scaled = round(slu * 25 / 30)
        sv_scaled = round(sv * 10 / 20)
        sd_scaled = round(sd * 5 / 10)
        so_val = so

        total = min(sa_scaled + slu_scaled + sv_scaled + so_val + sd_scaled + sz + sf + se, 100)
        tier = "A" if total >= 65 else ("B" if total >= 45 else ("C" if total >= 25 else "D"))

        old = conn.execute("SELECT score_total, score_tier FROM commercial_sites WHERE id=?", (sid,)).fetchone()
        old_total = int(old[0]) if old and old[0] else 0
        old_tier = old[1] if old else ""
        if sz != sz_old or sf != sf_old or se != se_old or old_total != total or old_tier != tier:
            conn.execute(
                "UPDATE commercial_sites SET score_zoning=?, score_flood=?, score_econ_dev=?, score_total=?, score_tier=? WHERE id=?",
                (sz, sf, se, total, tier, sid)
            )
            changed += 1

    conn.commit()
    conn.close()
    print(f"Updated {changed} sites")


if __name__ == "__main__":
    main()
