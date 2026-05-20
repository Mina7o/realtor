"""Fetch Zillow listings using crawl4ai (Playwright + stealth).
Extracts data from Zillow's search API via rendered __NEXT_DATA__ JSON.
Pagination via searchQueryState URL parameter.

Usage:
  python3 fetch_zillow_crawl4ai.py --city Charlotte --state NC --max-pages 10
  python3 fetch_zillow_crawl4ai.py --city Charlotte --state NC --db --max-pages 20
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


def extract_listings_from_page_data(page_data):
    cat1 = page_data.get("cat1", {})
    search_results = cat1.get("searchResults", {})
    list_results = search_results.get("listResults", [])
    total = cat1.get("totalResultCount", 0) or search_results.get("totalResultCount", 0) or page_data.get("categoryTotals", {}).get("cat1", {}).get("totalResultCount", 0)

    listings = []
    for r in list_results:
        lat_lng = r.get("latLong", {}) or {}
        zest = r.get("zestimate")
        listings.append({
            "zpid": r.get("zpid"),
            "address": r.get("address"),
            "address_street": r.get("addressStreet"),
            "city": r.get("addressCity"),
            "state": r.get("addressState"),
            "zip": r.get("addressZipcode"),
            "price": r.get("unformattedPrice"),
            "beds": r.get("beds"),
            "baths": r.get("baths"),
            "sqft": r.get("area"),
            "lat": lat_lng.get("latitude"),
            "lng": lat_lng.get("longitude"),
            "url": r.get("detailUrl"),
            "zestimate": zest if isinstance(zest, (int, float)) else None,
            "status_type": r.get("statusType"),
            "status_text": r.get("statusText"),
            "broker": r.get("brokerName"),
            "img": r.get("imgSrc"),
        })
    return listings, total


def fmt_city(city, state_abbr):
    c = city.strip().lower().replace(" ", "-")
    s = state_abbr.strip().lower()
    return c, s


def build_paginated_url(base_url, query_state, page):
    modified = {**query_state, "pagination": {"currentPage": page}}
    qs = json.dumps(modified)
    import urllib.parse
    return f"{base_url}?searchQueryState={urllib.parse.quote(qs)}"


async def fetch_single_page(url, config, retries=3):
    for attempt in range(retries):
        async with AsyncWebCrawler(config=config) as crawler:
            run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
            result = await crawler.arun(url, config=run_config)
            if result.success:
                return result
            await asyncio.sleep(2.0 ** attempt)
    return result


SORT_MAP = {
    "newest": "days",
    "lowprice": "prica",
    "highprice": "priced",
    "default": None,
}


async def scrape_zillow(city, state_abbr, max_pages=10, sort="default"):
    city_slug, state_slug = fmt_city(city, state_abbr)
    base_url = f"https://www.zillow.com/homes/{city_slug}-{state_slug}/"

    config = BrowserConfig(headless=True, verbose=False, viewport_width=2560, viewport_height=1440)

    all_listings = []
    total_expected = 0
    query_state = None
    sort_val = SORT_MAP.get(sort)

    for page_num in range(1, max_pages + 1):
        if sort_val and (page_num == 1 or query_state):
            state = query_state if query_state else {}
            state["sortSelection"] = {"value": sort_val}
            url = build_paginated_url(base_url, state, page_num)
        elif query_state and page_num > 1:
            url = build_paginated_url(base_url, query_state, page_num)
        else:
            url = base_url

        has_qs = "?" in url
        print(f"  Fetching page {page_num}..." + (f" ({url.split('?')[1][:80]}...)" if has_qs else ""))
        result = await fetch_single_page(url, config)

        if not result.success:
            print(f"    FAILED (status {result.status_code})")
            break

        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', result.html, re.DOTALL)
        if not match:
            print(f"    No __NEXT_DATA__ on page {page_num}")
            break

        data = json.loads(match.group(1))
        ss = data.get("props", {}).get("pageProps", {}).get("searchPageState", {})
        if not ss:
            print(f"    No searchPageState on page {page_num}")
            break

        if page_num == 1:
            query_state = ss.get("queryState")
            if not query_state:
                print("    No queryState found")
                break

        listings, total = extract_listings_from_page_data(ss)
        total_expected = total or total_expected
        all_listings.extend(listings)
        print(f"    Got {len(listings)} listings (total: {len(all_listings)} of ~{total_expected})")

        if len(listings) < 20:
            print("    Last page reached")
            break

        await asyncio.sleep(1.0)

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
        addr = l["address_street"] or l["address"]
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
            source="zillow",
            url=l.get("url"),
            zestimate=l.get("zestimate"),
            broker_name=l.get("broker"),
            img_url=l.get("img"),
            status_text=l.get("status_text"),
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
    parser = argparse.ArgumentParser(description="Scrape Zillow listings using crawl4ai")
    parser.add_argument("--city", default="Charlotte", help="City name")
    parser.add_argument("--state", default="NC", help="State abbreviation")
    parser.add_argument("--max-pages", type=int, default=10,
                        help="Max pages to scrape (default: 10, ~410 listings)")
    parser.add_argument("--sort", default="default",
                        choices=["default", "newest", "lowprice", "highprice"],
                        help="Sort order (default: best match, newest: days on Zillow)")
    parser.add_argument("--json", help="Output JSON filename")
    parser.add_argument("--csv", help="Output CSV filename")
    parser.add_argument("--db", action="store_true", help="Load into SQLite")
    args = parser.parse_args()

    sort_label = {"default": "Best Match", "newest": "Newest", "lowprice": "Low Price", "highprice": "High Price"}
    print(f"\nScraping Zillow listings for {args.city}, {args.state}  (max {args.max_pages} pages, sort: {sort_label.get(args.sort, args.sort)})")
    listings, total = asyncio.run(scrape_zillow(args.city, args.state, max_pages=args.max_pages, sort=args.sort))

    if not listings:
        print("No listings found.")
        sys.exit(1)

    prices = [l["price"] for l in listings if l["price"]]
    print(f"\nSummary:")
    print(f"  Total fetched: {len(listings)} (of ~{total} on Zillow)")
    if prices:
        print(f"  Price range: ${min(prices):,} - ${max(prices):,} (avg: ${sum(prices)//len(prices):,})")
    with_zest = sum(1 for l in listings if l.get("zestimate"))
    print(f"  With Zestimate: {with_zest}")

    if args.json:
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))
    if args.csv:
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))
    if args.db:
        loaded, skipped = load_to_db(listings, args.state)
        print(f"  DB: {loaded} loaded, {skipped} skipped")

    print(f"\nDone. {len(listings)} Zillow listings fetched.")


if __name__ == "__main__":
    main()
