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

CREATE TABLE IF NOT EXISTS attom_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties(id),
    attom_id INTEGER,
    apn TEXT,
    fips TEXT,
    owner_name TEXT,
    owner_mail_address TEXT,
    absentee_status TEXT,
    corporate_indicator TEXT,
    avm_value REAL,
    avm_high REAL,
    avm_low REAL,
    avm_confidence INTEGER,
    assessed_value REAL,
    market_value REAL,
    year_built INTEGER,
    beds INTEGER,
    baths REAL,
    sqft INTEGER,
    lot_acres REAL,
    property_type TEXT,
    quality TEXT,
    condition TEXT,
    raw_json TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(property_id)
);

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
);
CREATE INDEX IF NOT EXISTS idx_meck_situs ON mecklenburg_parcels(situsaddress1);

CREATE TABLE IF NOT EXISTS listing_county_match (
    listing_id INTEGER PRIMARY KEY,
    property_id INTEGER,
    pid TEXT,
    situsaddress1 TEXT,
    match_score INTEGER,
    matched_at TEXT DEFAULT (datetime('now'))
);
