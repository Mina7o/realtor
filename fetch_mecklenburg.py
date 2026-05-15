import os
import sys
import time
import sqlite3
import requests

COUNTY_DB = os.path.join(os.path.dirname(__file__), 'county_parcels_full.db')

def get_county_conn():
    conn = sqlite3.connect(COUNTY_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn

API_URL = (
    "https://meckgis.mecklenburgcountync.gov/server/rest/services/"
    "TaxParcel_Camaownershipvalues/FeatureServer/0/query"
)
PAGE_SIZE = 2000
STATE_FILE = os.path.join(os.path.dirname(__file__), ".mecklenburg_offset")

FIELDS = [
    "pid", "situsaddress1", "full_owner_name",
    "amt_totalvalue", "amt_landvalue", "amt_netbldgvalue",
    "amt_price", "dte_dateofsale",
    "txt_propertyuse_desc", "num_totalac",
    "txt_mailaddr1", "txt_mailaddr2", "txt_city", "txt_state", "txt_zipcode",
    "txt_legaldesc", "camapid", "id_commonpid",
    "nme_ownerlastname", "nme_ownerfirstname",
    "secownerlastname", "secownerfirstname",
]

def ensure_table():
    conn = get_county_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mecklenburg_parcels (
            pid TEXT PRIMARY KEY,
            situsaddress1 TEXT,
            full_owner_name TEXT,
            amt_totalvalue REAL,
            amt_landvalue REAL,
            amt_netbldgvalue REAL,
            amt_price REAL,
            dte_dateofsale TEXT,
            txt_propertyuse_desc TEXT,
            num_totalac REAL,
            txt_mailaddr1 TEXT,
            txt_mailaddr2 TEXT,
            txt_city TEXT,
            txt_state TEXT,
            txt_zipcode TEXT,
            txt_legaldesc TEXT,
            camapid TEXT,
            id_commonpid TEXT,
            nme_ownerlastname TEXT,
            nme_ownerfirstname TEXT,
            secownerlastname TEXT,
            secownerfirstname TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meck_situs ON mecklenburg_parcels(situsaddress1)")
    conn.commit()
    conn.close()

def load_offset():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return int(f.read().strip())
    return 0

def save_offset(offset):
    with open(STATE_FILE, "w") as f:
        f.write(str(offset))

def get_total_count():
    params = {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    r = requests.get(API_URL, params=params, timeout=30)
    return r.json().get("count", 0)

def fetch_page(offset):
    params = {
        "where": "1=1",
        "outFields": ",".join(FIELDS),
        "returnGeometry": "false",
        "f": "json",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
    }
    r = requests.get(API_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def epoch_to_date(ms):
    if not ms or ms < 0:
        return None
    from datetime import datetime
    return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")

def store_batch(conn, features):
    rows = []
    for f in features:
        a = f["attributes"]
        rows.append((
            a.get("pid"),
            a.get("situsaddress1"),
            a.get("full_owner_name"),
            a.get("amt_totalvalue"),
            a.get("amt_landvalue"),
            a.get("amt_netbldgvalue"),
            a.get("amt_price"),
            epoch_to_date(a.get("dte_dateofsale")),
            a.get("txt_propertyuse_desc"),
            a.get("num_totalac"),
            a.get("txt_mailaddr1"),
            a.get("txt_mailaddr2"),
            a.get("txt_city"),
            a.get("txt_state"),
            a.get("txt_zipcode"),
            a.get("txt_legaldesc"),
            a.get("camapid"),
            a.get("id_commonpid"),
            a.get("nme_ownerlastname"),
            a.get("nme_ownerfirstname"),
            a.get("secownerlastname"),
            a.get("secownerfirstname"),
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO mecklenburg_parcels
        (pid, situsaddress1, full_owner_name,
         amt_totalvalue, amt_landvalue, amt_netbldgvalue,
         amt_price, dte_dateofsale,
         txt_propertyuse_desc, num_totalac,
         txt_mailaddr1, txt_mailaddr2, txt_city, txt_state, txt_zipcode,
         txt_legaldesc, camapid, id_commonpid,
         nme_ownerlastname, nme_ownerfirstname,
         secownerlastname, secownerfirstname)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()

def main():
    ensure_table()
    total = get_total_count()
    print(f"Total parcels: {total}")

    offset = load_offset()
    if offset > 0:
        print(f"Resuming from offset {offset}")
    else:
        print("Starting fresh download")

    conn = get_county_conn()
    page = 0
    errors = 0
    max_errors = 10

    while offset < total:
        try:
            data = fetch_page(offset)
            features = data.get("features", [])
            if not features:
                print(f"  No features at offset {offset}, stopping")
                break
            store_batch(conn, features)
            offset += len(features)
            save_offset(offset)
            errors = 0
            page += 1
            pct = min(offset * 100 / total, 100)
            print(f"  Page {page}: offset={offset}/{total} ({pct:.1f}%) - {len(features)} records")
        except Exception as e:
            errors += 1
            print(f"  Error at offset {offset}: {e}")
            if errors > max_errors:
                print("Too many errors, aborting")
                break
            time.sleep(5)

    conn.close()
    print(f"\nDone. Total records: {offset}")

if __name__ == "__main__":
    main()
