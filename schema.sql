CREATE TABLE IF NOT EXISTS counties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    fips TEXT,
    UNIQUE(name, state)
);

CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    city TEXT,
    state TEXT,
    zip TEXT,
    county_id INTEGER REFERENCES counties(id),
    lat REAL,
    lng REAL,
    parcel_id TEXT,
    normalized_address TEXT,
    UNIQUE(normalized_address)
);

CREATE TABLE IF NOT EXISTS tax_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties(id),
    tax_year INTEGER,
    mkt_val_total REAL,
    mkt_val_land REAL,
    mkt_val_building REAL,
    sale_price REAL,
    sale_date TEXT,
    year_built INTEGER,
    sqft REAL,
    bedrooms INTEGER,
    bathrooms REAL,
    land_use TEXT,
    lot_sqft REAL
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER REFERENCES properties(id),
    list_price REAL NOT NULL,
    listing_status TEXT,
    listing_date TEXT,
    source TEXT,
    mls_id TEXT,
    rentcast_id TEXT,
    url TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_properties_addr ON properties(normalized_address);
CREATE INDEX IF NOT EXISTS idx_tax_property ON tax_records(property_id);
CREATE INDEX IF NOT EXISTS idx_listings_property ON listings(property_id);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(list_price);
