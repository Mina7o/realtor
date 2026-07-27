"""Fetch FSBO listings from ForSaleByOwner.com API."""
from logger_setup import setup_logging
from loguru import logger

setup_logging("fetch_fsbo")

import argparse
import csv
import json
import os
import sys
import time
import httpx

try:
    from db import get_conn, upsert_property, upsert_listing, upsert_county, set_property_details
except ImportError:
    pass

API_URL = "https://directory.forsalebyowner.com/search/listings"
HEADERS = {
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "content-type": "application/json",
    "accept": "application/json",
    "origin": "https://www.forsalebyowner.com",
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


def fetch_page(slug, page=1, limit=50, client=None):
    close = False
    if client is None:
        client = httpx.Client(headers=HEADERS, timeout=30)
        close = True
    payload = {"listing_search": {"slug": slug, "page": page, "limit": limit}}
    resp = client.post(API_URL, json=payload)
    if resp.status_code != 200:
        print(f"  API error: {resp.status_code} {resp.text[:200]}")
        return None
    data = resp.json()
    if not data.get("success", True) is False:
        if data.get("code", 0) != 0:
            print(f"  API error: {data.get('message', 'unknown')} {data.get('errors', '')}")
            return None
    if close:
        client.close()
    return data


def extract_listing(l):
    addr = l.get("address", {})
    center = l.get("center", {})
    coords = center.get("coordinates", [None, None]) if center else [None, None]

    return {
        "fsbo_id": l.get("id"),
        "address": addr.get("fullStreetAddress"),
        "city": addr.get("city"),
        "state": addr.get("stateOrProvince"),
        "zip": addr.get("postalCode"),
        "county": addr.get("county"),
        "lat": coords[1],
        "lng": coords[0],
        "list_price": l.get("listPrice"),
        "listing_status": l.get("listingStatus"),
        "property_type": l.get("propertyType"),
        "bedrooms": l.get("bedrooms"),
        "bathrooms": l.get("bathrooms"),
        "year_built": l.get("yearBuilt"),
        "sqft": l.get("baseSqft"),
        "lot_size": l.get("lotSize"),
        "lot_size_type": l.get("lotSizeType"),
        "owner_name": l.get("contact", {}).get("ownerName") if l.get("contact") else None,
        "listing_title": l.get("listingTitle") or l.get("fullName"),
        "listing_description": l.get("listingDescription"),
        "mls_number": l.get("mlsNumber"),
        "mls_name": l.get("mlsName"),
        "listing_url": l.get("listingURL"),
        "vendor_url": l.get("vendorUrl"),
        "is_under_contract": l.get("isUnderContract"),
        "is_price_reduced": l.get("isPriceReduced"),
        "is_new_construction": l.get("isNewConstruction"),
        "lease_to_own": l.get("leaseToOwn"),
        "source": l.get("source"),
        "listing_date": l.get("listingDate"),
        "activated_date": l.get("activatedDate"),
        "stories": l.get("stories"),
        "total_rooms": l.get("totalRooms"),
        "total_parking": l.get("totalParking"),
        "roof": l.get("roof"),
        "exterior": l.get("exterior"),
        "fireplace": l.get("fireplace"),
        "pool": l.get("pool"),
        "waterfront": l.get("waterfront"),
        "view_types": ", ".join(l.get("viewTypes", [])) if l.get("viewTypes") else None,
        "photo_url": l["photos"][0]["url"] if l.get("photos") else None,
    }


def fetch_all(slug, delay=0.5):
    client = httpx.Client(headers=HEADERS, timeout=30)
    all_listings = []
    page = 1
    total_pages = None

    while True:
        print(f"  Fetching page {page}" + (f"/{total_pages}" if total_pages else "") + "...")
        data = fetch_page(slug, page=page, client=client)
        if data is None:
            break

        paging = data["data"]["paging"]
        total_pages = paging["totalPageCount"]
        listings = data["data"]["listings"]

        if not listings:
            break

        for l in listings:
            all_listings.append(extract_listing(l))

        print(f"    Got {len(listings)} listings (total: {len(all_listings)})")

        if page >= total_pages:
            break

        page += 1
        time.sleep(delay)

    client.close()
    return all_listings


def save_csv(listings, filepath):
    if not listings:
        print("No listings to save")
        return
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=listings[0].keys())
        w.writeheader()
        w.writerows(listings)
    print(f"Saved {len(listings)} listings to {filepath}")


def save_json(listings, filepath):
    with open(filepath, "w") as f:
        json.dump(listings, f, indent=2, default=str)
    print(f"Saved {len(listings)} listings to {filepath}")


def load_to_db(listings, state="NC"):
    conn = get_conn()
    loaded = 0
    for l in listings:
        addr = l["address"]
        city = l["city"]
        zip_code = l["zip"]
        lat = l["lat"]
        lng = l["lng"]
        list_price = l["list_price"]
        if not addr or not list_price:
            continue

        county_id = upsert_county(l.get("county") or city, state)
        pid = upsert_property(addr, city, state, zip_code, county_id, lat, lng)
        upsert_listing(
            property_id=pid,
            list_price=list_price,
            listing_status=l["listing_status"],
            listing_date=l["listing_date"] or l["activated_date"],
            source="fsbo",
            url=l["listing_url"] or l["vendor_url"],
        )
        set_property_details(pid,
            bedrooms=l.get("bedrooms"),
            bathrooms=l.get("bathrooms"),
            sqft=l.get("sqft"),
            year_built=l.get("year_built"),
            lot_sqft=l.get("lot_size"),
            property_type=l.get("property_type"),
        )
        loaded += 1
    conn.close()
    return loaded


def main():
    parser = argparse.ArgumentParser(description="Scrape FSBO listings from ForSaleByOwner.com")
    parser.add_argument("--slug", default="charlotte-north-carolina",
                        help="Location slug (default: charlotte-north-carolina)")
    parser.add_argument("--csv", help="Output CSV filename (saved to output/)")
    parser.add_argument("--json", help="Output JSON filename (saved to output/)")
    parser.add_argument("--db", action="store_true",
                        help="Load listings into SQLite database")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Delay between page requests in seconds (default: 0.5)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Results per page (default: 50)")
    parser.add_argument("--no-summary", action="store_true",
                        help="Skip printing summary")
    args = parser.parse_args()

    print(f"\nFetching FSBO listings for: {args.slug}")
    listings = fetch_all(args.slug, delay=args.delay)

    if not listings:
        print("No listings found.")
        sys.exit(1)

    if not args.no_summary:
        prices = [l["list_price"] for l in listings if l["list_price"]]
        print(f"\nSummary:")
        print(f"  Total listings: {len(listings)}")
        print(f"  Price range: ${min(prices):,.0f} - ${max(prices):,.0f}" if prices else "  No prices")
        types = {}
        for l in listings:
            t = l["property_type"] or "Unknown"
            types[t] = types.get(t, 0) + 1
        if types:
            print(f"  Types: {dict(sorted(types.items(), key=lambda x: -x[1]))}")

    if args.csv:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))

    if args.json:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))

    if args.db:
        loaded = load_to_db(listings)
        print(f"  Loaded {loaded} listings into database")

    print(f"\nDone. {len(listings)} listings fetched.")


if __name__ == "__main__":
    main()
