"""Scrape individual Realtor.com property detail pages for rich data.

Extracts description, HOA, agent info, lot size, etc.
from each listing's __NEXT_DATA__ JSON.

Usage:
  python3 fetch_realtor_details.py                          # all missing Realtor listings
  python3 fetch_realtor_details.py --limit 10               # first 10 only
  python3 fetch_realtor_details.py --listing-id 123         # single listing
"""
import argparse
import asyncio
import json
import re
import sys
import time

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from db import get_conn


def extract_from_realtor_cache(cache_json):
    """Extract rich property details from Realtor __NEXT_DATA__ JSON."""
    try:
        data = json.loads(cache_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    def find_key(obj, target_key):
        """Recursively search for a key in a JSON dictionary."""
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

    # First, look for the main property node which usually contains everything
    # in Realtor's next.js structure under 'initialReduxState' or 'props'
    prop_data = find_key(data, "property") or data
    
    # Extract data using robust recursive searching where possible, 
    # or direct extraction if we know Realtor.com's common keys.
    description = find_key(prop_data, "description")
    if isinstance(description, dict):
        description = description.get("text", "")

    # HOA Fee
    hoa = None
    hoa_info = find_key(prop_data, "hoa")
    if isinstance(hoa_info, dict):
        hoa = hoa_info.get("fee") or hoa_info.get("amount")
    elif isinstance(hoa_info, (int, float)):
        hoa = hoa_info

    # Prices and measurements
    price = find_key(prop_data, "list_price") or find_key(prop_data, "price")
    living_area = find_key(prop_data, "sqft") or find_key(prop_data, "building_size")
    if isinstance(living_area, dict):
        living_area = living_area.get("value")

    price_per_sqft = find_key(prop_data, "price_per_sqft")
    if not price_per_sqft and price and living_area:
        try:
            price_per_sqft = round(float(price) / float(living_area), 2)
        except (ValueError, TypeError):
            pass

    # Lot size
    lot_size_sqft = find_key(prop_data, "lot_size")
    if isinstance(lot_size_sqft, dict):
        # Handle conversion if it's in acres
        units = lot_size_sqft.get("units")
        lot_val = lot_size_sqft.get("value")
        if lot_val:
            if units == "acres":
                lot_size_sqft = lot_val * 43560
            else:
                lot_size_sqft = lot_val

    # Agents
    agent_name = find_key(prop_data, "agent_name") or find_key(prop_data, "agentName")
    agent_email = find_key(prop_data, "agent_email") or find_key(prop_data, "agentEmail")
    agent_phone = find_key(prop_data, "agent_phone") or find_key(prop_data, "agentPhone")
    broker_name = find_key(prop_data, "broker_name") or find_key(prop_data, "brokerName") or find_key(prop_data, "brokerageName")
    broker_phone = find_key(prop_data, "broker_phone") or find_key(prop_data, "brokerPhone")

    # Photos
    photos = find_key(prop_data, "photos") or []
    photo_urls = None
    if isinstance(photos, list) and photos:
        urls = [p.get("href") or p.get("url") for p in photos if isinstance(p, dict)]
        photo_urls = json.dumps([u for u in urls if u])

    # Sales history
    last_sold_price = None
    last_sold_date = None
    history = find_key(prop_data, "property_history") or find_key(prop_data, "history") or []
    if isinstance(history, list):
        for event in history:
            evt_type = event.get("event_name") or event.get("event")
            if evt_type and "sold" in str(evt_type).lower():
                last_sold_price = event.get("price")
                last_sold_date = event.get("date")
                break

    return {
        "description": description if isinstance(description, str) else str(description) if description else None,
        "hoa_fee": hoa,
        "price_per_sqft": price_per_sqft,
        "days_on_zillow": find_key(prop_data, "days_on_market"), # Map days on market to the same column
        "views": find_key(prop_data, "page_views"),
        "saves": find_key(prop_data, "favorites"),
        "agent_name": agent_name,
        "agent_email": agent_email,
        "agent_phone": agent_phone,
        "broker_name": broker_name,
        "broker_phone": broker_phone,
        "lot_size_sqft": lot_size_sqft if isinstance(lot_size_sqft, (int, float)) else None,
        "year_built": find_key(prop_data, "year_built"),
        "home_type": find_key(prop_data, "prop_type") or find_key(prop_data, "property_type"),
        "photo_urls": photo_urls,
        "last_sold_price": last_sold_price,
        "last_sold_date": last_sold_date,
        "raw_json": cache_json,
    }


async def scrape_detail(url, config, retries=3):
    for attempt in range(retries):
        async with AsyncWebCrawler(config=config) as crawler:
            run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
            result = await crawler.arun(url, config=run_config)
            if result.success:
                return result
            await asyncio.sleep(2.0 ** attempt)
    return result


def get_listings_to_fetch(limit=None, listing_id=None):
    conn = get_conn()
    if listing_id:
        rows = conn.execute("""
            SELECT l.id, l.url, p.address
            FROM listings l
            JOIN properties p ON l.property_id = p.id
            WHERE l.id = ? AND l.source IN ('realtor', 'realtor.com') AND l.url IS NOT NULL
        """, (listing_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT l.id, l.url, p.address
            FROM listings l
            JOIN properties p ON l.property_id = p.id
            LEFT JOIN listing_details ld ON l.id = ld.listing_id
            WHERE l.source IN ('realtor', 'realtor.com') AND l.url IS NOT NULL
              AND ld.id IS NULL
            ORDER BY l.id
        """).fetchall()
    conn.close()
    if limit:
        rows = rows[:limit]
    return rows


def save_details(listing_id, details):
    if details is None:
        return
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO listing_details
            (listing_id, description, hoa_fee, price_per_sqft,
             days_on_zillow, views, saves,
             agent_name, agent_email, agent_phone,
             broker_name, broker_phone, lot_size_sqft, year_built,
             home_type, photo_urls, last_sold_price, last_sold_date,
             raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        listing_id,
        details.get("description"),
        details.get("hoa_fee"),
        details.get("price_per_sqft"),
        details.get("days_on_zillow"),
        details.get("views"),
        details.get("saves"),
        details.get("agent_name"),
        details.get("agent_email"),
        details.get("agent_phone"),
        details.get("broker_name"),
        details.get("broker_phone"),
        details.get("lot_size_sqft"),
        details.get("year_built"),
        details.get("home_type"),
        details.get("photo_urls"),
        details.get("last_sold_price"),
        details.get("last_sold_date"),
        details.get("raw_json"),
    ))
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Scrape Realtor.com detail pages")
    parser.add_argument("--limit", type=int, help="Max listings to process")
    parser.add_argument("--listing-id", type=int, help="Single listing ID")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests")
    args = parser.parse_args()

    listings = get_listings_to_fetch(limit=args.limit, listing_id=args.listing_id)
    if not listings:
        print("No Realtor.com listings to fetch.")
        sys.exit(0)

    config = BrowserConfig(headless=True, verbose=False,
                           viewport_width=2560, viewport_height=1440)

    print(f"\\nFetching details for {len(listings)} Realtor.com listings...")
    done = 0
    errors = 0

    for row in listings:
        lid, url, address = row["id"], row["url"], row["address"]
        if not url:
            errors += 1
            continue

        print(f"  [{lid}] {address[:45]:45s} ...", end=" ", flush=True)
        try:
            result = asyncio.run(scrape_detail(url, config))
            if not result or not result.success:
                print("FAILED (HTTP)")
                errors += 1
                continue

            match = re.search(
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                result.html, re.DOTALL
            )
            if not match:
                print("FAILED (no __NEXT_DATA__)")
                errors += 1
                continue

            cache_json = match.group(1)
            details = extract_from_realtor_cache(cache_json)
            if not details:
                print("FAILED (parse error)")
                errors += 1
                continue

            save_details(lid, details)
            desc = (details.get("description") or "")[:60]
            print(f"OK  {desc}")
            done += 1

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

        if done + errors < len(listings):
            time.sleep(args.delay)

    print(f"\\nDone. {done} loaded, {errors} errors, "
          f"{len(listings) - done - errors} skipped")


if __name__ == "__main__":
    main()
