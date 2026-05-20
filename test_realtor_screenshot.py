import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

async def main():
    config = BrowserConfig(headless=True)
    async with AsyncWebCrawler(config=config) as crawler:
        run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, magic=True, screenshot=True)
        result = await crawler.arun("https://www.realtor.com/realestateandhomes-search/Fort-Mill_SC", config=run_config)
        import base64
        if result.screenshot:
            with open("realtor_block.jpg", "wb") as f:
                f.write(base64.b64decode(result.screenshot))
            print("Saved screenshot")
        else:
            print("No screenshot", result.success, result.status_code)

asyncio.run(main())
