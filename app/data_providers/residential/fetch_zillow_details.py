"""Scrape individual Zillow property detail pages for rich data.

Extracts description, HOA, agent info, lot size, days on Zillow, etc.
from each listing's __NEXT_DATA__ JSON.

Usage:
  python3 fetch_zillow_details.py                          # all missing Zillow listings
  python3 fetch_zillow_details.py --limit 10              # first 10 only
  python3 fetch_zillow_details.py --listing-id 123        # single listing
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from db import get_conn


def extract_from_gdp_cache(cache_json):
    """Extract rich property details from gdpClientCache JSON."""
    try:
        cache = json.loads(cache_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    key = list(cache.keys())[0] if cache else None
    if not key:
        return None
    root = cache[key]
    prop = root.get("property", {}) or {}
    att = prop.get("attributionInfo", {}) or {}
    reso = prop.get("resoFacts", {}) or {}

    description = prop.get("description")
    price = prop.get("price")
    living_area = prop.get("livingArea")

    price_per_sqft = prop.get("pricePerSquareFoot")
    if not price_per_sqft and price and living_area:
        price_per_sqft = round(price / living_area, 2)

    hoa = None
    hoa_raw = prop.get("hoa")
    if isinstance(hoa_raw, dict):
        hoa = hoa_raw.get("amount")
    elif isinstance(hoa_raw, (int, float)):
        hoa = hoa_raw
    if not hoa and isinstance(reso, dict):
        hoa_fees = reso.get("hoaFees")
        if isinstance(hoa_fees, list) and hoa_fees:
            hoa = hoa_fees[0].get("fee")

    photos = prop.get("photos") or []
    photo_urls = json.dumps([p.get("url") for p in photos if p.get("url")]) if photos else None

    agent_name = att.get("agentName")
    agent_email = att.get("agentEmail")
    agent_phone = att.get("agentPhone")
    broker_name = att.get("brokerName") or att.get("brokerageName")
    broker_phone = att.get("brokerPhone")

    last_sold = None
    last_sold_date = None
    price_history = prop.get("priceHistory") or []
    for entry in price_history:
        if entry.get("event") == "Sold":
            last_sold = entry.get("price")
            last_sold_date = entry.get("date")
            break

    return {
        "description": description,
        "hoa_fee": hoa,
        "price_per_sqft": price_per_sqft,
        "days_on_zillow": prop.get("daysOnZillow"),
        "views": prop.get("pageViewCount"),
        "saves": prop.get("favoriteCount"),
        "agent_name": agent_name,
        "agent_email": agent_email,
        "agent_phone": agent_phone,
        "broker_name": broker_name,
        "broker_phone": broker_phone,
        "lot_size_sqft": prop.get("lotSize"),
        "year_built": prop.get("yearBuilt"),
        "home_type": prop.get("homeType"),
        "photo_urls": photo_urls,
        "last_sold_price": last_sold,
        "last_sold_date": last_sold_date,
        "raw_json": cache_json,
    }


def get_listings_to_fetch(limit=None, listing_id=None):
    conn = get_conn()
    if listing_id:
        rows = conn.execute("""
            SELECT l.id, l.url, p.address
            FROM listings l
            JOIN properties p ON l.property_id = p.id
            WHERE l.id = ? AND l.source = 'zillow' AND l.url IS NOT NULL
        """, (listing_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT l.id, l.url, p.address
            FROM listings l
            JOIN properties p ON l.property_id = p.id
            LEFT JOIN listing_details ld ON l.id = ld.listing_id
            WHERE l.source = 'zillow' AND l.url IS NOT NULL
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
    parser = argparse.ArgumentParser(description="Scrape Zillow detail pages")
    parser.add_argument("--limit", type=int, help="Max listings to process")
    parser.add_argument("--listing-id", type=int, help="Single listing ID")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests")
    args = parser.parse_args()

    listings = get_listings_to_fetch(limit=args.limit, listing_id=args.listing_id)
    if not listings:
        print("No listings to fetch.")
        sys.exit(0)

    config = BrowserConfig(headless=True, verbose=False,
                           viewport_width=2560, viewport_height=1440)

    print(f"\nFetching details for {len(listings)} Zillow listings...")

    async def process_all():
        done = 0
        errors = 0
        async with AsyncWebCrawler(config=config) as crawler:
            for row in listings:
                lid, url, address = row["id"], row["url"], row["address"]
                if not url:
                    errors += 1
                    continue

                print(f"  [{lid}] {address[:45]:45s} ...", end=" ", flush=True)
                try:
                    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
                    result = await crawler.arun(url, config=run_config)
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

                    data = json.loads(match.group(1))
                    cp = data.get("props", {}).get("pageProps", {}).get("componentProps", {})
                    cache_json = cp.get("gdpClientCache")
                    if not cache_json:
                        print("FAILED (no gdpClientCache)")
                        errors += 1
                        continue

                    details = extract_from_gdp_cache(cache_json)
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
                    await asyncio.sleep(args.delay)

        return done, errors

    done, errors = asyncio.run(process_all())

    print(f"\nDone. {done} loaded, {errors} errors, "
          f"{len(listings) - done - errors} skipped")


if __name__ == "__main__":
    main()
