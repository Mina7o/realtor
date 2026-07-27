"""
Zillow detail scraper using real Chrome profile via Playwright.
Bypasses PerimeterX by navigating through search results.

Usage:
  1. Start Chrome with: flatpak run com.google.Chrome --no-first-run
  2. Log into Zillow in the opened browser
  3. Run:
       python3 fetch_zillow_details_browser_use.py
       python3 fetch_zillow_details_browser_use.py --limit 10
       python3 fetch_zillow_details_browser_use.py --listing-id 123
"""
import argparse
import asyncio
import json
import os
import re
import sys
import random

sys.path.insert(0, os.path.dirname(__file__))
from db import get_conn

from playwright.async_api import async_playwright


async def extract_listing_data(page):
    """Extract data from Zillow detail page using DOM selectors."""
    data = await page.evaluate("""
        () => {
            const result = {};
            
            // Helper to get text by selector
            const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? el.textContent.trim() : null;
            };
            
            // Price
            const priceEl = document.querySelector('[data-testid="price"]');
            result.price = priceEl ? priceEl.textContent.trim() : null;
            
            // Key facts (beds, baths, sqft)
            const facts = document.querySelectorAll('[data-testid="bed-bath-sqft"]');
            // ... extract each
            
            // Description
            const descEl = document.querySelector('[data-testid="description"]');
            result.description = descEl ? descEl.textContent.trim() : null;
            
            // Agent info
            const agentEl = document.querySelector('[data-testid="seller-info"]');
            // ... extract agent name, phone
            
            // HOA
            // ... look for HOA section
            
            // Last sold
            const priceHistory = document.querySelector('[data-testid="price-history"]');
            // ... extract last sold price and date
            
            // Photos
            const imgEls = document.querySelectorAll('[data-testid="carousel"] img');
            result.photo_urls = Array.from(imgEls).map(img => img.src).join(',');
            
            return result;
        }
    """)
    return data


def get_listings(limit=None, listing_id=None):
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
        json.dumps(details),
    ))
    conn.commit()
    conn.close()


async def scrape_listing(page, lid, url, address):
    """Navigate to a Zillow detail page and extract data."""
    try:
        # First navigate to search results page
        search_url = 'https://www.zillow.com/monroe-nc/'
        await page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(2000)
        
        # Then navigate to detail URL
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)
        
        # Extract data
        data = await extract_listing_data(page)
        if data:
            save_details(lid, data)
            desc = (data.get("description") or "")[:60]
            return f"OK  {desc}"
        else:
            return "NO_DATA"
    except Exception as e:
        return f"ERROR: {e}"


async def main():
    parser = argparse.ArgumentParser(description="Scrape Zillow detail pages via real Chrome")
    parser.add_argument("--limit", type=int, help="Max listings to process")
    parser.add_argument("--listing-id", type=int, help="Single listing ID")
    args = parser.parse_args()

    listings = get_listings(limit=args.limit, listing_id=args.listing_id)
    if not listings:
        print("No listings to fetch.")
        sys.exit(0)

    async with async_playwright() as p:
        # Launch Chrome with real profile
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=os.path.expanduser("~/.var/app/com.google.Chrome/config/google-chrome"),
            headless=False,
            args=['--no-first-run', '--no-default-browser-check']
        )
        page = await browser.new_page()

        # First visit search results page directly
        search_url = 'https://www.zillow.com/monroe-nc/'
        print(f"Visiting search page directly: {search_url}")
        try:
            await page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(2000)
            print(f"  Title: {await page.title()}")
        except Exception as e:
            print(f"\nFailed to load search page: {e}")
            print(f"  Current URL: {page.url}")
            print(f"  Page content length: {len(await page.content())}")
            await browser.close()
            sys.exit(1)

        # Alternative approach: try to find and click a listing card
        try:
            print("\nLooking for listing cards...")
            cards = await page.query_selector_all('a[data-test="property-card-link"]')
            if not cards:
                print("No listing cards found. Trying alternative selectors...")
                cards = await page.query_selector_all('article[data-testid="property-card"] a')

            if cards:
                print(f"Found {len(cards)} listing cards")
                # Click first card
                await cards[0].click()
                await page.wait_for_timeout(3000)
                print(f"After click URL: {page.url}")

                # Extract data from detail page
                data = await extract_listing_data(page)
                if data:
                    print(f"Extracted data: {data.get('description', 'N/A')[:60]}")
                else:
                    print("Failed to extract data from detail page")
            else:
                print("No listing cards found on search page")
        except Exception as e:
            print(f"\nError interacting with listing cards: {e}")

        total = len(listings)
        done = 0
        errors = 0

        print(f"\nFetching details for {total} Zillow listings...\n")

        for i, row in enumerate(listings, 1):
            lid, url, address = row["id"], row["url"], row["address"]
            if not url:
                print(f"  [{i}/{total}] [{lid}] {address[:45]:45s} ... NO_URL")
                errors += 1
                continue

            print(f"  [{i}/{total}] [{lid}] {address[:45]:45s} ...", end=" ", flush=True)
            result = await scrape_listing(page, lid, url, address)
            print(result)

            if result.startswith("OK"):
                done += 1
            else:
                errors += 1

            if i < total:
                delay = random.uniform(3, 7)
                print(f"         waiting {delay:.1f}s ...")
                await asyncio.sleep(delay)

        await page.close()
        await browser.close()

        print(f"\nDone. {done} loaded, {errors} errors, {total - done - errors} skipped")


if __name__ == "__main__":
    asyncio.run(main())
