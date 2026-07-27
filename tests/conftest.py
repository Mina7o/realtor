import pytest
from unittest.mock import patch
from app.main import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    app.config["TRAP_HTTP_EXCEPTIONS"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def mock_db():
    """Patch get_conn to return a lightweight in-memory SQLite DB for tests."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY,
            property_id INTEGER,
            list_price REAL,
            listing_status TEXT,
            listing_date TEXT,
            source TEXT,
            mls_id TEXT,
            url TEXT,
            zestimate REAL,
            broker_name TEXT,
            img_url TEXT,
            status_text TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY,
            address TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            county_id INTEGER,
            lat REAL,
            lng REAL,
            parcel_id TEXT,
            normalized_address TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS property_details (
            id INTEGER PRIMARY KEY,
            property_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attom_cache (
            id INTEGER PRIMARY KEY,
            property_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tax_records (
            id INTEGER PRIMARY KEY,
            property_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_match (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER,
            pid TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mecklenburg_parcels (
            pid TEXT PRIMARY KEY
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_union (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER,
            pid TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS union_parcels (
            pid TEXT PRIMARY KEY
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_york (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER,
            pid TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS york_parcels (
            objectid INTEGER PRIMARY KEY
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS counties (
            id INTEGER PRIMARY KEY,
            name TEXT,
            state TEXT,
            fips TEXT,
            UNIQUE(name, state)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY,
            listing_id INTEGER,
            property_id INTEGER,
            old_price REAL,
            new_price REAL,
            source TEXT,
            detected_at TEXT
        )
    """)
    conn.commit()

    with patch("app.api.listings.get_conn", return_value=conn), \
         patch("app.api.system.get_conn", return_value=conn):
        yield conn

    conn.close()
