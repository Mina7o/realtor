"""Scrape EDPNC certified sites and economic development sites using crawl4ai.
"""
import asyncio
import json
import os
import re

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def main():
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        # 1. NC Commerce certified sites page - check for embedded list
        print("1. NC Commerce certified sites...")
        r = await crawler.arun("https://www.commerce.nc.gov/data-tools-reports/north-carolina-certified-sites", config=run_cfg)
        if r.success:
            with open(f"{OUTPUT_DIR}/nc_commerce.html", "w") as f:
                f.write(r.html)
            # Look for any list of sites
            for m in re.findall(r'(?:Union|Mecklenburg|York)\s+County[^<]*', r.html):
                print(f"  Found: {m.strip()[:120]}")
            # Check for PDF links
            for m in re.findall(r'href="([^"]+\.pdf)"[^>]*>', r.html):
                if 'certified' in m.lower() or 'site' in m.lower():
                    print(f"  PDF: {m}")

        # 2. Union County Development Corporation site listings
        print("\n2. Union County site listings...")
        r = await crawler.arun("https://unioncountycorp.com/sites-buildings/featured-sites", config=run_cfg)
        if r.success:
            with open(f"{OUTPUT_DIR}/unioncorp.html", "w") as f:
                f.write(r.html)
            # Extract site names
            sites = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', r.html)
            for s in sites:
                s = re.sub(r'<[^>]+>', '', s).strip()
                if s and ('park' in s.lower() or 'site' in s.lower() or 'industrial' in s.lower()):
                    print(f"  Site: {s}")

        # 3. Charlotte-Mecklenburg rezoning for data center site
        print("\n3. Mecklenburg data center rezoning...")
        r = await crawler.arun("https://charlottenc.legistar.com/View.ashx?G=E8128701-6BB3-4D7D-A5ED-825F33F967B9&ID=14129259&M=F", config=run_cfg)
        if r.success:
            with open(f"{OUTPUT_DIR}/mecklenburg_rezoning.html", "w") as f:
                f.write(r.html)
            # Extract key details
            text = r.html
            for line in text.split('\n'):
                line = line.strip()
                if any(w in line.lower() for w in ['acres', 'data center', 'data storage', 'telecom', 'square feet', 'i-2']):
                    clean = re.sub(r'<[^>]+>', '', line).strip()
                    if clean:
                        print(f"  {clean[:150]}")

        # 4. York County site listings
        print("\n4. York County industrial sites...")
        r = await crawler.arun("https://www.yorkcountyed.com/sites-buildings", config=run_cfg)
        if r.success:
            with open(f"{OUTPUT_DIR}/york_sites.html", "w") as f:
                f.write(r.html)
            sites = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', r.html)
            for s in sites:
                s = re.sub(r'<[^>]+>', '', s).strip()
                if s and ('park' in s.lower() or 'site' in s.lower() or 'industrial' in s.lower()):
                    print(f"  Site: {s}")

        # 5. Charlotte Regional Business Alliance site database
        print("\n5. Charlotte Regional Business Alliance...")
        r = await crawler.arun("https://charlotteregion.com/economic-development/sites-and-buildings", config=run_cfg)
        if r.success:
            with open(f"{OUTPUT_DIR}/clt_alliance.html", "w") as f:
                f.write(r.html)
            # Look for site listings
            for m in re.findall(r'(?:data center|industrial|site|park|acres)[^<]*', r.html, re.IGNORECASE):
                clean = re.sub(r'<[^>]+>', '', m).strip()
                if len(clean) > 20:
                    print(f"  {clean[:150]}")

    print(f"\nDone. All output saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
