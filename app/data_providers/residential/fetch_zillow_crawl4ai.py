"""Fetch Zillow listings using crawl4ai (Playwright + stealth)."""
from logger_setup import setup_logging
from loguru import logger
from otel_utils import init_otel

setup_logging("fetch_zillow")

import argparse
import asyncio
import json
import os
import random
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]


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


def random_delay(min_s=1.5, max_s=4.5):
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)
    return delay

async def warmup_session(crawler):
    """Visit a benign page to set cookies before hitting the target."""
    warmup_urls = [
        "https://www.zillow.com/",
        "https://www.zillow.com/homes/",
    ]
    url = random.choice(warmup_urls)
    cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=15000)
    await crawler.arun(url, config=cfg)
    await asyncio.sleep(random.uniform(1.0, 2.5))


async def fetch_single_page(url, config, retries=5):
    last_result = None
    for attempt in range(retries):
        async with AsyncWebCrawler(config=config) as crawler:
            if attempt == 0:
                await warmup_session(crawler)
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                magic=True,
                simulate_user=True,
                override_navigator=True,
                page_timeout=30000,
            )
            result = await crawler.arun(url, config=run_config)
            if result.success:
                return result
            last_result = result
            wait = (2.0 ** attempt) + random.uniform(0.5, 2.0)
            logger.warning(f"Retry {attempt+1}/{retries} after {wait:.1f}s (status {result.status_code})")
            await asyncio.sleep(wait)
    return last_result


SORT_MAP = {
    "newest": "days",
    "lowprice": "prica",
    "highprice": "priced",
    "default": None,
}


def pick_browser_config():
    ua = random.choice(USER_AGENTS)
    w = random.choice([1920, 2560, 1440, 1366])
    h = random.choice([1080, 1440, 900, 768])

    if "Firefox" in ua:
        sec_ua = '"Firefox";v="130", "Not=A?Brand";v="8"'
        platform = random.choice(['"Windows"', '"macOS"'])
    elif "Safari" in ua and "Chrome" not in ua:
        sec_ua = '"Safari";v="17.6", "Not=A?Brand";v="8"'
        platform = '"macOS"'
    else:
        sec_ua = '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"'
        platform = random.choice(['"Windows"', '"macOS"'])

    return BrowserConfig(
        headless=True,
        verbose=False,
        viewport_width=w,
        viewport_height=h,
        user_agent=ua,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": sec_ua,
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": platform,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )


async def scrape_zillow(city, state_abbr, max_pages=10, sort="default"):
    city_slug, state_slug = fmt_city(city, state_abbr)
    base_url = f"https://www.zillow.com/homes/{city_slug}-{state_slug}/"

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
        logger.info(f"Fetching page {page_num}..." + (f" ({url.split('?')[1][:80]}...)" if has_qs else ""))

        config = pick_browser_config()
        result = await fetch_single_page(url, config)

        if not result.success:
            logger.error(f"FAILED (status {result.status_code})")
            break

        match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', result.html, re.DOTALL)
        if not match:
            logger.error(f"No __NEXT_DATA__ on page {page_num}")
            break

        data = json.loads(match.group(1))
        ss = data.get("props", {}).get("pageProps", {}).get("searchPageState", {})
        if not ss:
            logger.error(f"No searchPageState on page {page_num}")
            break

        if page_num == 1:
            query_state = ss.get("queryState")
            if not query_state:
                logger.error("No queryState found")
                break

        listings, total = extract_listings_from_page_data(ss)
        total_expected = total or total_expected
        all_listings.extend(listings)
        logger.info(f"Got {len(listings)} listings (total: {len(all_listings)} of ~{total_expected})")

        if len(listings) < 20:
            logger.info("Last page reached")
            break

        delay = random.uniform(2.0, 5.0)
        logger.info(f"Waiting {delay:.1f}s before next page...")
        await asyncio.sleep(delay)

    return all_listings, total_expected


def save_json(listings, filepath):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(listings, f, indent=2, default=str)
    logger.info(f"Saved {len(listings)} listings to {filepath}")


def save_csv(listings, filepath):
    import csv
    if not listings:
        return
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=listings[0].keys())
        w.writeheader()
        w.writerows(listings)
    logger.info(f"Saved {len(listings)} listings to {filepath}")


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
    tracer = init_otel("fetch_zillow")
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
    logger.info(f"Scraping Zillow {args.city}, {args.state} (max {args.max_pages} pages, sort: {sort_label.get(args.sort, args.sort)})")
    with tracer.start_as_current_span("scrape") as span:
        span.set_attribute("city", args.city)
        span.set_attribute("state", args.state)
        listings, total = asyncio.run(scrape_zillow(args.city, args.state, max_pages=args.max_pages, sort=args.sort))

    if not listings:
        logger.error("No listings found.")
        sys.exit(1)

    prices = [l["price"] for l in listings if l["price"]]
    logger.info(f"Total fetched: {len(listings)} (of ~{total} on Zillow)")
    if prices:
        logger.info(f"Price range: ${min(prices):,} - ${max(prices):,} (avg: ${sum(prices)//len(prices):,})")
    with_zest = sum(1 for l in listings if l.get("zestimate"))
    logger.info(f"With Zestimate: {with_zest}")

    if args.json:
        save_json(listings, os.path.join(OUTPUT_DIR, args.json))
    if args.csv:
        save_csv(listings, os.path.join(OUTPUT_DIR, args.csv))
    if args.db:
        loaded, skipped = load_to_db(listings, args.state)
        logger.info(f"DB: {loaded} loaded, {skipped} skipped")

    logger.info(f"Done. {len(listings)} Zillow listings fetched.")


if __name__ == "__main__":
    main()
