import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'deals.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn

def init_db():
    conn = get_conn()
    schema = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

def upsert_county(name, state, fips=None):
    conn = get_conn()
    cur = conn.execute(
        "INSERT OR IGNORE INTO counties (name, state, fips) VALUES (?, ?, ?)",
        (name, state, fips)
    )
    conn.commit()
    row = conn.execute("SELECT id FROM counties WHERE name=? AND state=?", (name, state)).fetchone()
    conn.close()
    return row['id'] if row else None

def upsert_property(address, city, state, zip_code, county_id, lat=None, lng=None, parcel_id=None):
    norm = normalize_address(address, city, state, zip_code)
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM properties WHERE normalized_address=?", (norm,)
    ).fetchone()
    if existing:
        conn.close()
        return existing['id']
    conn.execute(
        """INSERT INTO properties (address, city, state, zip, county_id, lat, lng, parcel_id, normalized_address)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (address, city, state, zip_code, county_id, lat, lng, parcel_id, norm)
    )
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return pid

def upsert_tax_record(property_id, tax_year, mkt_val_total=None, mkt_val_land=None,
                       mkt_val_building=None, sale_price=None, sale_date=None,
                       year_built=None, sqft=None, bedrooms=None, bathrooms=None,
                       land_use=None, lot_sqft=None):
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO tax_records
           (property_id, tax_year, mkt_val_total, mkt_val_land, mkt_val_building,
            sale_price, sale_date, year_built, sqft, bedrooms, bathrooms, land_use, lot_sqft)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (property_id, tax_year, mkt_val_total, mkt_val_land, mkt_val_building,
         sale_price, sale_date, year_built, sqft, bedrooms, bathrooms, land_use, lot_sqft)
    )
    conn.commit()
    conn.close()

def upsert_listing(property_id, list_price, listing_status=None, listing_date=None,
                    source=None, mls_id=None, rentcast_id=None, url=None):
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO listings
           (property_id, list_price, listing_status, listing_date, source, mls_id, rentcast_id, url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (property_id, list_price, listing_status, listing_date, source, mls_id, rentcast_id, url)
    )
    conn.commit()
    conn.close()

def find_deals(diff_pct=15):
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            p.address, p.city, p.state, p.zip,
            c.name as county,
            t.mkt_val_total as tax_value,
            t.mkt_val_land as land_value,
            t.mkt_val_building as building_value,
            l.list_price,
            ROUND((l.list_price - t.mkt_val_total) * 100.0 / t.mkt_val_total, 2) as diff_pct,
            t.year_built, t.sqft, t.bedrooms, t.bathrooms,
            t.sale_price as last_sale_price,
            t.sale_date as last_sale_date,
            l.url as listing_url,
            l.source as listing_source
        FROM properties p
        JOIN tax_records t ON p.id = t.property_id
        JOIN listings l ON p.id = l.property_id
        JOIN counties c ON p.county_id = c.id
        WHERE t.mkt_val_total > 0
          AND l.list_price BETWEEN t.mkt_val_total * (1 - ?)
                               AND t.mkt_val_total * (1 + ?)
        ORDER BY diff_pct ASC
    """, (diff_pct / 100.0, diff_pct / 100.0))
    results = [dict(r) for r in rows.fetchall()]
    conn.close()
    return results

def normalize_address(address, city, state, zip_code):
    import re
    addr = address.strip().upper()
    addr = re.sub(r'\b(STREET|DRIVE|AVENUE|ROAD|LANE|COURT|CIRCLE|BOULEVARD|HIGHWAY|PLACE|WAY|PARKWAY|SQUARE|TERRACE|TRACE|VIEW|RIDGE|WALK|RUN|DR|ST|AVE|RD|LN|CT|CIR|BLVD|HWY|PL|PKWY|SQ|TER|TR|VW)\b\.?', '', addr)
    addr = re.sub(r'\b(NORTH|SOUTH|EAST|WEST|NO|SO|EA|WE|N|S|E|W)\b\.?', '', addr)
    addr = re.sub(r'\b(SUITE|UNIT|APT|#)\s*\w*\b', '', addr)
    addr = re.sub(r'[^A-Z0-9\s]', '', addr)
    addr = re.sub(r'\s+', ' ', addr).strip()
    return f"{addr}|{city.upper()}|{state.upper()}|{zip_code[:5]}" if zip_code else f"{addr}|{city.upper()}|{state.upper()}"

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
