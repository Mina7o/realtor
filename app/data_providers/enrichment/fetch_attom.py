import json
import os
import time
import requests
from db import get_conn
from otel_utils import init_otel

API_KEY = os.getenv("ATTOM_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ATTOM_API_KEY environment variable not set")
BASE = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"

HEADERS = {
    "Accept": "application/json",
    "APIKey": API_KEY,
}

def call_api(endpoint, address1, address2):
    url = f"{BASE}/{endpoint}"
    params = {"address1": address1, "address2": address2}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if resp.status_code == 200:
        return resp.json()
    return None

def enrich_properties(limit=50):
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT p.id, p.address, p.city, p.state, p.zip
        FROM properties p
        JOIN listings l ON p.id = l.property_id
        WHERE NOT EXISTS (
            SELECT 1 FROM attom_cache a WHERE a.property_id = p.id
        )
        LIMIT ?
    """, (limit,)).fetchall()

    if not rows:
        print("No properties to enrich")
        return

    print(f"Enriching {len(rows)} properties from ATTOM...")

    for row in rows:
        pid, addr, city, state, zip_code = row["id"], row["address"], row["city"], row["state"], row["zip"]

        addr1 = addr.split(",")[0].strip()
        addr2 = f"{city}, {state} {zip_code}".strip()

        print(f"\n[{pid}] {addr1}, {addr2}")

        detail = call_api("property/expandedprofile", addr1, addr2)
        if not detail or detail["status"]["code"] != 0:
            print("  No property found (expandedprofile)")
            time.sleep(0.5)
            continue

        prop = detail["property"][0]
        attom_id = prop["identifier"]["attomId"]
        apn = prop["identifier"]["apn"]
        fips = prop["identifier"]["fips"]

        owner = prop.get("owner", {})
        owner_name = owner.get("owner1", {}).get("fullname") if owner.get("owner1") else None
        owner_mail = owner.get("mailingaddressoneline")
        absentee_status = owner.get("absenteeownerstatus")
        corporate_ind = owner.get("corporateindicator")

        summary = prop.get("summary", {})
        year_built = summary.get("yearbuilt")

        building = prop.get("building", {})
        rooms = building.get("rooms", {})
        beds = rooms.get("beds")
        baths = rooms.get("bathstotal")
        size = building.get("size", {})
        sqft = size.get("universalsize") or size.get("livingsize")

        construction = building.get("construction", {})
        quality = building.get("summary", {}).get("quality")
        condition = construction.get("condition")

        lot = prop.get("lot", {})
        lot_acres = lot.get("lotsize1")

        prop_type = summary.get("propertyType")

        zoning = prop.get("zoning", {}) or {}
        land_use = prop.get("landUse", {}) or {}

        avm = prop.get("avm", {})
        amt = avm.get("amount", {})
        avm_value = amt.get("value")
        avm_high = amt.get("high")
        avm_low = amt.get("low")
        avm_conf = amt.get("scr")
        assessment = prop.get("assessment", {})
        assessed_val = assessment.get("assessed", {}).get("assdttlvalue")
        market_val = assessment.get("market", {}).get("mktttlvalue")

        zoning_code = (zoning.get("zoningCode") or "").strip() if zoning else ""
        zoning_desc = (zoning.get("zoningDescription") or "").strip() if zoning else ""
        lu_code = (land_use.get("landUseCode") or "").strip() if land_use else ""
        lu_desc = (land_use.get("landUseDescription") or "").strip() if land_use else ""

        conn.execute("""
            INSERT OR REPLACE INTO attom_cache
            (property_id, attom_id, apn, fips,
             owner_name, owner_mail_address, absentee_status, corporate_indicator,
             avm_value, avm_high, avm_low, avm_confidence,
             assessed_value, market_value,
             year_built, beds, baths, sqft, lot_acres,
             zoning_code, zoning_description, land_use_code, land_use_description,
             property_type, quality, condition, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pid, attom_id, apn, fips,
            owner_name, owner_mail, absentee_status, corporate_ind,
            avm_value, avm_high, avm_low, avm_conf,
            assessed_val, market_val,
            year_built, beds, baths, sqft, lot_acres,
            zoning_code, zoning_desc, lu_code, lu_desc,
            prop_type, quality, condition,
            json.dumps(prop, default=str)[:10000]
        ))
        conn.commit()

        print(f"  Owner: {owner_name or 'N/A'}, AVM: ${avm_value or 'N/A'}, Beds: {beds}, Baths: {baths}, Sqft: {sqft}")
        if absentee_status == "A":
            print(f"  ** ABSENTEE OWNER ** Mail: {owner_mail}")
        if avm_value and avm_conf:
            print(f"  AVM: ${avm_value} (conf: {avm_conf}%), range: ${avm_low}-${avm_high}")

        time.sleep(0.3)

    enriched = conn.execute("SELECT COUNT(*) FROM attom_cache").fetchone()[0]
    conn.close()
    print(f"\nDone. Total enriched: {enriched}")

if __name__ == "__main__":
    tracer = init_otel("fetch_attom")
    with tracer.start_as_current_span("enrich_properties"):
        enrich_properties(limit=50)
