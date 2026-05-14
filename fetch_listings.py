"""
Fetch for-sale listings from RentCast API and match against tax records.
Usage: python fetch_listings.py --api-key KEY --state NC --cities Charlotte,Raleigh
"""
import argparse
import json
import time
import sys
import requests
from db import get_conn, upsert_property, upsert_listing, upsert_county

RENTCAST_BASE = "https://api.rentcast.io/v1"

def fetch_sale_listings(api_key, city, state, limit=500):
    url = f"{RENTCAST_BASE}/listings/sale"
    params = {"city": city, "state": state, "status": "Active", "limit": min(limit, 500)}
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    all_listings = []
    page = 0
    while True:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        if not data or len(data) == 0:
            break
        all_listings.extend(data)
        print(f"  Fetched {len(data)} listings (total: {len(all_listings)})")
        if len(data) < params["limit"]:
            break
        # RentCast may not support pagination via offset;
        # for large cities we narrow by zip or area in subsequent calls
        break
    return all_listings

def match_and_store(listings, county_id, state):
    conn = get_conn()
    matched = 0
    unmatched = 0
    for l in listings:
        addr = l.get("addressLine1", "")
        city = l.get("city", "")
        zip_code = l.get("zipCode", "")
        lat = l.get("latitude")
        lng = l.get("longitude")
        list_price = l.get("price")
        status = l.get("listingStatus")
        mls_id = l.get("mlsId")
        listing_url = l.get("url")
        property_type = l.get("propertyType", "")

        if not addr or not list_price:
            continue

        # Skip land/commercial, keep residential
        if property_type.lower() in ("land", "commercial", "industrial"):
            continue

        pid = upsert_property(addr, city, state, zip_code, county_id, lat, lng)
        upsert_listing(
            property_id=pid,
            list_price=list_price,
            listing_status=status,
            source="rentcast",
            mls_id=str(mls_id) if mls_id else None,
            url=listing_url
        )
        matched += 1
    return matched

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True, help="RentCast API key")
    parser.add_argument("--state", default="NC", help="State code")
    parser.add_argument("--cities", help="Comma-separated city list")
    args = parser.parse_args()

    cities = [c.strip() for c in args.cities.split(",")] if args.cities else []

    for city in cities:
        print(f"\nFetching listings for {city}, {args.state}...")
        listings = fetch_sale_listings(args.api_key, city, args.state)
        print(f"Total listings: {len(listings)}")

        county_id = upsert_county(city, args.state)
        matched = match_and_store(listings, county_id, args.state)
        print(f"Stored {matched} listings in DB")

        time.sleep(0.5)

    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"\nTotal listings in DB: {count}")

if __name__ == "__main__":
    main()
