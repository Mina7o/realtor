import sqlite3
import os
from pathlib import Path
from otel_utils import init_otel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / 'deals.db'
_otel_tracer = None

def _get_tracer():
    global _otel_tracer
    if _otel_tracer is None:
        _otel_tracer = init_otel("realtor-db")
    return _otel_tracer

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_conn()
    try:
        schema = os.path.join(os.path.dirname(__file__), 'schema.sql')
        with open(schema) as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()

def upsert_county(name, state, fips=None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO counties (name, state, fips) VALUES (?, ?, ?)",
            (name, state, fips)
        )
        conn.commit()
        row = conn.execute("SELECT id FROM counties WHERE name=? AND state=?", (name, state)).fetchone()
        return row['id'] if row else None
    finally:
        conn.close()

def upsert_property(address, city, state, zip_code, county_id, lat=None, lng=None, parcel_id=None):
    with _get_tracer().start_as_current_span("upsert_property") as span:
        span.set_attribute("address", address or "")
        span.set_attribute("city", city or "")
        norm = normalize_address(address, city, state, zip_code)
        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM properties WHERE normalized_address=?", (norm,)
            ).fetchone()
            if existing:
                return existing['id']
            conn.execute(
                """INSERT INTO properties (address, city, state, zip, county_id, lat, lng, parcel_id, normalized_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (address, city, state, zip_code, county_id, lat, lng, parcel_id, norm)
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

def upsert_tax_record(property_id, tax_year=None, mkt_val_total=None, mkt_val_land=None,
                       mkt_val_building=None, sale_price=None, sale_date=None,
                       year_built=None, sqft=None, bedrooms=None, bathrooms=None,
                       land_use=None, lot_sqft=None):
    with _get_tracer().start_as_current_span("upsert_tax_record") as span:
        span.set_attribute("property_id", property_id)
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO tax_records (property_id, tax_year, mkt_val_total, mkt_val_land, mkt_val_building,
                    sale_price, sale_date, year_built, sqft, bedrooms, bathrooms, land_use, lot_sqft)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(property_id, tax_year) DO UPDATE SET
                       mkt_val_total=excluded.mkt_val_total,
                       mkt_val_land=excluded.mkt_val_land,
                       mkt_val_building=excluded.mkt_val_building,
                       sale_price=excluded.sale_price,
                       sale_date=excluded.sale_date,
                       year_built=excluded.year_built,
                       sqft=excluded.sqft,
                       bedrooms=excluded.bedrooms,
                       bathrooms=excluded.bathrooms,
                       land_use=excluded.land_use,
                       lot_sqft=excluded.lot_sqft""",
                (property_id, tax_year, mkt_val_total, mkt_val_land, mkt_val_building,
                 sale_price, sale_date, year_built, sqft, bedrooms, bathrooms, land_use, lot_sqft)
            )
            conn.commit()
        finally:
            conn.close()

def upsert_listing(property_id, list_price, listing_status=None, listing_date=None,
                    source=None, mls_id=None, url=None,
                    zestimate=None, broker_name=None, img_url=None, status_text=None):
    with _get_tracer().start_as_current_span("upsert_listing") as span:
        span.set_attribute("property_id", property_id)
        span.set_attribute("source", source or "")
        conn = get_conn()
        try:
            existing = conn.execute(
                "SELECT id, list_price FROM listings WHERE property_id=? AND source=?",
                (property_id, source)
            ).fetchone()
            if existing and existing['list_price'] != list_price:
                conn.execute(
                    """INSERT INTO price_history (listing_id, property_id, old_price, new_price, source, detected_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                    (existing['id'], property_id, existing['list_price'], list_price, source)
                )
            conn.execute(
                """INSERT INTO listings (property_id, list_price, listing_status, listing_date,
                   source, mls_id, url, zestimate, broker_name, img_url, status_text, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(property_id, source) DO UPDATE SET
                       list_price=excluded.list_price,
                       listing_status=excluded.listing_status,
                       listing_date=excluded.listing_date,
                       url=excluded.url,
                       zestimate=excluded.zestimate,
                       broker_name=excluded.broker_name,
                       img_url=excluded.img_url,
                       status_text=excluded.status_text,
                       last_seen_at=datetime('now')""",
                (property_id, list_price, listing_status, listing_date, source, mls_id, url,
                 zestimate, broker_name, img_url, status_text)
            )
            conn.commit()
        finally:
            conn.close()


def set_property_details(property_id, bedrooms=None, bathrooms=None, sqft=None,
                          year_built=None, lot_sqft=None, property_type=None):
    if all(v is None for v in (bedrooms, bathrooms, sqft, year_built, lot_sqft, property_type)):
        return
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO property_details (property_id, bedrooms, bathrooms, sqft,
               year_built, lot_sqft, property_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(property_id) DO UPDATE SET
                bedrooms = COALESCE(excluded.bedrooms, bedrooms),
                bathrooms = COALESCE(excluded.bathrooms, bathrooms),
                sqft = COALESCE(excluded.sqft, sqft),
                year_built = COALESCE(excluded.year_built, year_built),
                lot_sqft = COALESCE(excluded.lot_sqft, lot_sqft),
                property_type = COALESCE(excluded.property_type, property_type),
                updated_at = datetime('now')
        """, (property_id, bedrooms, bathrooms, sqft, year_built, lot_sqft, property_type))
        conn.commit()
    finally:
        conn.close()


def get_property_details(property_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM property_details WHERE property_id=?", (property_id,)
        ).fetchone()
    finally:
        conn.close()

def normalize_address(address, city, state, zip_code):
    import re
    if not address:
        return None
    addr = address.strip().upper()
    addr = re.sub(r'\b(STREET|DRIVE|AVENUE|ROAD|LANE|COURT|CIRCLE|BOULEVARD|HIGHWAY|PLACE|WAY|PARKWAY|SQUARE|TERRACE|TRACE|VIEW|RIDGE|WALK|RUN|DR|ST|AVE|RD|LN|CT|CIR|BLVD|HWY|PL|PKWY|SQ|TER|TR|VW)\b\.?', '', addr)
    addr = re.sub(r'\b(NORTH|SOUTH|EAST|WEST|NO|SO|EA|WE|N|S|E|W)\b\.?', '', addr)
    addr = re.sub(r'\b(SUITE|UNIT|APT|#)\s*\w*\b', '', addr)
    addr = re.sub(r'[^A-Z0-9\s]', '', addr)
    addr = re.sub(r'\s+', ' ', addr).strip()
    city_norm = (city or '').strip().upper()
    state_norm = (state or '').strip().upper()
    zip_norm = (zip_code or '')[:5] if zip_code else ''
    return f"{addr}|{city_norm}|{state_norm}|{zip_norm}"

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
