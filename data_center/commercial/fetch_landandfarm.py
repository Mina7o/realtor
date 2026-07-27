"""Fetch LandAndFarm.com land listings via crawl4ai."""
from logger_setup import setup_logging
from loguru import logger
from otel_utils import init_otel

setup_logging("fetch_landandfarm")

import asyncio
import re
import sqlite3
import json
import argparse
from datetime import datetime
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

DB_PATH = Path(__file__).resolve().parent.parent / "deals.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

COUNTY_SLUGS = {
    "NC": {
        "Mecklenburg": "mecklenburg-county",
        "Cabarrus": "cabarrus-county",
        "Rowan": "rowan-county",
        "Iredell": "iredell-county",
        "Durham": "durham-county",
        "Orange": "orange-county",
        "Guilford": "guilford-county",
        "Forsyth": "forsyth-county",
        "Johnston": "johnston-county",
        "Chatham": "chatham-county",
    },
    "SC": {
        "Greenville": "greenville-county",
        "Spartanburg": "spartanburg-county",
        "Charleston": "charleston-county",
        "Berkeley": "berkeley-county",
        "Richland": "richland-county",
        "Lexington": "lexington-county",
    },
    "TX": {
        "Dallas": "dallas-county",
        "Travis": "travis-county",
        "Tarrant": "tarrant-county",
        "Collin": "collin-county",
        "Denton": "denton-county",
        "Bexar": "bexar-county",
        "Harris": "harris-county",
        "Williamson": "williamson-county",
        "Ellis": "ellis-county",
        "Johnson": "johnson-county",
        "Hood": "hood-county",
        "Grayson": "grayson-county",
        "Navarro": "navarro-county",
        "Parker": "parker-county",
        "Wise": "wise-county",
        "Rockwall": "rockwall-county",
        "Kaufman": "kaufman-county",
        "Hunt": "hunt-county",
        "Cooke": "cooke-county",
    },
    "GA": {
        "Fulton": "fulton-county",
        "DeKalb": "dekalb-county",
        "Cobb": "cobb-county",
        "Gwinnett": "gwinnett-county",
        "Cherokee": "cherokee-county",
        "Henry": "henry-county",
        "Forsyth": "forsyth-county",
        "Douglas": "douglas-county",
        "Paulding": "paulding-county",
        "Bartow": "bartow-county",
        "Coweta": "coweta-county",
        "Fayette": "fayette-county",
        "Newton": "newton-county",
        "Rockdale": "rockdale-county",
    },
}

BROWSER_CONFIG = BrowserConfig(headless=True, verbose=False)


def parse_acres(text: str) -> float | None:
    text = text.replace(",", "").strip().lower()
    m = re.search(r"([\d.]+)\s*acres?", text)
    if m:
        return float(m.group(1))
    return None


def parse_price(text: str) -> float | None:
    text = text.strip()
    if text.lower() == "auction":
        return None
    m = re.search(r"\$?([\d,]+(?:\.\d{2})?)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def extract_listings(html: str) -> list[dict]:
    listings = []
    placard_ids = re.findall(r'data-qa-placard="(\d+)"', html)
    if not placard_ids:
        return listings

    for pid in set(placard_ids):
        try:
            prices = re.findall(
                rf'<span class="_47a280d">(.*?)</span>',
                html[html.find(f'data-qa-placard="{pid}"'):]
            )
            acres_all = re.findall(
                rf'<span class="_28423b5">(.*?)</span>',
                html[html.find(f'data-qa-placard="{pid}"'):]
            )

            start = html.find(f'data-qa-placard="{pid}"')
            next_placard = html.find('data-qa-placard="', start + 30)
            if next_placard == -1:
                next_placard = start + 8000
            card_section = html[start:next_placard]

            addr_match = re.search(
                r'<p[^>]*class="(?:aceb1be|_28d22f4)"[^>]*>(.*?)</p>',
                card_section, re.DOTALL
            )
            address = addr_match.group(1).strip() if addr_match else ""

            jsonld_match = re.search(
                r'"name":"([^"]+)"',
                card_section, re.DOTALL
            )
            title_json = jsonld_match.group(1).strip() if jsonld_match else ""

            title_div_match = re.search(
                r'<div class="_3e90c15"><a[^>]*>(.*?)</a>',
                card_section, re.DOTALL
            )
            title_div = title_div_match.group(1).strip() if title_div_match else ""

            title = title_json or title_div

            if not address and title_json:
                name_parts = title_json.split(",")
                if len(name_parts) >= 3:
                    address = title_json

            desc_match = re.search(
                rf'id="placard-description-[^"]*"[^>]*>(.*?)</div>',
                card_section, re.DOTALL
            )
            description = desc_match.group(1).strip() if desc_match else ""

            if not description:
                jsonld_desc = re.search(
                    r'"@type"[^}]*"description":"([^"]+)"',
                    card_section, re.DOTALL
                )
                if jsonld_desc:
                    description = jsonld_desc.group(1).strip()

            url_match = re.search(
                r'href="(/property/[^"]+)"',
                card_section
            )
            url = "https://www.landandfarm.com" + url_match.group(1) if url_match else ""

            price_text = prices[0].strip() if prices else ""
            acres_text = acres_all[0].strip() if acres_all else ""
            acres_val = parse_acres(acres_text)
            price_val = parse_price(price_text)

            state_match = re.search(r"([A-Z]{2})\b", address)

            listing = {
                "pid": pid,
                "title": title,
                "address": address,
                "state": state_match.group(1) if state_match else "",
                "price": price_val,
                "price_text": price_text,
                "acres": acres_val,
                "acres_text": acres_text,
                "description": description,
                "url": url,
            }
            listings.append(listing)
        except Exception:
            continue

    return listings


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS landandfarm_listings (
            pid TEXT PRIMARY KEY,
            county TEXT NOT NULL,
            state TEXT NOT NULL,
            title TEXT,
            address TEXT,
            price REAL,
            price_text TEXT,
            acres REAL,
            acres_text TEXT,
            description TEXT,
            url TEXT,
            fetched_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_landandfarm_county 
        ON landandfarm_listings(county, state)
    """)
    conn.commit()


def save_listings(conn: sqlite3.Connection, county: str, state: str, listings: list[dict]):
    now = datetime.utcnow().isoformat()
    for l in listings:
        conn.execute(
            """INSERT OR REPLACE INTO landandfarm_listings 
               (pid, county, state, title, address, price, price_text, acres, acres_text, description, url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                l["pid"], county, state, l["title"], l["address"],
                l["price"], l["price_text"], l["acres"], l["acres_text"],
                l["description"], l["url"], now,
            ),
        )
    conn.commit()


def make_county_url(state_slug: str, county_slug: str) -> str:
    return f"https://www.landandfarm.com/search/land/{state_slug}/{county_slug}/"


def make_page_url(state_slug: str, county_slug: str, page: int) -> str:
    return f"https://www.landandfarm.com/search/{state_slug}/{county_slug}-land-for-sale/page-{page}/"


STATE_SLUGS = {"NC": "north-carolina", "SC": "south-carolina", "GA": "georgia", "FL": "florida", "VA": "virginia", "TX": "texas"}


async def fetch_county(crawler: AsyncWebCrawler, state: str, county: str) -> int:
    county_slug = COUNTY_SLUGS[state][county]
    state_slug = STATE_SLUGS[state]
    first_url = make_county_url(state_slug, county_slug)

    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
    result = await crawler.arun(first_url, config=config)

    count_match = re.search(r"(\d+)\s+listings?", result.html, re.IGNORECASE)
    total_listings = int(count_match.group(1)) if count_match else 0
    total_pages = max(1, (total_listings + 24) // 25)

    all_listings = extract_listings(result.html)
    print(f"  Page 1: {len(extract_listings(result.html))} listings extracted")

    for page in range(2, total_pages + 1):
        page_url = make_page_url(state_slug, county_slug, page)
        result = await crawler.arun(page_url, config=config)
        page_listings = extract_listings(result.html)
        if not page_listings:
            break
        all_listings.extend(page_listings)
        print(f"  Page {page}: {len(page_listings)} listings extracted")

    return all_listings


async def main():
    tracer = init_otel("fetch_landandfarm")
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", "-s", help="State code (NC, SC, GA)")
    parser.add_argument("--county", "-c", help="County name")
    parser.add_argument("--min-acres", type=float, default=10.0, help="Minimum acreage filter")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    ensure_table(conn)

    states_to_fetch = {}
    if args.state:
        if args.state in COUNTY_SLUGS:
            if args.county:
                states_to_fetch[args.state] = [args.county]
            else:
                states_to_fetch[args.state] = list(COUNTY_SLUGS[args.state].keys())
        else:
            print(f"Unknown state: {args.state}. Available: {list(COUNTY_SLUGS.keys())}")
            return
    else:
        states_to_fetch = {s: list(cs.keys()) for s, cs in COUNTY_SLUGS.items()}

    async with AsyncWebCrawler(config=BROWSER_CONFIG) as crawler:
        for state, counties in states_to_fetch.items():
            for county in counties:
                print(f"\n[{state}] {county}...")
                try:
                    listings = await fetch_county(crawler, state, county)
                    before = len(listings)
                    if args.min_acres:
                        listings = [l for l in listings if l["acres"] is not None and l["acres"] >= args.min_acres]
                    save_listings(conn, county, state, listings)
                    print(f"  Saved {len(listings)}/{before} listings (acres >= {args.min_acres})")
                except Exception as e:
                    print(f"  ERROR: {e}")

    conn.close()

    cur = conn = sqlite3.connect(str(DB_PATH))
    row = cur.execute("SELECT COUNT(*) FROM landandfarm_listings").fetchone()
    print(f"\nTotal landandfarm_listings in DB: {row[0]}")
    cur.close()


if __name__ == "__main__":
    asyncio.run(main())
