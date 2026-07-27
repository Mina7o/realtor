"""Enrich commercial_sites with zoning, flood, power, econ dev data
from county ArcGIS servers. Uses parcel lat/lng for spatial queries.

Run: python -m data_center.enrich_sites
Resumes on interrupt (skips already-enriched sites)."""

import sqlite3
import time
import json
import urllib.request
import urllib.parse
import os
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))

# ArcGIS layer configs per county
LAYERS = {
    "mecklenburg": {
        "zoning": [
            "https://meckgis.mecklenburgcountync.gov/server/rest/services/CityofCharlotteZoning/FeatureServer/0/query",
            "https://meckgis.mecklenburgcountync.gov/server/rest/services/UnincorporatedCountyandTownsZoning/FeatureServer/0/query",
        ],
        "flood": "https://meckgis.mecklenburgcountync.gov/server/rest/services/FEMAFloodplain/FeatureServer/0/query",
        "econ_dev": "https://meckgis.mecklenburgcountync.gov/server/rest/services/BusinessInvestmentOpportunityZones/FeatureServer/0/query",
        "zoning_field": lambda d: d.get("zonedes") or d.get("zone_des") or "",
        "flood_field": lambda d: f"{d.get('fld_zone','')} {d.get('status','')}".strip(),
        "econ_field": lambda d: d.get("name", ""),
    },
    "union": {
        "zoning": [
            "https://atlas.unioncountync.gov/server/rest/services/Zoning_Map_MIL1/MapServer/6/query",
        ],
        "flood": "https://atlas.unioncountync.gov/server/rest/services/2014_FEMA_Floodplain_ADA_MIL1/MapServer/17/query",
        "econ_dev": "https://atlas.unioncountync.gov/server/rest/services/Union_County_2050_Land_Use_Map_MIL2/MapServer/19/query",
        "zoning_field": lambda d: d.get("ZONE", ""),
        "flood_field": lambda d: d.get("ZONE_LID", ""),
        "econ_field": lambda d: d.get("Label", ""),
    },
    "york": {
        "zoning": [
            "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/ArcGIS/rest/services/York_County_Zoning__regions_/FeatureServer/0/query",
            "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/ArcGIS/rest/services/Rock_Hill_Zoning/FeatureServer/0/query",
        ],
        "flood": "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/ArcGIS/rest/services/Flood_Hazard_Zone_Areas__DFIRM_2017_/FeatureServer/0/query",
        "power": "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/ArcGIS/rest/services/Electric_Service_Areas___Boundary_/FeatureServer/0/query",
        "econ_dev": "https://services1.arcgis.com/2AGLxyiJoNiVHKwq/ArcGIS/rest/services/Land_Use_Plan__2035_/FeatureServer/0/query",
        "zoning_field": lambda d: d.get("zone") or d.get("ZONE") or "",
        "flood_field": lambda d: d.get("FLD_ZONE", ""),
        "power_field": lambda d: d.get("ElectricServiceProvider", ""),
        "econ_field": lambda d: d.get("FLU", ""),
    },
    "Rowan": {
        "zoning": [
            "https://gis.rowancountync.gov/arcgis/rest/services/Public/New_Tax_Map/MapServer/17/query",
        ],
        "flood": "https://gis.rowancountync.gov/arcgis/rest/services/Public/topo_flood/MapServer/8/query",
        "zoning_field": lambda d: d.get("ZONING", ""),
        "flood_field": lambda d: d.get("ZONE_LID_VALUE", ""),
    },
    "Orange": {
        "zoning": [
            "https://gis.orangecountync.gov/arcgis/rest/services/WebIdentifyServiceZoning/MapServer/0/query",
        ],
        "zoning_field": lambda d: d.get("Zonings", ""),
        "flood_field": lambda d: f"100yr:{d.get('Floodzone100','')} 500yr:{d.get('Floodzone500','')}",
        "econ_field": lambda d: d.get("MTCZ", ""),
    },
}

def spatial_query(url, lat, lng, retries=2):
    params = {
        "geometry": json.dumps({"x": lng, "y": lat}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    for attempt in range(retries):
        try:
            full = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(full, headers={"User-Agent": "RealtorPipeline/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                features = data.get("features", [])
                if features:
                    return features[0].get("attributes", {})
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
    return None

DEFAULT_POWER = {
    "mecklenburg": "Duke Energy Carolinas",
    "union": "Union Electric Membership Corp",
    "york": "York Electric Cooperative",
    "Rowan": "Duke Energy Carolinas",
    "Iredell": "Duke Energy Carolinas",
    "Durham": "Duke Energy Carolinas",
    "Orange": "Duke Energy Carolinas",
    "Guilford": "Duke Energy Carolinas",
    "Forsyth": "Duke Energy Carolinas",
    "Johnston": "Duke Energy Carolinas",
    "Chatham": "Duke Energy Carolinas",
}

def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, county, lat, lng FROM commercial_sites "
        "WHERE lat IS NOT NULL AND lng IS NOT NULL "
        "AND zoning IS NULL"
    ).fetchall()
    total = len(rows)
    print(f"Enriching {total} sites with GIS data...")

    done = 0
    for i, r in enumerate(rows):
        cfg = LAYERS.get(r["county"])
        if not cfg:
            default_power = DEFAULT_POWER.get(r["county"], "")
            conn.execute(
                "UPDATE commercial_sites SET zoning=?, power_provider=? WHERE id=?",
                ("UNKNOWN", default_power, r["id"])
            )
            done += 1
            continue

        lat, lng = r["lat"], r["lng"]

        # Zoning
        zoning_val = ""
        for zurl in cfg.get("zoning", []):
            res = spatial_query(zurl, lat, lng)
            if res:
                val = cfg["zoning_field"](res)
                if val:
                    zoning_val = val
                    break

        # Flood
        flood_val = ""
        if "flood" in cfg:
            res = spatial_query(cfg["flood"], lat, lng)
            if res:
                flood_val = cfg["flood_field"](res)

        # Power provider
        power_val = ""
        if "power" in cfg:
            res = spatial_query(cfg["power"], lat, lng)
            if res:
                power_val = cfg["power_field"](res)
        if not power_val:
            power_val = DEFAULT_POWER.get(r["county"], "")

        # Econ dev zone
        econ_val = ""
        if "econ_dev" in cfg:
            res = spatial_query(cfg["econ_dev"], lat, lng)
            if res:
                econ_val = cfg["econ_field"](res)

        conn.execute(
            "UPDATE commercial_sites SET zoning=?, flood_zone=?, power_provider=?, econ_dev_zone=? WHERE id=?",
            ((zoning_val or "")[:100], (flood_val or "")[:100], (power_val or "")[:100], (econ_val or "")[:100], r["id"])
        )
        conn.commit()
        done += 1

        if (i + 1) % 100 == 0 or i == total - 1:
            print(f"  {i+1}/{total} — {done} enriched")

    conn.close()
    print(f"Done: {done} sites enriched")

if __name__ == "__main__":
    main()
