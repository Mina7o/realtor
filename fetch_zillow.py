import json
import time
import random
import httpx
from parsel import Selector
from db import get_conn

BASE_HEADERS = {
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "accept-encoding": "gzip, deflate, br",
}

def search_zillow(address, city, state, zip_code):
    query = f"{address}, {city}, {state} {zip_code}"
    url = f"https://www.zillow.com/homes/{query}_rb/"

    with httpx.Client(headers=BASE_HEADERS, timeout=15) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"  Search blocked ({resp.status_code})")
            return None

        sel = Selector(resp.text)
        script = sel.css("script#__NEXT_DATA__::text").get()
        if not script:
            print("  No __NEXT_DATA__ found")
            return None

        data = json.loads(script)
        search_state = data["props"]["pageProps"]["searchPageState"]
        query_state = search_state["queryState"]

        payload = {
            "searchQueryState": query_state,
            "wants": {"cat1": ["listResults", "mapResults"], "cat2": ["total"]},
            "requestId": random.randint(2, 10),
        }

        api_resp = client.put(
            "https://www.zillow.com/async-create-search-page-state",
            headers={"content-type": "application/json"},
            content=json.dumps(payload),
        )
        if api_resp.status_code != 200:
            print(f"  API blocked ({api_resp.status_code})")
            return None

        results = api_resp.json().get("cat1", {}).get("searchResults", {}).get("listResults", [])
        return results

def scrape_property_page(zpid):
    url = f"https://www.zillow.com/homedetails/{zpid}_zpid/"
    with httpx.Client(headers=BASE_HEADERS, timeout=15) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            return None

        sel = Selector(resp.text)
        script = sel.css("script#__NEXT_DATA__::text").get()
        if not script:
            return None

        data = json.loads(script)
        try:
            cache = json.loads(
                data["props"]["pageProps"]["componentProps"]["gdpClientCache"]
            )
            prop = cache[list(cache)[0]]["property"]
            return prop
        except (KeyError, json.JSONDecodeError):
            pass

        try:
            apollo = sel.css("script#hdpApolloPreloadedData::text").get()
            if apollo:
                cache = json.loads(json.loads(apollo)["apiCache"])
                prop = next(v["property"] for k, v in cache.items() if "ForSale" in k or "ForRent" in k)
                return prop
        except (KeyError, json.JSONDecodeError, StopIteration):
            pass

        return None

def extract_zestimate(property_data):
    try:
        zestimate = property_data.get("zestimate")
        if zestimate and isinstance(zestimate, dict):
            return zestimate.get("value")
    except Exception:
        pass
    return None

def extract_rent_zestimate(property_data):
    try:
        rent = property_data.get("rentZestimate")
        if rent and isinstance(rent, dict):
            return rent.get("value")
    except Exception:
        pass
    return None

def extract_days_on_zillow(property_data):
    try:
        attrs = property_data.get("attributionInfo", {})
        if isinstance(attrs, dict):
            return attrs.get("daysOnZillow")
    except Exception:
        pass
    return None

def extract_price_history(property_data):
    try:
        history = property_data.get("priceHistory")
        if history and isinstance(history, list):
            return [
                {
                    "date": h.get("date"),
                    "event": h.get("event"),
                    "price": h.get("price"),
                }
                for h in history
            ]
    except Exception:
        pass
    return None

def enrich_properties(limit=20):
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.id, p.address, p.city, p.state, p.zip
        FROM properties p
        WHERE NOT EXISTS (
            SELECT 1 FROM zillow_cache z WHERE z.property_id = p.id
        )
        LIMIT ?
    """, (limit,)).fetchall()

    if not rows:
        print("No properties to enrich")
        return

    print(f"Enriching {len(rows)} properties from Zillow...")

    for row in rows:
        pid, addr, city, state, zip_code = row["id"], row["address"], row["city"], row["state"], row["zip"]
        print(f"\nSearching: {addr}, {city}, {state} {zip_code}")

        results = search_zillow(addr, city, state, zip_code)
        if not results:
            time.sleep(random.uniform(2, 4))
            continue

        match = None
        for r in results:
            if addr.upper() in r.get("address", "").upper():
                match = r
                break
        if not match and results:
            match = results[0]

        if not match:
            print("  No match found")
            time.sleep(random.uniform(2, 4))
            continue

        zpids = []
        raw = match.get("zpid")
        if raw:
            zpids.append(raw)

        detail_url = match.get("detailUrl", "")
        if "/homedetails/" in detail_url:
            zpid = detail_url.split("/")[-1].replace("_zpid/", "")
            if zpid not in zpids:
                zpids.append(zpid)

        for zpid in zpids:
            print(f"  Fetching property page (zpid={zpid})...")
            prop_data = scrape_property_page(zpid)
            if not prop_data:
                continue

            zestimate = extract_zestimate(prop_data)
            rent_zest = extract_rent_zestimate(prop_data)
            days_on = extract_days_on_zillow(prop_data)
            price_hist = extract_price_history(prop_data)

            conn.execute("""
                INSERT OR REPLACE INTO zillow_cache
                (property_id, zpid, zestimate, rent_zestimate, days_on_zillow, price_history, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                pid, zpid, zestimate, rent_zest, days_on,
                json.dumps(price_hist) if price_hist else None,
                json.dumps(prop_data, default=str)[:5000]
            ))
            conn.commit()
            print(f"  Got Zestimate={zestimate}, RentZest={rent_zest}, DaysOnZillow={days_on}")
            break

        time.sleep(random.uniform(2, 4))

    conn.close()
    print(f"\nDone. Enriched {len(rows)} properties")

if __name__ == "__main__":
    enrich_properties(limit=20)
