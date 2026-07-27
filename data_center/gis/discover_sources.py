"""Probe county ArcGIS servers to discover public parcel endpoints.

Tests common URL patterns for each target county and updates
county_config with working URLs.
"""

import requests
import sys
import time
import json

# Common ArcGIS server URL patterns by state
URL_PATTERNS = {
    "NC": [
        "https://maps.{countylc}countync.gov/arcgis/rest/services",
        "https://maps.{countylc}countync.gov/server/rest/services",
        "https://gis.{countylc}countync.gov/arcgis/rest/services",
        "https://gis.{countylc}countync.gov/server/rest/services",
        "https://{countylc}countync.maps.arcgis.com",
    ],
    "SC": [
        "https://maps.{countylc}countysc.gov/arcgis/rest/services",
        "https://maps.{countylc}countysc.gov/server/rest/services",
        "https://gis.{countylc}countysc.gov/arcgis/rest/services",
        "https://{countylc}countysc.maps.arcgis.com",
    ],
    "GA": [
        "https://maps.{countylc}countyga.gov/arcgis/rest/services",
        "https://maps.{countylc}countyga.gov/server/rest/services",
        "https://gis.{countylc}countyga.gov/arcgis/rest/services",
        "https://{countylc}countyga.maps.arcgis.com",
        "https://gis.{countylc}ga.gov/arcgis/rest/services",
    ],
    "FL": [
        "https://maps.{countylc}countyfl.gov/arcgis/rest/services",
        "https://maps.{countylc}countyfl.gov/server/rest/services",
        "https://gis.{countylc}countyfl.gov/arcgis/rest/services",
        "https://{countylc}countyfl.maps.arcgis.com",
        "https://gis.{countylc}fl.gov/arcgis/rest/services",
    ],
    "VA": [
        "https://maps.{countylc}countyva.gov/arcgis/rest/services",
        "https://maps.{countylc}countyva.gov/server/rest/services",
        "https://gis.{countylc}countyva.gov/arcgis/rest/services",
        "https://{countylc}countyva.maps.arcgis.com",
    ],
}

# Special cases / known URLs
KNOWN_URLS = {
    "ORANGE": {"NC": "https://gis.orangecountync.gov/arcgis/rest/services/WebParcelService/MapServer/0"},
}


def probe_url(url, timeout=8):
    """Check if an ArcGIS server URL responds."""
    try:
        r = requests.get(f"{url}?f=json", timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            return data
    except:
        pass
    return None


def find_parcel_layer(server_url, timeout=10):
    """Search for parcel-related layers in a server."""
    data = probe_url(server_url, timeout)
    if not data:
        return None

    services = data.get("services", [])
    folders = data.get("folders", [])

    candidates = []

    for svc in services:
        name = svc.get("name", "")
        stype = svc.get("type", "")
        if "parcel" in name.lower() and stype == "MapServer":
            candidates.append(name)

    for folder in folders:
        furl = f"{server_url}/{folder}"
        fdata = probe_url(furl, timeout)
        if fdata:
            for svc in fdata.get("services", []):
                name = svc.get("name", "")
                stype = svc.get("type", "")
                if "parcel" in name.lower() and stype == "MapServer":
                    candidates.append(f"{folder}/{name}")
    return candidates


def discover_county(county, state):
    """Try to find a working ArcGIS server for a county."""
    countylc = county.lower().replace(" ", "").replace("-", "")
    state_key = state

    patterns = URL_PATTERNS.get(state_key, [])

    if state_key == "FL":
        patterns = [
            f"https://maps.{countylc}countyfl.gov/arcgis/rest/services",
            f"https://gis.{countylc}countyfl.gov/arcgis/rest/services",
            f"https://{countylc}countyfl.maps.arcgis.com",
            f"https://gis.{countylc}fl.gov/arcgis/rest/services",
        ]

    for pattern in patterns:
        url = pattern.format(countylc=countylc)
        data = probe_url(url)
        if data:
            layers = find_parcel_layer(url)
            return {"server_url": url, "data": data, "parcel_layers": layers}

    # Try direct street-level patterns
    direct = f"https://www.{countylc}countync.gov"
    if state_key == "NC":
        data = probe_url(f"{direct}/gis/arcgis/rest/services")
        if data:
            layers = find_parcel_layer(f"{direct}/gis/arcgis/rest/services")
            return {"server_url": f"{direct}/gis/arcgis/rest/services", "data": data, "parcel_layers": layers}

    return None


def main():
    from data_center.gis.county_config import TARGET_COUNTIES

    print("=== ArcGIS Server Discovery ===\n")

    results = {}
    for county, cfg in sorted(TARGET_COUNTIES.items()):
        if cfg["arcgis_url"]:
            print(f"  {county}, {cfg['state']}: already configured")
            results[county] = {"status": "configured", "url": cfg["arcgis_url"]}
            continue

        print(f"  Probing {county}, {cfg['state']}...", end=" ", flush=True)
        result = discover_county(county, cfg["state"])
        time.sleep(1)

        if result:
            layers = result.get("parcel_layers", [])
            print(f"FOUND server at {result['server_url']}")
            if layers:
                print(f"    Parcel layers: {layers}")
            else:
                print("    (no parcel layer found)")
            results[county] = {
                "status": "found",
                "server_url": result["server_url"],
                "parcel_layers": layers,
            }
        else:
            data_source = "NC OneMap" if cfg["nconemap_ok"] else "unknown"
            print(f"not found (fallback: {data_source})")
            results[county] = {"status": "not_found", "fallback": data_source}

    print("\n=== Summary ===")
    found = sum(1 for r in results.values() if r["status"] == "found")
    configured = sum(1 for r in results.values() if r["status"] == "configured")
    not_found = sum(1 for r in results.values() if r["status"] == "not_found")
    print(f"  Found servers: {found}")
    print(f"  Already configured: {configured}")
    print(f"  Not found: {not_found}")

    report_file = "discovery_results.json"
    with open(report_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {report_file}")


if __name__ == "__main__":
    main()
