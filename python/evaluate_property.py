import requests
import json


def _get_conn():
    from db import get_conn
    return get_conn()


def get_flood_zone(lat, lng):
    """FEMA flood zone via NFHL API"""
    url = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY",
        "f": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        features = data.get("features", [])
        if features:
            attrs = features[0]["attributes"]
            zone = attrs.get("FLD_ZONE", "X")
            risk_map = {"A": 9, "AE": 9, "AH": 8, "AO": 8, "AR": 7, "V": 10, "VE": 10, "X": 2, "XShaded": 3, "D": 5}
            score = risk_map.get(zone, 3)
            return {"zone": zone, "subtype": attrs.get("ZONE_SUBTY"), "risk_score": score}
    except Exception:
        pass
    return {"zone": "Unknown", "risk_score": 0}


def get_solar_potential(lat, lng):
    """NREL PVWatts free tier — monthly kWh for 10kW system"""
    url = "https://developer.nrel.gov/api/pvwatts/v8.json"
    params = {
        "api_key": "DEMO_KEY",
        "lat": lat, "lon": lng,
        "system_capacity": 10,
        "array_type": 1,
        "tilt": 20,
        "losses": 14,
        "format": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if "outputs" in data:
            annual = data["outputs"]["ac_annual"]
            return {"annual_kwh": round(annual, 1), "monthly_kwh": data["outputs"]["ac_monthly"]}
    except Exception:
        pass
    return {"annual_kwh": 0}


def get_environmental_risk(lat, lng):
    """EPA Superfund proximity (NEAREST_HAZARDOUS_WASTE)"""
    url = "https://data.epa.gov/efservice/SEMS_ACTIVE_SUPERFUND_SITES/JSON"
    try:
        r = requests.get(url, timeout=10)
        sites = r.json()
        closest = None
        min_dist = float("inf")
        for site in sites:
            try:
                slat = float(site.get("LATITUDE", 0))
                slng = float(site.get("LONGITUDE", 0))
                if slat and slng:
                    dist = ((lat - slat)**2 + (lng - slng)**2) ** 0.5
                    if dist < min_dist:
                        min_dist = dist
                        closest = site
            except:
                continue
        if closest and min_dist < 0.5:
            return {"nearest_site": closest.get("SITE_NAME"), "distance_deg": round(min_dist, 4), "risk_score": min(10, int(10 - min_dist * 20))}
    except Exception:
        pass
    return {"nearest_site": None, "risk_score": 0}


def evaluate_property(property_id):
    conn = _get_conn()
    prop = conn.execute("SELECT * FROM properties WHERE id=?", (property_id,)).fetchone()
    if not prop or not prop["lat"] or not prop["lng"]:
        return {"error": "Property not found or missing coordinates"}

    lat, lng = prop["lat"], prop["lng"]

    flood = get_flood_zone(lat, lng)
    solar = get_solar_potential(lat, lng)
    env = get_environmental_risk(lat, lng)

    result = {
        "property_id": property_id,
        "flood_zone": flood["zone"],
        "flood_risk": flood["risk_score"],
        "solar_potential_kwh": solar["annual_kwh"],
        "environmental_risk": env["risk_score"],
        "nearest_superfund": env.get("nearest_site"),
    }

    conn.execute("""
        CREATE TABLE IF NOT EXISTS property_evaluations (
            property_id INTEGER PRIMARY KEY,
            flood_zone TEXT,
            flood_risk INTEGER,
            solar_potential_kwh REAL,
            environmental_risk INTEGER,
            nearest_superfund TEXT,
            evaluated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO property_evaluations
        (property_id, flood_zone, flood_risk, solar_potential_kwh, environmental_risk, nearest_superfund)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (property_id, flood["zone"], flood["risk"], solar["annual_kwh"], env["risk_score"], env.get("nearest_site")))
    conn.commit()
    conn.close()

    return result


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(json.dumps(evaluate_property(pid), indent=2))
