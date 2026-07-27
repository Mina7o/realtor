"""Batch-geocode all commercial_sites via ArcGIS World Geocoding, store lat/lng in DB.
ArcGIS is free, no API key needed, ~10 req/sec sustained."""

import sqlite3
import time
import urllib.request
import urllib.parse
import json
import os
from pathlib import Path

DB = Path(os.path.expanduser("~/Documents/proj/realtor/deals.db"))

# County → default city/state mapping
DEFAULT_PLACE = {
    # Original
    "mecklenburg": ("Charlotte", "NC"),
    "union": ("Monroe", "NC"),
    "york": ("Rock Hill", "SC"),
    # New NC counties
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


def build_query(row):
    """Build a geocoding query from the row data."""
    county = row["county"] or ""

    addr = (row["address"] or "").strip()
    city = (row["owner_city"] or "").strip()
    default_city, default_state = DEFAULT_PLACE.get(county, ("", "NC"))

    place_city = city or default_city
    state = default_state

    # If county is still lowercase (legacy), look up case-insensitive
    if not place_city:
        for k, (c, s) in DEFAULT_PLACE.items():
            if k.lower() == county.lower():
                place_city, state = c, s
                break

    parts = [p for p in [addr, place_city, state] if p]
    return ", ".join(parts) if parts else state


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, pid, county, address, owner_city FROM commercial_sites "
        "WHERE lat IS NULL OR lng IS NULL"
    ).fetchall()
    total = len(rows)
    if total == 0:
        print("No sites need geocoding.")
        return

    print(f"Geocoding {total} sites via ArcGIS...")

    done, skipped = 0, 0
    for i, r in enumerate(rows):
        query = build_query(r)
        result = geocode(query)

        if not result and r["pid"]:
            county = r["county"] or ""
            query = f"{r['pid']}, {county} NC"
            result = geocode(query)

        if result:
            conn.execute(
                "UPDATE commercial_sites SET lat = ?, lng = ? WHERE id = ?",
                (result[0], result[1], r["id"])
            )
            conn.commit()
            done += 1
        else:
            skipped += 1

        if (i + 1) % 500 == 0 or i == total - 1:
            print(f"  {i+1}/{total} — {done} geocoded, {skipped} skipped")

    conn.close()
    print(f"Done: {done} geocoded, {skipped} skipped")


if __name__ == "__main__":
    main()
