import sqlite3, json, sys, math
sys.path.insert(0, '/home/euclid/Documents/proj/realtor/venv/lib/python3.12/site-packages')
import pymongo
from shapely.geometry import shape, mapping
from shapely.validation import make_valid
from pathlib import Path

mongo = pymongo.MongoClient('mongodb://localhost:27017')
db = mongo['realtor_sovereign']

DEALS_DB = str(Path(__file__).parent.parent / 'deals.db')
INFRA_DB = str(Path(__file__).parent.parent / 'infrastructure.db')

def is_valid_lng(v):
    return v is not None and -180 <= float(v) <= 180

def is_valid_lat(v):
    return v is not None and -90 <= float(v) <= 90

def validate_geometry(gj_str):
    try:
        geom = json.loads(gj_str)
        s = shape(geom)
        if not s.is_valid:
            s = make_valid(s)
        valid_geom = mapping(s)
        return valid_geom
    except Exception:
        return None

def migrate_commercial_sites():
    coll = db['commercial_sites']
    coll.drop()
    cur = sqlite.execute('SELECT * FROM commercial_sites')
    batch = []
    invalid = 0
    for row in cur:
        d = dict(row)
        d.pop('id', None)
        lat, lng = d.pop('lat', None), d.pop('lng', None)
        if lat is not None and lng is not None and is_valid_lat(lat) and is_valid_lng(lng):
            d['location'] = {'type': 'Point', 'coordinates': [float(lng), float(lat)]}
        else:
            invalid += 1
        batch.append(d)
        if len(batch) >= 1000:
            coll.insert_many(batch, ordered=False)
            batch = []
    if batch:
        coll.insert_many(batch, ordered=False)
    total = coll.count_documents({})
    print(f'commercial_sites: {total} docs inserted ({invalid} without valid coordinates)')

def migrate_tx_stratmap_parcels():
    coll = db['tx_stratmap_parcels']
    coll.drop()
    cur = sqlite.execute('SELECT * FROM raw_tx_stratmap_parcels')
    batch = []
    invalid = 0
    for row in cur:
        d = dict(row)
        d.pop('id', None)
        gj = d.pop('geometry_geojson', None)
        if gj:
            valid_geom = validate_geometry(gj)
            if valid_geom:
                d['geometry'] = valid_geom
            else:
                invalid += 1
                lat, lng = d.get('lat'), d.get('lng')
                if lat and lng and is_valid_lat(lat) and is_valid_lng(lng):
                    d['location'] = {'type': 'Point', 'coordinates': [float(lng), float(lat)]}
        else:
            lat, lng = d.get('lat'), d.get('lng')
            if lat and lng and is_valid_lat(lat) and is_valid_lng(lng):
                d['location'] = {'type': 'Point', 'coordinates': [float(lng), float(lat)]}
        batch.append(d)
        if len(batch) >= 1000:
            coll.insert_many(batch, ordered=False)
            batch = []
    if batch:
        coll.insert_many(batch, ordered=False)
    print(f'tx_stratmap_parcels: {coll.count_documents({})} docs inserted ({invalid} repaired)')

def migrate_transmission_lines():
    coll = db['transmission_lines']
    coll.drop()
    infra = sqlite3.connect(INFRA_DB)
    infra.row_factory = sqlite3.Row
    cur = infra.execute('SELECT * FROM transmission_lines WHERE voltage >= 345')
    batch = []
    for row in cur:
        d = dict(row)
        d.pop('id', None)
        gj = d.pop('geometry_geojson', None)
        if gj:
            try:
                geom = json.loads(gj)
                d['geometry'] = geom
            except json.JSONDecodeError:
                pass
        batch.append(d)
        if len(batch) >= 1000:
            coll.insert_many(batch, ordered=False)
            batch = []
    if batch:
        coll.insert_many(batch, ordered=False)
    print(f'transmission_lines: {coll.count_documents({})} docs inserted')
    infra.close()

def create_indexes():
    db['commercial_sites'].create_index([('location', '2dsphere')])
    db['tx_stratmap_parcels'].create_index([('geometry', '2dsphere')])
    db['transmission_lines'].create_index([('geometry', '2dsphere')])
    db['commercial_sites'].create_index([('county', 1)])
    db['commercial_sites'].create_index([('score_tier', 1)])
    db['tx_stratmap_parcels'].create_index([('owner_name', 1)])
    db['transmission_lines'].create_index([('voltage', 1)])
    print('Indexes created')

if __name__ == '__main__':
    sqlite = sqlite3.connect(DEALS_DB)
    sqlite.row_factory = sqlite3.Row
    migrate_commercial_sites()
    migrate_tx_stratmap_parcels()
    migrate_transmission_lines()
    create_indexes()
    mongo.close()
    sqlite.close()
    print('Migration complete')
