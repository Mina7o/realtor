"""Fetch SellByOwner listings via crawl4ai."""
from logger_setup import setup_logging
from loguru import logger
from otel_utils import init_otel

setup_logging("fetch_sellbyowner")

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

SEARCH_URL = "https://www.sellbyowner.com/search/{state}/{city}"


def find_listings_in_script(html):
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'__NEXT_DATA__[^>]*>(.*?)</script>',
        r'window\.__DATA__\s*=\s*({.*?});',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def extract_listings_from_rendered(html):
    listings = []
    card_pattern = re.compile(
        r'<div[^>]*class="[^"]*listing[^"]*"[^>]*>.*?</div>\s*(?=<div|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    cards = card_pattern.findall(html)
    if not cards:
        card_pattern = re.compile(
            r'<article[^>]*class="[^"]*property[^"]*"[^>]*>.*?</article>',
            re.DOTALL | re.IGNORECASE
        )
        cards = card_pattern.findall(html)

    for card in cards:
        listing = {}

        m = re.search(r'\$?([0-9,]+)\s*(?:,|$)?', card)
        if m:
            try:
                listing["price"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        addr_match = re.search(r'<div[^>]*class="[^"]*address[^"]*"[^>]*>(.*?)</div>', card, re.DOTALL)
        if addr_match:
            listing["address"] = re.sub(r'<[^>]+>', '', addr_match.group(1)).strip()

        m = re.search(r'(\d+)\s*(?:bed|bdrm|br)', card, re.IGNORECASE)
        if m:
            listing["beds"] = int(m.group(1))

        m = re.search(r'(\d+)\s*(?:bath|ba)', card, re.IGNORECASE)
        if m:
            listing["baths"] = float(m.group(1))

        m = re.search(r'(\d[\d,]*)\s*(?:sqft|sq\s*ft|sf)', card, re.IGNORECASE)
        if m:
            listing["sqft"] = int(m.group(1).replace(",", ""))

        m = re.search(r'(?:href)=["\'](/[^"\']+)["\']', card)
        if m:
            listing["url"] = "https://www.sellbyowner.com" + m.group(1)

        m = re.search(r'<img[^>]*src=["\']([^"\']+)["\']', card)
        if m:
            listing["img"] = m.group(1)

        if listing.get("address") or listing.get("price"):
            listings.append(listing)

    return listings


async def fetch_single_page(url, config, retries=3):
    for attempt in range(retries):
        async with AsyncWebCrawler(config=config) as crawler:
            run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
            result = await crawler.arun(url, config=run_config)
            if result.success:
                return result
            await asyncio.sleep(2.0 ** attempt)
    return result


async def scrape_sellbyowner(city, state_abbr, max_pages=5):
    url = SEARCH_URL.format(city=urllib.parse.quote(city), state=state_abbr)

    config = BrowserConfig(headless=True, verbose=False, viewport_width=2560, viewport_height=1440)

    all_listings = []

    for page_num in range(1, max_pages + 1):
        page_url = url if page_num == 1 else f"{url}?page={page_num}"

        print(f"  Fetching page {page_num}... ({page_url})")
        result = await fetch_single_page(page_url, config)

        if not result.success:
            print(f"    FAILED (status {result.status_code})")
            break

        script_data = find_listings_in_script(result.html)
        if script_data:
            listings = extract_listings_from_rendered(result.html)
        else:
            listings = extract_listings_from_rendered(result.html)

        if not listings:
            print(f"    No listings found on page {page_num}")
            break

        all_listings.extend(listings)
        print(f"    Got {len(listings)} listings (total: {len(all_listings)})")

        if len(listings) < 5:
            print("    Last page reached")
            break

        await asyncio.sleep(2.0)

    return all_listings


def save_json(listings, filepath):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(listings, f, indent=2, default=str)
    print(f"Saved {len(listings)} listings to {filepath}")


def save_csv(listings, filepath):
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
        addr = l.get("address")
        city = l.get("city")
        zip_code = l.get("zip")
        lat = l.get("lat")
        lng = l.get("lng")
        price = l.get("price")
        if not addr or not price:
            skipped += 1
            continue

        county_id = upsert_county(city or "Unknown", state)
        pid = upsert_property(addr, city or "Unknown", state, zip_code, county_id, lat, lng)
        upsert_listing(
            property_id=pid,
            list_price=price,
            listing_status=l.get("status_type"),
            source="sellbyowner",
            url=l.get("url"),
            broker_name=l.get("broker"),
            img_url=l.get("img"),
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
    tracer = init_otel("fetch_sellbyowner")
    parser = argparse.ArgumentParser(description="Scrape SellByOwner listings using crawl4ai")
    parser.add_argument("--city", default="Charlotte", help="City name")
    parser.add_argument("--state", default="NC", help="State abbreviation")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages to scrape")
    parser.add_argument("--json", help="Output JSON filename")
    parser.add_argument("--csv", help="Output CSV filename")
    parser.add_argument("--db", action="store_true", help="Load into SQLite")
    args = parser.parse_args()

    print(f"\nScraping SellByOwner listings for {args.city}, {args.state} (max {args.max_pages} pages)")
    with tracer.start_as_current_span("scrape") as span:
        span.set_attribute("city", args.city)
        span.set_attribute("state", args.state)
        listings = asyncio.run(scrape_sellbyowner(args.city, args.state, max_pages=args.max_pages))

    if not listings:
        print("No listings found.")
        sys.exit(1)

    prices = [l["price"] for l in listings if l.get("price")]
    print(f"\nSummary:")
    print(f"  Total fetched: {len(listings)}")
    if prices:
        print(f"  Price range: ${min(prices):,} - ${max(prices):,} (avg: ${sum(prices)//len(prices):,})")

    if args.json:
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))
    if args.csv:
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))
    if args.db:
        loaded, skipped = load_to_db(listings, args.state)
        print(f"  DB: {loaded} loaded, {skipped} skipped")

    print(f"\nDone. {len(listings)} SellByOwner listings fetched.")


if __name__ == "__main__":
    main()
