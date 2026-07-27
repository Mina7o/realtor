"""Batch-geocode landandfarm_listings via ArcGIS World Geocoding, store lat/lng."""

import sqlite3
import time
import urllib.request
import urllib.parse
import json
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "deals.db"

DEFAULT_PLACE = {
    "Mecklenburg": ("Charlotte", "NC"),
    "Cabarrus": ("Concord", "NC"),
    "Rowan": ("Salisbury", "NC"),
    "Iredell": ("Statesville", "NC"),
    "Durham": ("Durham", "NC"),
    "Orange": ("Hillsborough", "NC"),
    "Guilford": ("Greensboro", "NC"),
    "Forsyth": ("Winston-Salem", "NC"),
    "Johnston": ("Smithfield", "NC"),
    "Chatham": ("Pittsboro", "NC"),
}


def geocode(query, retries=3):
    for attempt in range(retries):
        try:
            url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
            params = {"f": "json", "singleLine": query, "maxLocations": 1}
            full_url = url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(full_url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                candidates = data.get("candidates")
                if candidates and len(candidates) > 0:
                    loc = candidates[0]["location"]
                    return (loc["y"], loc["x"])
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
    return None


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT pid, county, address, state FROM landandfarm_listings "
        "WHERE lat IS NULL OR lng IS NULL"
    ).fetchall()
    total = len(rows)
    if total == 0:
        print("All listings already geocoded.")
        return

    print(f"Geocoding {total} LandAndFarm listings...")

    done, skipped = 0, 0
    for i, r in enumerate(rows):
        addr = (r["address"] or "").strip()
        county = r["county"] or ""

        query = addr if addr else ""
        if not query:
            city, state = DEFAULT_PLACE.get(county, ("", "NC"))
            query = f"{county} County, {state}"

        result = geocode(query)

        if result:
            conn.execute(
                "UPDATE landandfarm_listings SET lat = ?, lng = ? WHERE pid = ?",
                (result[0], result[1], r["pid"])
            )
            conn.commit()
            done += 1
        else:
            skipped += 1

        if (i + 1) % 100 == 0 or i == total - 1:
            print(f"  {i+1}/{total} — {done} geocoded, {skipped} skipped")

    conn.close()
    print(f"Done: {done} geocoded, {skipped} skipped")


if __name__ == "__main__":
    main()
