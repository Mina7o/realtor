"""Score large parcels across Union, York, Mecklenburg for data center readiness.
Run: python -m data_center.score_sites
Scores are saved to deals.db table `commercial_sites`."""

import sqlite3
import re
import os
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))
MECK_DB = Path(os.path.expanduser("~/Documents/proj/realtor/county_parcels_full.db"))
COLS = [
    "pid", "county", "address", "owner_name", "owner_city", "owner_state",
    "acres", "total_value", "land_value", "bldg_value",
    "land_use", "neighborhood",
    "score_acreage", "score_land_use", "score_vacancy", "score_owner", "score_density",
    "score_total", "score_tier",
    "last_sale_price", "last_sale_date", "last_sale_grantor",
    "s1_source"
]


def owner_type_score(owner_name):
    if not owner_name:
        return 0
    name = owner_name.upper()
    if any(kw in name for kw in ("COUNTY", "CITY OF", "TOWN OF", "BOARD OF EDUCATION",
                                  "STATE OF", "DEPARTMENT", "MECKLENBURG COUNTY",
                                  "CHARLOTTE MECKLENBURG", "DOT", "NCDOT")):
        return 0
    if any(kw in name for kw in ("CHURCH", "BAPTIST", "METHODIST", "PRESBYTERIAN",
                                  "MINISTRY", "FELLOWSHIP", "CATHOLIC")):
        return 0
    if any(kw in name for kw in (" LLC", " INC", " LP", " CORPORATION", " CORP",
                                  " COMPANY", " CO ", " LTD", " PARTNERSHIP",
                                  " HOLDINGS", " PROPERTIES", " REALTY",
                                  " INVESTMENTS", " VENTURES", " TRUST")):
        return 10
    if any(kw in name for kw in (" ET AL", " HEIRS", " FAMILY", " REVOCABLE",
                                  " TRUSTEE", " LIVING TRUST")):
        return 5
    return 5


def land_use_score(use, county):
    if not use:
        return 5
    u = use.upper().strip()
    if county == "union":
        if u == "IND":
            return 30
        if u == "COM":
            return 20
        if u == "FARM":
            return 20
        if u == "UTIL":
            return 15
        if u == "OTHER":
            return 10
        if u == "RES":
            return 5
        if u == "EXEMPT":
            return 0
    if county == "mecklenburg":
        if "INDUSTRIAL" in u:
            return 30
        if "COMMERCIAL" in u or "OFFICE" in u:
            return 20
        if "FARM" in u or "AGRICULT" in u or "RURAL" in u:
            return 20
        if "VACANT" in u or "WASTELAND" in u:
            return 15
        if "RESIDENTIAL" in u or "SINGLE FAMILY" in u or "MULTI FAMILY" in u:
            return 5
        if "MUNICIPAL" in u or "COUNTY" in u or "GOVT" in u or "SCHOOL" in u:
            return 0
        if "CHURCH" in u:
            return 0
        if "UTILITY" in u:
            return 15
    if county == "york":
        if "FARM" in u or "AGRICULT" in u:
            return 20
        if "COMMERCIAL" in u:
            return 20
        if "RESIDENTIAL" in u or "RES " in u:
            return 5
        if "EXEMPT" in u:
            return 0
        if "VACANT" in u:
            return 15
    return 10


def build_union_sites(conn):
    rows = conn.execute("""
        SELECT pid, physstradd, curr_name1, curr_city, curr_state,
               gross_acres, fmv_total, fmv_land, fmv_imprv,
               property_use, nbhdname,
               s1_salesamt, s1_saledate, s1_grantor,
               curr_name2
        FROM union_parcels
        WHERE gross_acres >= 10
    """).fetchall()
    sites = []
    for r in rows:
        ac = r[5] or 0
        score_ac = 5 if ac < 20 else (15 if ac < 50 else (20 if ac < 100 else (25 if ac < 200 else 30)))
        score_lu = land_use_score(r[9], "union")
        bldg = r[8] or 0
        score_vac = 20 if bldg == 0 else 5
        score_own = owner_type_score(r[2])
        score_den = 10  # will be refined per neighborhood
        total = min(score_ac + score_lu + score_vac + score_own + score_den, 100)
        tier = "A" if total >= 70 else ("B" if total >= 50 else ("C" if total >= 30 else "D"))
        sites.append({
            "pid": r[0], "county": "union", "address": r[1] or "",
            "owner_name": r[2] or "", "owner_city": r[3] or "", "owner_state": r[4] or "",
            "acres": round(ac, 1), "total_value": r[6], "land_value": r[7], "bldg_value": bldg,
            "land_use": r[9] or "", "neighborhood": (r[10] or "").strip(),
            "score_acreage": score_ac, "score_land_use": score_lu,
            "score_vacancy": score_vac, "score_owner": score_own, "score_density": score_den,
            "score_total": total, "score_tier": tier,
            "last_sale_price": r[11], "last_sale_date": r[12], "last_sale_grantor": r[13] or "",
            "s1_source": "union_parcels"
        })
    return sites


def build_york_sites(conn):
    rows = conn.execute("""
        SELECT parcelid, property_address, owner1, mail_city, mail_state,
               gis_acres, apr_tot_val, apr_land_val, apr_bldg_val,
               land_use_desc, neighborhood_desc,
               sale_price, date_sold, owner2
        FROM york_parcels
        WHERE gis_acres >= 10
    """).fetchall()
    sites = []
    for r in rows:
        ac = r[5] or 0
        score_ac = 5 if ac < 20 else (15 if ac < 50 else (20 if ac < 100 else (25 if ac < 200 else 30)))
        score_lu = land_use_score(r[9], "york")
        bldg = r[8] or 0
        score_vac = 20 if bldg == 0 else 5
        score_own = owner_type_score(r[2])
        score_den = 10
        total = min(score_ac + score_lu + score_vac + score_own + score_den, 100)
        tier = "A" if total >= 70 else ("B" if total >= 50 else ("C" if total >= 30 else "D"))
        sites.append({
            "pid": r[0], "county": "york", "address": r[1] or "",
            "owner_name": r[2] or "", "owner_city": r[3] or "", "owner_state": r[4] or "",
            "acres": round(ac, 1), "total_value": r[6], "land_value": r[7], "bldg_value": bldg,
            "land_use": r[9] or "", "neighborhood": (r[10] or "").strip(),
            "score_acreage": score_ac, "score_land_use": score_lu,
            "score_vacancy": score_vac, "score_owner": score_own, "score_density": score_den,
            "score_total": total, "score_tier": tier,
            "last_sale_price": r[11], "last_sale_date": r[12], "last_sale_grantor": r[13] or "",
            "s1_source": "york_parcels"
        })
    return sites


def build_meck_sites(mconn):
    rows = mconn.execute("""
        SELECT pid, situsaddress1, full_owner_name, txt_city, txt_state,
               num_totalac, amt_totalvalue, amt_landvalue, amt_netbldgvalue,
               txt_propertyuse_desc, txt_mailaddr1
        FROM mecklenburg_parcels
        WHERE num_totalac >= 435600
    """).fetchall()
    sites = []
    for r in rows:
        ac_sqft = r[5] or 0
        ac = round(ac_sqft / 43560, 1)
        if ac < 10:
            continue
        score_ac = 5 if ac < 20 else (15 if ac < 50 else (20 if ac < 100 else (25 if ac < 200 else 30)))
        score_lu = land_use_score(r[9], "mecklenburg")
        bldg = r[8] or 0
        score_vac = 20 if bldg == 0 else 5
        score_own = owner_type_score(r[2])
        score_den = 10
        total = min(score_ac + score_lu + score_vac + score_own + score_den, 100)
        tier = "A" if total >= 70 else ("B" if total >= 50 else ("C" if total >= 30 else "D"))
        sites.append({
            "pid": r[0], "county": "mecklenburg", "address": r[1] or "",
            "owner_name": r[2] or "", "owner_city": r[3] or "", "owner_state": r[4] or "",
            "acres": ac, "total_value": r[6], "land_value": r[7], "bldg_value": bldg,
            "land_use": r[9] or "", "neighborhood": "",
            "score_acreage": score_ac, "score_land_use": score_lu,
            "score_vacancy": score_vac, "score_owner": score_own, "score_density": score_den,
            "score_total": total, "score_tier": tier,
            "last_sale_price": None, "last_sale_date": None, "last_sale_grantor": "",
            "s1_source": "mecklenburg_parcels"
        })
    return sites


def save_sites(conn, sites):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS commercial_sites")
    col_defs = ", ".join(f'"{c}"' for c in COLS)
    cur.execute(f"""
        CREATE TABLE commercial_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {', '.join(f'"{c}" TEXT' for c in COLS)}
        )
    """)
    placeholders = ", ".join("?" for _ in COLS)
    values_list = []
    for s in sites:
        row = [str(s.get(c, "")) for c in COLS]
        values_list.append(row)
    cur.executemany(f"INSERT INTO commercial_sites ({col_defs}) VALUES ({placeholders})", values_list)
    conn.commit()
    return len(values_list)


def compute_density_scores(conn):
    """Refine density scores per neighborhood in Union county."""
    cur = conn.cursor()
    nbhds = cur.execute("""
        SELECT nbhdname, SUM(CASE WHEN property_use = 'IND' THEN gross_acres ELSE 0 END),
               COUNT(CASE WHEN property_use = 'IND' THEN 1 END)
        FROM union_parcels GROUP BY nbhdname
    """).fetchall()
    dense_nbhds = {}
    for r in nbhds:
        name = (r[0] or "").strip()
        ind_ac = r[1] or 0
        ind_cnt = r[2] or 0
        dense_nbhds[name] = ind_ac
    return dense_nbhds


def main():
    print("Scoring commercial sites for data center readiness...")
    conn = sqlite3.connect(str(DB))

    union_sites = build_union_sites(conn)
    print(f"  Union: {len(union_sites)} parcels 10+ ac")

    york_sites = build_york_sites(conn)
    print(f"  York: {len(york_sites)} parcels 10+ ac")

    mconn = sqlite3.connect(str(MECK_DB))
    meck_sites = build_meck_sites(mconn)
    mconn.close()
    print(f"  Mecklenburg: {len(meck_sites)} parcels 10+ ac")

    all_sites = union_sites + york_sites + meck_sites
    dense_nbhds = compute_density_scores(conn)

    for s in all_sites:
        nbhd = s["neighborhood"]
        ind_ac = dense_nbhds.get(nbhd, 0)
        if ind_ac >= 100:
            s["score_density"] = 10
        elif ind_ac >= 30:
            s["score_density"] = 7
        elif ind_ac > 0:
            s["score_density"] = 5
        else:
            s["score_density"] = 0

        s["score_total"] = min(
            s["score_acreage"] + s["score_land_use"] + s["score_vacancy"]
            + s["score_owner"] + s["score_density"], 100
        )
        t = s["score_total"]
        s["score_tier"] = "A" if t >= 70 else ("B" if t >= 50 else ("C" if t >= 30 else "D"))

    all_sites.sort(key=lambda s: (-s["score_total"], -s["acres"]))

    n = save_sites(conn, all_sites)
    conn.close()

    tiers = {}
    for s in all_sites:
        tiers.setdefault(s["score_tier"], 0)
        tiers[s["score_tier"]] += 1

    print(f"\nSaved {n} sites to commercial_sites table")
    print(f"Tiers: {dict(sorted(tiers.items()))}")
    print(f"Top 5:")
    for s in all_sites[:5]:
        print(f"  [{s['score_tier']} {s['score_total']}pts] {s['county']}: {s['acres']}ac {s['address']} — {s['owner_name'][:40]}")


if __name__ == "__main__":
    main()
