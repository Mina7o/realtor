"""Fetch Realtor.com listings using crawl4ai (Playwright + stealth).
Extracts data from Realtor's search API via rendered __NEXT_DATA__ JSON.

Usage:
  python3 fetch_realtor_crawl4ai.py --city Charlotte --state NC --max-pages 10
  python3 fetch_realtor_crawl4ai.py --city Charlotte --state NC --db --max-pages 20
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

try:
    from db import get_conn, upsert_property, upsert_listing, upsert_county, set_property_details
except ImportError:
    pass

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")


def extract_listings_from_realtor_data(page_data):
    # Realtor's structure changes often. We do a recursive search for 'home_search'.
    def find_key(obj, target_key):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == target_key:
                    return v
                res = find_key(v, target_key)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_key(item, target_key)
                if res is not None:
                    return res
        return None

    home_search = find_key(page_data, "home_search") or find_key(page_data, "results")
    if isinstance(home_search, dict):
        list_results = home_search.get("results", [])
        total = home_search.get("total", 0)
    elif isinstance(home_search, list):
        list_results = home_search
        total = len(list_results)
    else:
        list_results = []
        total = 0

    listings = []
    for r in list_results:
        location = r.get("location", {})
        addr = location.get("address", {})
        price = r.get("list_price")
        
        # Safely extract bed/bath/sqft
        description = r.get("description", {})
        beds = description.get("beds")
        baths = description.get("baths")
        sqft = description.get("sqft")
        
        # URL construction
        permalink = r.get("permalink")
        url = f"https://www.realtor.com/realestateandhomes-detail/{permalink}" if permalink else None

        listings.append({
            "id": r.get("property_id"),
            "address": addr.get("line"),
            "city": addr.get("city"),
            "state": addr.get("state_code"),
            "zip": addr.get("postal_code"),
            "price": price,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "lat": location.get("address", {}).get("coordinate", {}).get("lat"),
            "lng": location.get("address", {}).get("coordinate", {}).get("lon"),
            "url": url,
            "status_type": r.get("status"),
            "broker": r.get("advertisers", [{}])[0].get("name") if r.get("advertisers") else None,
            "img": r.get("primary_photo", {}).get("href") if r.get("primary_photo") else None,
        })
    return listings, total


def fmt_city(city, state_abbr):
    c = city.strip().replace(" ", "-")
    s = state_abbr.strip()
    return c, s


async def fetch_single_page(url, config, retries=3):
    for attempt in range(retries):
        async with AsyncWebCrawler(config=config) as crawler:
            run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
            result = await crawler.arun(url, config=run_config)
            if result.success:
                return result
            await asyncio.sleep(2.0 ** attempt)
    return result


async def scrape_realtor(city, state_abbr, max_pages=10):
    city_slug, state_slug = fmt_city(city, state_abbr)
    base_url = f"https://www.realtor.com/realestateandhomes-search/{city_slug}_{state_slug}"

    config = BrowserConfig(headless=True, verbose=False, viewport_width=2560, viewport_height=1440)

    all_listings = []
    total_expected = 0

    for page_num in range(1, max_pages + 1):
        url = base_url if page_num == 1 else f"{base_url}/pg-{page_num}"
        
        print(f"  Fetching page {page_num}... ({url})")
        result = await fetch_single_page(url, config)

        if not result.success:
            print(f"    FAILED (status {result.status_code})")
            break

        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', result.html, re.DOTALL)
        if not match:
            print(f"    No __NEXT_DATA__ on page {page_num} (IP Block / Captcha)")
            break

        data = json.loads(match.group(1))

        listings, total = extract_listings_from_realtor_data(data)
        total_expected = total or total_expected
        
        # Filter out bad entries
        listings = [l for l in listings if l.get("address")]
        all_listings.extend(listings)
        
        print(f"    Got {len(listings)} listings (total: {len(all_listings)} of ~{total_expected})")

        if len(listings) < 10:
            print("    Last page reached")
            break

        await asyncio.sleep(2.0)

    return all_listings, total_expected


def save_json(listings, filepath):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(listings, f, indent=2, default=str)
    print(f"Saved {len(listings)} listings to {filepath}")


def save_csv(listings, filepath):
    import csv
    if not listings:
        return
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=listings[0].keys())
        w.writeheader()
        w.writerows(listings)
    print(f"Saved {len(listings)} listings to {filepath}")


def load_to_db(listings, state="NC"):
    conn = get_conn()
    loaded = 0
    skipped = 0
    for l in listings:
        addr = l["address"]
        city = l["city"]
        zip_code = l["zip"]
        lat = l["lat"]
        lng = l["lng"]
        price = l["price"]
        if not addr or not price:
            skipped += 1
            continue

        county_id = upsert_county(city, state)
        pid = upsert_property(addr, city, state, zip_code, county_id, lat, lng)
        upsert_listing(
            property_id=pid,
            list_price=price,
            listing_status=l.get("status_type"),
            source="realtor.com",
            url=l.get("url"),
            zestimate=None, # Realtor doesn't have Zestimates
            broker_name=l.get("broker"),
            img_url=l.get("img"),
            status_text=l.get("status_type"),
        )
        set_property_details(pid,
            bedrooms=l.get("beds"),
            bathrooms=l.get("baths"),
            sqft=l.get("sqft"),
        )
        loaded += 1
    conn.close()
    return loaded, skipped


def main():
    parser = argparse.ArgumentParser(description="Scrape Realtor.com listings using crawl4ai")
    parser.add_argument("--city", default="Charlotte", help="City name")
    parser.add_argument("--state", default="NC", help="State abbreviation")
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Max pages to scrape (default: 10)")
    parser.add_argument("--json", help="Output JSON filename")
    parser.add_argument("--csv", help="Output CSV filename")
    parser.add_argument("--db", action="store_true", help="Load into SQLite")
    args = parser.parse_args()

    print(f"\nScraping Realtor.com listings for {args.city}, {args.state}  (max {args.max_pages} pages)")
    listings, total = asyncio.run(scrape_realtor(args.city, args.state, max_pages=args.max_pages))

    if not listings:
        print("No listings found.")
        sys.exit(1)

    prices = [l["price"] for l in listings if l["price"]]
    print(f"\nSummary:")
    print(f"  Total fetched: {len(listings)} (of ~{total} on Realtor)")
    if prices:
        print(f"  Price range: ${min(prices):,} - ${max(prices):,} (avg: ${sum(prices)//len(prices):,})")

    if args.json:
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))
    if args.csv:
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))
    if args.db:
        loaded, skipped = load_to_db(listings, args.state)
        print(f"  DB: {loaded} loaded, {skipped} skipped")

    print(f"\nDone. {len(listings)} Realtor.com listings fetched.")


if __name__ == "__main__":
    main()
