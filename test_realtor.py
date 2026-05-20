import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

async def main():
    config = BrowserConfig(headless=True)
    async with AsyncWebCrawler(config=config) as crawler:
        run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True)
        result = await crawler.arun("https://www.realtor.com/realestateandhomes-search/Charlotte_NC", config=run_config)
        
        import re
        links = re.findall(r'href="(/realestateandhomes-detail/[^"]+)"', result.html)
        if links:
            print("FOUND LINK:", links[0])
            # Now fetch that link
            url = "https://www.realtor.com" + links[0]
            result2 = await crawler.arun(url, config=run_config)
            
            match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', result2.html, re.DOTALL)
            if match:
                with open("realtor_next_data.json", "w") as f:
                    f.write(match.group(1))
                print("SAVED __NEXT_DATA__")
            else:
                print("NO __NEXT_DATA__ FOUND")

asyncio.run(main())
