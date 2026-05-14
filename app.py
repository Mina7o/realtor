import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from db import get_conn, upsert_property, upsert_tax_record
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

RENTCAST_API_KEY = "1e1071e57f9a422b95a3f064822c3b4a"
RENTCAST_BASE = "https://api.rentcast.io/v1"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/listings')
def get_listings():
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            p.id as property_id,
            p.address, p.city, p.state, p.zip,
            p.lat, p.lng,
            l.list_price, l.listing_status, l.listing_date,
            l.source, l.url,
            l.id as listing_id,
            t.mkt_val_total as tax_value,
            t.mkt_val_building as tax_building,
            t.mkt_val_land as tax_land,
            t.year_built, t.sqft, t.bedrooms, t.bathrooms,
            t.sale_price as last_sale_price,
            t.sale_date as last_sale_date,
            c.name as county
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN tax_records t ON p.id = t.property_id
        LEFT JOIN counties c ON p.county_id = c.id
        WHERE l.list_price IS NOT NULL
        ORDER BY l.list_price ASC
    """).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        if d['tax_value'] and d['tax_value'] > 0 and d['list_price']:
            d['diff_pct'] = round((d['list_price'] - d['tax_value']) * 100.0 / d['tax_value'], 2)
        else:
            d['diff_pct'] = None
        results.append(d)
    return jsonify(results)

@app.route('/api/stats')
def get_stats():
    conn = get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_listings,
            ROUND(AVG(l.list_price), 0) as avg_price,
            ROUND(MIN(l.list_price), 0) as min_price,
            ROUND(MAX(l.list_price), 0) as max_price,
            COUNT(t.mkt_val_total) as with_tax_data,
            COUNT(CASE WHEN t.mkt_val_total > 0 AND l.list_price > 0
                  AND ABS(l.list_price - t.mkt_val_total) * 100.0 / t.mkt_val_total <= 15
                  THEN 1 END) as deals_15pct
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN tax_records t ON p.id = t.property_id
    """).fetchone()
    return jsonify(dict(stats))

@app.route('/api/enrich-tax', methods=['POST'])
def enrich_tax():
    """Enrich a listing with tax data from RentCast property endpoint."""
    data = request.json
    address = data.get('address', '')
    city = data.get('city', 'Charlotte')
    state = data.get('state', 'NC')
    property_id = data.get('property_id')

    if not address:
        return jsonify({'error': 'Address required'}), 400

    url = f"{RENTCAST_BASE}/properties"
    params = {'address': address, 'city': city, 'state': state}
    headers = {'X-Api-Key': RENTCAST_API_KEY}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return jsonify({'error': f'API error: {resp.status_code}', 'detail': resp.text[:200]}), 500
        data = resp.json()
        if not data:
            return jsonify({'error': 'No property found'}), 404
        prop = data[0]

        tax = prop.get('taxAssessments', {})
        latest_year = max(tax.keys()) if tax else None
        tax_data = tax.get(latest_year, {}) if latest_year else {}

        result = {
            'bedrooms': prop.get('bedrooms'),
            'bathrooms': prop.get('bathrooms'),
            'squareFootage': prop.get('squareFootage'),
            'yearBuilt': prop.get('yearBuilt'),
            'lastSalePrice': prop.get('lastSalePrice'),
            'lastSaleDate': prop.get('lastSaleDate'),
            'taxYear': latest_year,
            'taxValue': tax_data.get('value'),
            'taxLand': tax_data.get('land'),
            'taxImprovements': tax_data.get('improvements'),
        }

        if property_id:
            upsert_tax_record(
                property_id=property_id,
                tax_year=int(latest_year) if latest_year else None,
                mkt_val_total=tax_data.get('value'),
                mkt_val_land=tax_data.get('land'),
                mkt_val_building=tax_data.get('improvements'),
                sale_price=prop.get('lastSalePrice'),
                sale_date=prop.get('lastSaleDate'),
                year_built=prop.get('yearBuilt'),
                sqft=prop.get('squareFootage'),
                bedrooms=prop.get('bedrooms'),
                bathrooms=prop.get('bathrooms'),
            )

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/enrich-batch', methods=['POST'])
def enrich_batch():
    """Enrich multiple listings with tax data (max 45 per run due to free tier)."""
    conn = get_conn()
    listings = conn.execute("""
        SELECT p.id, p.address, p.city, p.state
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN tax_records t ON p.id = t.property_id
        WHERE t.id IS NULL
        LIMIT 45
    """).fetchall()

    results = []
    for l in listings:
        url = f"{RENTCAST_BASE}/properties"
        params = {'address': l['address'], 'city': l['city'], 'state': l['state']}
        headers = {'X-Api-Key': RENTCAST_API_KEY}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data:
                continue
            prop = data[0]
            tax = prop.get('taxAssessments', {})
            latest_year = max(tax.keys()) if tax else None
            td = tax.get(latest_year, {}) if latest_year else {}
            upsert_tax_record(
                property_id=l['id'],
                tax_year=int(latest_year) if latest_year else None,
                mkt_val_total=td.get('value'),
                mkt_val_land=td.get('land'),
                mkt_val_building=td.get('improvements'),
                sale_price=prop.get('lastSalePrice'),
                sale_date=prop.get('lastSaleDate'),
                year_built=prop.get('yearBuilt'),
                sqft=prop.get('squareFootage'),
                bedrooms=prop.get('bedrooms'),
                bathrooms=prop.get('bathrooms'),
            )
            results.append({'address': l['address'], 'status': 'ok'})
        except Exception as e:
            results.append({'address': l['address'], 'status': 'error', 'error': str(e)})
    return jsonify({'enriched': len(results), 'results': results})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
