"""Fetch ForSaleByOwner.com via crawl4ai."""
from logger_setup import setup_logging
from loguru import logger
from otel_utils import init_otel

setup_logging("fetch_fsbo_crawl4ai")

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import urllib.parse

try:
    from db import get_conn, upsert_property, upsert_listing, upsert_county, set_property_details
except ImportError:
    pass

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

SEARCH_URL = "https://www.forsalebyowner.com/search/{slug}/"


def find_next_data(html):
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def extract_from_next_data(data):
    listings = []
    props = data.get("props", {}).get("pageProps", {})
    search_data = props.get("searchData") or props.get("listings") or props.get("results")
    if isinstance(search_data, dict):
        items = search_data.get("listings", []) or search_data.get("results", [])
    elif isinstance(search_data, list):
        items = search_data
    else:
        items = []

    for item in items:
        listing = extract_listing_fields(item)
        if listing:
            listings.append(listing)
    return listings


def extract_listing_fields(item):
    if isinstance(item, dict):
        addr = item.get("address", {}) or {}
        center = item.get("center", {}) or {}
        coords = center.get("coordinates", [None, None]) if isinstance(center, dict) else [None, None]
        contact = item.get("contact", {}) or {}
        photos = item.get("photos") or item.get("media", [])
        img_url = None
        if isinstance(photos, list) and photos:
            first = photos[0]
            img_url = first.get("url") if isinstance(first, dict) else first

        return {
            "fsbo_id": item.get("id"),
            "address": addr.get("fullStreetAddress") or addr.get("streetAddress") or item.get("fullStreetAddress"),
            "city": addr.get("city") or item.get("city"),
            "state": addr.get("stateOrProvince") or item.get("state"),
            "zip": addr.get("postalCode") or item.get("zip"),
            "county": addr.get("county") or item.get("county"),
            "lat": coords[1] if len(coords) > 1 else item.get("latitude"),
            "lng": coords[0] if len(coords) > 0 else item.get("longitude"),
            "price": item.get("listPrice") or item.get("price"),
            "listing_status": item.get("listingStatus") or item.get("status"),
            "property_type": item.get("propertyType") or item.get("type"),
            "bedrooms": item.get("bedrooms") or item.get("beds"),
            "bathrooms": item.get("bathrooms") or item.get("baths"),
            "sqft": item.get("baseSqft") or item.get("sqft") or item.get("area"),
            "year_built": item.get("yearBuilt") or item.get("yearBuilt"),
            "lot_size": item.get("lotSize") or item.get("lotSize"),
            "owner_name": contact.get("ownerName") or item.get("ownerName"),
            "listing_title": item.get("listingTitle") or item.get("title"),
            "listing_description": item.get("listingDescription") or item.get("description"),
            "mls_number": item.get("mlsNumber"),
            "mls_name": item.get("mlsName"),
            "listing_url": item.get("listingURL") or item.get("url"),
            "is_under_contract": item.get("isUnderContract"),
            "is_price_reduced": item.get("isPriceReduced"),
            "listing_date": item.get("listingDate") or item.get("listDate"),
            "img_url": img_url,
        }
    return None


def extract_listings_from_rendered(html):
    listings = []

    card_pattern = re.compile(
        r'<div[^>]*class="[^"]*(?:listing-card|property-card|result-item|listing-item)[^"]*"[^>]*>.*?</div>\s*(?=<div|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    cards = card_pattern.findall(html)
    if not cards:
        card_pattern = re.compile(
            r'<li[^>]*class="[^"]*listing[^"]*"[^>]*>.*?</li>',
            re.DOTALL | re.IGNORECASE
        )
        cards = card_pattern.findall(html)

    for card in cards:
        listing = {}

        m = re.search(r'\$?([0-9,]+)\s*(?:,|$)', card)
        if m:
            try:
                listing["price"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        addr_match = re.search(
            r'<div[^>]*class="[^"]*address[^"]*"[^>]*>(.*?)</div>',
            card, re.DOTALL
        )
        if addr_match:
            listing["address"] = re.sub(r'<[^>]+>', '', addr_match.group(1)).strip()

        m = re.search(r'(\d+)\s*(?:bed|bdrm|br)', card, re.IGNORECASE)
        if m:
            listing["bedrooms"] = int(m.group(1))

        m = re.search(r'(\d+)\s*(?:bath|ba)', card, re.IGNORECASE)
        if m:
            listing["bathrooms"] = float(m.group(1))

        m = re.search(r'(\d[\d,]*)\s*(?:sqft|sq\s*ft|sf)', card, re.IGNORECASE)
        if m:
            listing["sqft"] = int(m.group(1).replace(",", ""))

        m = re.search(r'(?:href)=["\'](/[^"\']+)["\']', card)
        if m:
            listing["listing_url"] = "https://www.forsalebyowner.com" + m.group(1)

        m = re.search(r'<img[^>]*src=["\']([^"\']+)["\']', card)
        if m:
            listing["img_url"] = m.group(1)

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


async def scrape_fsbo(slug, max_pages=5):
    base_url = SEARCH_URL.format(slug=slug)
    config = BrowserConfig(headless=True, verbose=False, viewport_width=2560, viewport_height=1440)

    all_listings = []

    for page_num in range(1, max_pages + 1):
        page_url = base_url if page_num == 1 else f"{base_url}?page={page_num}"

        print(f"  Fetching page {page_num}... ({page_url})")
        result = await fetch_single_page(page_url, config)

        if not result.success:
            print(f"    FAILED (status {result.status_code})")
            break

        next_data = find_next_data(result.html)
        if next_data:
            listings = extract_from_next_data(next_data)
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
    fieldnames = list(listings[0].keys())
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(listings)
    print(f"Saved {len(listings)} listings to {filepath}")


def load_to_db(listings, state="NC"):
    conn = get_conn()
    loaded = 0
    skipped = 0
    for l in listings:
        addr = l.get("address") or l.get("fullStreetAddress")
        city = l.get("city")
        zip_code = l.get("zip")
        lat = l.get("lat")
        lng = l.get("lng")
        price = l.get("price")
        if not addr or not price:
            skipped += 1
            continue
        county_id = upsert_county(l.get("county") or city or "Unknown", state)
        pid = upsert_property(addr, city or "Unknown", state, zip_code, county_id, lat, lng)
        upsert_listing(
            property_id=pid,
            list_price=price,
            listing_status=l.get("listing_status"),
            listing_date=l.get("listing_date"),
            source="fsbo",
            url=l.get("listing_url"),
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
    return loaded, skipped


def main():
    tracer = init_otel("fetch_fsbo_crawl4ai")
    parser = argparse.ArgumentParser(description="Scrape FSBO listings using crawl4ai")
    parser.add_argument("--slug", default="charlotte-north-carolina",
                        help="Location slug (default: charlotte-north-carolina)")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages to scrape")
    parser.add_argument("--csv", help="Output CSV filename")
    parser.add_argument("--json", help="Output JSON filename")
    parser.add_argument("--db", action="store_true", help="Load into SQLite")
    parser.add_argument("--no-summary", action="store_true", help="Skip summary")
    args = parser.parse_args()

    print(f"\nScraping FSBO listings for slug: {args.slug} (max {args.max_pages} pages)")
    with tracer.start_as_current_span("scrape") as span:
        span.set_attribute("slug", args.slug)
        listings = asyncio.run(scrape_fsbo(args.slug, max_pages=args.max_pages))

    if not listings:
        print("No listings found.")
        sys.exit(1)

    if not args.no_summary:
        prices = [l["price"] for l in listings if l.get("price")]
        print(f"\nSummary:")
        print(f"  Total listings: {len(listings)}")
        if prices:
            print(f"  Price range: ${min(prices):,} - ${max(prices):,}" if prices else "  No prices")
        types = {}
        for l in listings:
            t = l.get("property_type") or l.get("listing_title") or "Unknown"
            types[t] = types.get(t, 0) + 1
        if types:
            sorted_types = sorted(types.items(), key=lambda x: -x[1])
            print(f"  Types: {dict(sorted_types[:5])}")

    if args.csv:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))
    if args.json:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))
    if args.db:
        loaded, skipped = load_to_db(listings)
        print(f"  DB: {loaded} loaded, {skipped} skipped")

    print(f"\nDone. {len(listings)} FSBO listings fetched.")


if __name__ == "__main__":
    main()
