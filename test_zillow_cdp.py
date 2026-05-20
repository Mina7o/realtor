"""Test Zillow detail page access via real Chrome CDP connection."""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

ZILLOW_DETAIL_URL = "https://www.zillow.com/homedetails/123-Test-St-Charlotte-NC-28205/123456789_zpid/"


async def main():
    async with async_playwright() as p:
        print("Connecting to Chrome via CDP on port 9222...")
        try:
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print(f"FAILED to connect: {e}")
            print("\nMake sure Chrome is running with:")
            print("  flatpak run com.google.Chrome --remote-debugging-port=9222")
            sys.exit(1)

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        # First visit Zillow homepage to establish session
        print("\n1. Visiting Zillow homepage...")
        await page.goto("https://www.zillow.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        title = await page.title()
        url = page.url
        print(f"   Title: {title}")
        print(f"   URL: {url}")

        # Check for verification
        content = await page.content()
        if "verify" in content.lower() or "challenge" in content.lower() or "captcha" in content.lower():
            print("   >>> VERIFICATION DETECTED on homepage!")
        else:
            print("   >>> HOMEPAGE OK - no verification")

        # Now try a real Zillow detail page from our DB
        print("\n2. Looking up a real Zillow detail URL from DB...")
        try:
            sys.path.insert(0, "/home/euclid/Documents/proj/realtor")
            from db import get_conn
            conn = get_conn()
            row = conn.execute(
                "SELECT l.url, p.address FROM listings l JOIN properties p ON l.property_id = p.id "
                "WHERE l.source = 'zillow' AND l.url IS NOT NULL AND l.url LIKE '%homedetails%' "
                "LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                detail_url = row["url"]
                print(f"   Testing: {row['address'][:60]}...")
            else:
                print("   No Zillow listings with homedetails URL found, using fallback")
                detail_url = ZILLOW_DETAIL_URL
        except Exception as e:
            print(f"   DB error: {e}, using fallback URL")
            detail_url = ZILLOW_DETAIL_URL

        print(f"\n3. Navigating to detail page...")
        print(f"   URL: {detail_url[:100]}")
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            title2 = await page.title()
            url2 = page.url
            print(f"   Title: {title2}")
            print(f"   URL: {url2}")

            content2 = await page.content()
            if "verify" in content2.lower() or "challenge" in content2.lower() or "captcha" in content2.lower():
                print("   >>> VERIFICATION DETECTED on detail page!")
            elif "zestimate" in content2.lower() or "price" in content2.lower():
                print("   >>> DETAIL PAGE LOADED SUCCESSFULLY - data visible!")
            else:
                # check for __NEXT_DATA__
                import re
                match = re.search(r'__NEXT_DATA__[^>]*>(.*?)</script>', content2, re.DOTALL)
                if match:
                    print("   >>> PAGE HAS __NEXT_DATA__ (good sign)")
                else:
                    print(f"   >>> UNKNOWN STATE - page length: {len(content2)} chars")
        except Exception as e:
            print(f"   ERROR: {e}")

        print("\nDone. Press Ctrl+C to close or keep Chrome open.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
