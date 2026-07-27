"""
Directive 27: NC OneMap Bulk Ingest
Match our 17,051 commercial sites to the NC Parcels GeoPackage
to restore zoning/owner data for $0.

Strategy:
  Phase 1 — Load commercial sites from deals.db
  Phase 2 — For each county, load matching parcels from GeoPackage via bounding box
  Phase 3 — Update commercial_sites with owner, zoning, land use from parcel match
"""

import os
import sys
import sqlite3
import argparse
import time
from pathlib import Path

import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import Point


DB_PATH = Path(__file__).parent / "deals.db"
GPKG_PATH = Path(__file__).parent / "data" / "nc-parcels" / "NC_Parcels_all.gpkg"
BATCH_SIZE = 500

CNTY_FIPS = {
    "Alamance": 1, "Alexander": 3, "Alleghany": 5, "Anson": 7, "Ashe": 9,
    "Avery": 11, "Beaufort": 13, "Bertie": 15, "Bladen": 17, "Brunswick": 19,
    "Buncombe": 21, "Burke": 23, "Cabarrus": 25, "Caldwell": 27, "Camden": 29,
    "Carteret": 31, "Caswell": 33, "Catawba": 35, "Chatham": 37, "Cherokee": 39,
    "Chowan": 41, "Clay": 43, "Cleveland": 45, "Columbus": 47, "Craven": 49,
    "Cumberland": 51, "Currituck": 53, "Dare": 55, "Davidson": 57, "Davie": 59,
    "Duplin": 61, "Durham": 63, "Edgecombe": 65, "Forsyth": 67, "Franklin": 69,
    "Gaston": 71, "Gates": 73, "Graham": 75, "Granville": 77, "Greene": 79,
    "Guilford": 81, "Halifax": 83, "Harnett": 85, "Haywood": 87, "Henderson": 89,
    "Hertford": 91, "Hoke": 93, "Hyde": 95, "Iredell": 97, "Jackson": 99,
    "Johnston": 101, "Jones": 103, "Lee": 105, "Lenoir": 107, "Lincoln": 109,
    "McDowell": 111, "Macon": 113, "Madison": 115, "Martin": 117, "Mecklenburg": 119,
    "Mitchell": 121, "Montgomery": 123, "Moore": 125, "Nash": 127, "New Hanover": 129,
    "Northampton": 131, "Onslow": 133, "Orange": 135, "Pamlico": 137, "Pasquotank": 139,
    "Pender": 141, "Perquimans": 143, "Person": 145, "Pitt": 147, "Polk": 149,
    "Randolph": 151, "Richmond": 153, "Robeson": 155, "Rockingham": 157,
    "Rowan": 159, "Rutherford": 161, "Sampson": 163, "Scotland": 165, "Stanly": 167,
    "Stokes": 169, "Surry": 171, "Swain": 173, "Transylvania": 175, "Tyrrell": 177,
    "Union": 179, "Vance": 181, "Wake": 183, "Warren": 185, "Washington": 187,
    "Watauga": 189, "Wayne": 191, "Wilkes": 193, "Wilson": 195, "Yadkin": 197,
    "Yancey": 199,
}


def load_commercial_sites(db_path, counties=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM commercial_sites"
    params = []
    if counties:
        placeholders = ",".join("?" * len(counties))
        query += f" WHERE county IN ({placeholders})"
        params = counties
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    
    sites = []
    for r in rows:
        lat = r["lat"]
        lng = r["lng"]
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            lat, lng = None, None
        
        sites.append({
            "id": r["id"],
            "county": r["county"],
            "address": r["address"] or "",
            "owner_name": r["owner_name"] or "",
            "zoning": r["zoning"] or "",
            "land_use": r["land_use"] or "",
            "pin": "",
            "lat": lat,
            "lng": lng,
        })
    return sites


def build_parcel_index(gpkg_path, county_fips_int):
    """Load parcels for one county from GeoPackage into memory for matching."""
    fips_str = f"{37000 + county_fips_int}" if county_fips_int < 1000 else str(county_fips_int)
    stcntyfips = str(37000 + county_fips_int)
    
    where = f"stcntyfips = '{stcntyfips}'"
    
    cols = ["parno", "ownname", "siteadd", "scity", "sstate", "gisacres",
            "parusecode", "parusedesc", "improvval", "landval", "parval",
            "saledate"]
    
    try:
        gdf = gpd.read_file(
            gpkg_path,
            layer="nc_parcels_poly",
            where=where,
            columns=cols,
            rows=100000,
        )
    except Exception as e:
        print(f"    Error loading parcels: {e}")
        return None, []
    
    if len(gdf) == 0:
        return None, []
    
    print(f"    Loaded {len(gdf):,} parcels")
    return gdf, cols


def match_by_spatial(sites_batch, parcel_gdf):
    """Point-in-polygon match for a batch of sites."""
    matched = {}
    points = []
    site_map = []
    
    for i, s in enumerate(sites_batch):
        if s["lat"] and s["lng"]:
            points.append(Point(s["lng"], s["lat"]))
            site_map.append(i)
    
    if not points:
        return matched
    
    points_gdf = gpd.GeoDataFrame(geometry=points, crs="EPSG:4326")
    
    if parcel_gdf.crs is not None and parcel_gdf.crs.to_string() != "EPSG:4326":
        parcel_gdf_4326 = parcel_gdf.to_crs("EPSG:4326")
    else:
        parcel_gdf_4326 = parcel_gdf
    
    joined = gpd.sjoin(points_gdf, parcel_gdf_4326, how="left", predicate="within")
    
    for idx, row in joined.iterrows():
        if idx < len(site_map) and pd.notna(row.get("index_right")):
            site_idx = site_map[idx]
            matched[site_idx] = row
    return matched


def update_site(db_path, site_id, updates):
    """Update a commercial_site row with new data."""
    conn = sqlite3.connect(str(db_path))
    set_parts = []
    params = []
    for col, val in updates.items():
        if val is not None and val != "":
            set_parts.append(f"{col} = ?")
            params.append(val)
    
    if set_parts:
        params.append(site_id)
        sql = f"UPDATE commercial_sites SET {', '.join(set_parts)} WHERE id = ?"
        conn.execute(sql, params)
        conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Ingest NC OneMap parcels data")
    parser.add_argument("--counties", nargs="+", default=None,
                        help="Specific counties to process (default: all with sites)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be updated without writing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit sites to process (for testing)")
    parser.add_argument("--match-mode", choices=["spatial", "pin", "address", "all"],
                        default="all", help="Matching strategy")
    args = parser.parse_args()
    
    if not GPKG_PATH.exists():
        print(f"ERROR: GeoPackage not found at {GPKG_PATH}")
        sys.exit(1)
    
    print("Loading commercial sites from database...")
    sites = load_commercial_sites(DB_PATH, args.counties)
    if args.limit:
        sites = sites[:args.limit]
    print(f"  {len(sites)} sites loaded")
    
    updates_applied = 0
    matches_found = 0
    
    county_groups = {}
    for s in sites:
        c = s["county"]
        if c:
            county_groups.setdefault(c, []).append(s)
    
    for county, county_sites in sorted(county_groups.items()):
        fips = CNTY_FIPS.get(county, "" if isinstance(county, int) else None)
        if fips is None:
            print(f"  Skipping {county}: unknown FIPS code")
            continue
        
        print(f"\n{'='*60}")
        print(f"County: {county} (FIPS {fips}) — {len(county_sites)} sites")
        
        parcel_gdf, cols = build_parcel_index(GPKG_PATH, fips)
        if parcel_gdf is None or len(parcel_gdf) == 0:
            print(f"  No parcels found for {county} (FIPS {fips})")
            continue
        
        if parcel_gdf.crs is not None and parcel_gdf.crs.to_epsg() != 4326:
            parcel_gdf = parcel_gdf.to_crs("EPSG:4326")
        
        sites_with_geo = [
            (i, s) for i, s in enumerate(county_sites)
            if s["lat"] and s["lng"]
        ]
        
        if not sites_with_geo:
            print(f"  No sites with coordinates for {county}")
            continue
        
        points_gdf = gpd.GeoDataFrame(
            {"site_idx": [si for si, _ in sites_with_geo]},
            geometry=[Point(s["lng"], s["lat"]) for _, s in sites_with_geo],
            crs="EPSG:4326",
        )
        
        joined = gpd.sjoin(points_gdf, parcel_gdf, how="left", predicate="within")
        
        seen_sites = set()
        for _, row in joined.iterrows():
            if pd.isna(row.get("index_right")):
                continue
            
            site_idx = int(row["site_idx"])
            s = county_sites[site_idx]
            
            if site_idx in seen_sites:
                continue
            seen_sites.add(site_idx)
            
            matches_found += 1
            
            updates = {}
            new_owner = row.get("ownname")
            new_parusedesc = row.get("parusedesc")
            new_parusecode = row.get("parusecode")
            new_parval = row.get("parval")
            new_saledate = row.get("saledate")
            
            if new_owner and str(new_owner).strip() and (not s["owner_name"] or s["owner_name"] == "—"):
                updates["owner_name"] = str(new_owner).strip()
            if new_parusedesc and str(new_parusedesc).strip() and (not s["land_use"] or s["land_use"] == "—"):
                updates["land_use"] = str(new_parusedesc).strip()
            if new_parval and new_parval > 0:
                updates["total_value"] = str(float(new_parval))
            
            if updates:
                updates_applied += 1
                if not args.dry_run:
                    update_site(DB_PATH, s["id"], updates)
            
            if matches_found <= 10:
                pname = row.get("ownname", "")
                pdesc = row.get("parusedesc", "")
                pcode = row.get("parusecode", "")
                pval = row.get("parval", "")
                print(f"    Match #{matches_found}: {s['address'] or 'no addr'}")
                if updates:
                    for k, v in updates.items():
                        print(f"      → {k}: '{v}'")
                else:
                    print(f"      owner='{pname}' use='{pdesc}' code='{pcode}' value=${pval}")
        
        if args.limit:
            break
    
    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  Sites processed: {len(sites)}")
    print(f"  Spatial matches: {matches_found}")
    print(f"  Updates applied: {updates_applied}")
    print(f"  Mode: {'DRY RUN (no writes)' if args.dry_run else 'LIVE'}")

    if args.dry_run and updates_applied > 0:
        print(f"\n  Re-run without --dry-run to write updates to DB.")


if __name__ == "__main__":
    main()
