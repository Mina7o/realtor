import sys, os, datetime
# sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from db import get_conn, upsert_property, upsert_tax_record
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)


@app.after_request
def no_cache(response):
    if 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


RENTCAST_API_KEY = "1e1071e57f9a422b95a3f064822c3b4a"
RENTCAST_BASE = "https://api.rentcast.io/v1"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/charts')
def charts_page():
    return render_template('charts.html')

def get_deal_tier(diff_pct):
    if diff_pct is None:
        return None
    if diff_pct < 0:
        return "undervalued"
    if diff_pct <= 5:
        return "at_value"
    if diff_pct <= 20:
        return "premium"
    return "overpriced"


COALESCE_MV = "COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate)"


def build_listing_filters(args):
    clauses = ["l.list_price IS NOT NULL"]
    params = {}

    source = args.get('source', '').strip()
    if source in ('zillow', 'fsbo', 'rentcast'):
        clauses.append("l.source = :source")
        params['source'] = source

    price_min = args.get('price_min', type=float)
    price_max = args.get('price_max', type=float)
    if price_min is not None:
        clauses.append("l.list_price >= :pmin")
        params['pmin'] = price_min
    if price_max is not None:
        clauses.append("l.list_price <= :pmax")
        params['pmax'] = price_max

    search_q = args.get('q', '').strip()
    if search_q:
        clauses.append("(p.address LIKE :q1 OR p.city LIKE :q2)")
        params['q1'] = f"%{search_q}%"
        params['q2'] = f"%{search_q}%"

    owner_q = args.get('owner', '').strip()
    if owner_q:
        clauses.append("(a.owner_name LIKE :owner1 OR mp.full_owner_name LIKE :owner2 OR up.curr_name1 LIKE :owner3)")
        params['owner1'] = f"%{owner_q}%"
        params['owner2'] = f"%{owner_q}%"
        params['owner3'] = f"%{owner_q}%"

    city_q = args.get('city', '').strip()
    if city_q:
        clauses.append("p.city LIKE :city_q")
        params['city_q'] = f"%{city_q}%"

    min_acres = args.get('min_acres', type=float)
    if min_acres:
        clauses.append("(pd.lot_sqft >= :acres_sqft OR mp.num_totalac >= :acres_mp OR a.lot_acres >= :acres_attom)")
        params['acres_sqft'] = min_acres * 43560
        params['acres_mp'] = min_acres
        params['acres_attom'] = min_acres

    min_sqft = args.get('min_sqft', type=float)
    if min_sqft:
        clauses.append("(pd.sqft >= :min_sqft OR t.sqft >= :min_sqft OR a.sqft >= :min_sqft2)")
        params['min_sqft'] = min_sqft
        params['min_sqft2'] = min_sqft

    has_val = args.get('has_val', '').strip()
    if has_val == 'yes':
        clauses.append(f"{COALESCE_MV} IS NOT NULL AND {COALESCE_MV} > 0")
    elif has_val == 'no':
        clauses.append(f"({COALESCE_MV} IS NULL OR {COALESCE_MV} <= 0)")

    tier = args.get('tier', '').strip()
    if tier == 'undervalued':
        clauses.append(f"l.list_price < {COALESCE_MV}")
    elif tier == 'at_value':
        clauses.append(f"l.list_price >= {COALESCE_MV}")
        clauses.append(f"l.list_price <= {COALESCE_MV} * 1.05")
    elif tier == 'premium':
        clauses.append(f"l.list_price > {COALESCE_MV} * 1.05")
        clauses.append(f"l.list_price <= {COALESCE_MV} * 1.20")
    elif tier == 'overpriced':
        clauses.append(f"l.list_price > {COALESCE_MV} * 1.20")

    absentee = args.get('absentee', '').strip()
    if absentee == 'yes':
        clauses.append("""
            (a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)')
             OR (mp.txt_mailaddr1 IS NOT NULL AND (
                 UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state)
                 OR mp.txt_mailaddr1 LIKE 'PO BOX%'
                 OR mp.txt_mailaddr1 LIKE 'P O BOX%'
                 OR mp.txt_mailaddr1 LIKE 'P.O.%'
             ))
             OR (up.curr_city IS NOT NULL AND (
                 UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state)
             )))
        """)
    elif absentee == 'no':
        clauses.append("""
            NOT (a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)')
             OR (mp.txt_mailaddr1 IS NOT NULL AND (
                 UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state)
                 OR mp.txt_mailaddr1 LIKE 'PO BOX%'
                 OR mp.txt_mailaddr1 LIKE 'P O BOX%'
                 OR mp.txt_mailaddr1 LIKE 'P.O.%'
             ))
             OR (up.curr_city IS NOT NULL AND (
                 UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state)
             )))
        """)

    return clauses, params


@app.route('/api/listings')
def get_listings():
    conn = get_conn()
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    where_clauses, params = build_listing_filters(request.args)
    params["lim"] = min(limit, 500)
    params["off"] = offset
    where_sql = " AND ".join(where_clauses)

    value_sql = """
        COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, yp.apr_tot_val, a.assessed_value, l.zestimate) as market_value,
        CASE
            WHEN t.mkt_val_total > 0 THEN 'tax'
            WHEN mp.amt_totalvalue > 0 THEN 'county'
            WHEN up.fmv_total > 0 THEN 'union'
            WHEN yp.apr_tot_val > 0 THEN 'york'
            WHEN a.assessed_value > 0 THEN 'attom'
            WHEN l.zestimate > 0 THEN 'zest'
            ELSE NULL
        END as value_source,
        CASE
            WHEN a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)') THEN 1
            WHEN mp.txt_mailaddr1 IS NOT NULL AND (
                (UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state))
                OR mp.txt_mailaddr1 LIKE 'PO BOX%'
                OR mp.txt_mailaddr1 LIKE 'P O BOX%'
                OR mp.txt_mailaddr1 LIKE 'P.O.%'
            ) THEN 1
            WHEN up.curr_city IS NOT NULL AND (
                UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state)
            ) THEN 1
            WHEN yp.mail_city IS NOT NULL AND (
                UPPER(yp.mail_city) != UPPER(p.city) OR UPPER(yp.mail_state) != UPPER(p.state)
            ) THEN 1
            ELSE 0
        END as is_absentee
    """

    owner_q = request.args.get('owner', '').strip()
    if owner_q:
        where_clauses.append("""
            (a.owner_name LIKE :owner1 OR mp.full_owner_name LIKE :owner2
             OR up.curr_name1 LIKE :owner3 OR yp.owner1 LIKE :owner4)
        """)
        params['owner1'] = f"%{owner_q}%"
        params['owner2'] = f"%{owner_q}%"
        params['owner3'] = f"%{owner_q}%"
        params['owner4'] = f"%{owner_q}%"

    city_q = request.args.get('city', '').strip()
    if city_q:
        where_clauses.append("p.city LIKE :city_q")
        params['city_q'] = f"%{city_q}%"

    min_acres = request.args.get('min_acres', type=float)
    if min_acres:
        where_clauses.append("""
            (pd.lot_sqft >= :acres_sqft OR mp.num_totalac >= :acres_mp OR a.lot_acres >= :acres_attom
             OR yp.gis_acres >= :acres_york)
        """)
        params['acres_sqft'] = min_acres * 43560
        params['acres_mp'] = min_acres
        params['acres_attom'] = min_acres
        params['acres_york'] = min_acres

    min_sqft = request.args.get('min_sqft', type=float)
    if min_sqft:
        where_clauses.append("""
            (pd.sqft >= :min_sqft OR t.sqft >= :min_sqft OR a.sqft >= :min_sqft2 OR yp.finished_sqft >= :min_sqft3)
        """)
        params['min_sqft'] = min_sqft
        params['min_sqft2'] = min_sqft
        params['min_sqft3'] = min_sqft

    where_sql = " AND ".join(where_clauses)

    count_row = conn.execute(f"""
        SELECT COUNT(*) as cnt FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN property_details pd ON p.id = pd.property_id
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN tax_records t ON p.id = t.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
        LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
        WHERE {where_sql}
    """, params).fetchone()

    rows = conn.execute(f"""
        SELECT
            p.id as property_id,
            p.address, p.city, p.state, p.zip,
            p.lat, p.lng,
            l.list_price, l.listing_status, l.listing_date,
            l.source, l.url,
            l.id as listing_id,
            l.zestimate, l.img_url, l.broker_name, l.status_text,
            {value_sql},
            t.mkt_val_total as tax_value,
            t.mkt_val_building as tax_building,
            t.mkt_val_land as tax_land,
            t.year_built, t.sqft, t.bedrooms, t.bathrooms,
            t.sale_price as last_sale_price,
            t.sale_date as last_sale_date,
            c.name as county,
            a.avm_value, a.avm_high, a.avm_low, a.avm_confidence,
            a.assessed_value, a.market_value as attom_market_value,
            a.owner_name, a.absentee_status as attom_absentee_status, a.corporate_indicator,
            a.property_type as attom_prop_type,
            a.quality, a.condition,
            a.lot_acres,
            cm.match_score as county_match_score,
            mp.full_owner_name as county_owner,
            mp.amt_totalvalue as county_assessed_value,
            mp.amt_landvalue as county_land_value,
            mp.amt_netbldgvalue as county_building_value,
            mp.amt_price as county_last_sale_price,
            mp.dte_dateofsale as county_last_sale_date,
            mp.txt_propertyuse_desc as county_property_use,
            mp.num_totalac as county_acres,
            mp.pid as county_pid,
            mp.txt_mailaddr1 as county_mail_addr,
            mp.txt_city as county_mail_city,
            mp.txt_state as county_mail_state,
            mp.txt_zipcode as county_mail_zip,
            up.curr_name1 as union_owner,
            up.fmv_total as union_fmv,
            up.fmv_land as union_fmv_land,
            up.fmv_imprv as union_fmv_building,
            up.mapped_acres as union_acres,
            up.yearblt as union_year_built,
            up.sqft as union_sqft,
            up.s1_salesamt as union_last_sale_price,
            up.s1_saledate as union_last_sale_date,
            up.curr_city as union_mail_city,
            up.curr_state as union_mail_state,
            up.curr_zipcode as union_mail_zip,
            up.s1_deedtype as union_deed_type,
            yp.owner1 as york_owner,
            yp.apr_tot_val as york_fmv,
            yp.apr_land_val as york_fmv_land,
            yp.apr_bldg_val as york_fmv_building,
            yp.gis_acres as york_acres,
            yp.deeded_acres as york_deeded_acres,
            yp.year_built as york_year_built,
            yp.finished_sqft as york_sqft,
            yp.sale_price as york_last_sale_price,
            yp.date_sold as york_last_sale_date,
            yp.mail_city as york_mail_city,
            yp.mail_state as york_mail_state,
            yp.mail_zip as york_mail_zip,
            yp.subdivision as york_subdivision,
            yp.bldg_type_desc as york_bldg_type,
            yp.land_use_desc as york_land_use,
            yp.homestead as york_homestead,
            pd.bedrooms as prop_bedrooms,
            pd.bathrooms as prop_bathrooms,
            pd.sqft as prop_sqft,
            pd.year_built as prop_year_built,
            pd.property_type as prop_type,
            pd.lot_sqft as prop_lot_sqft,
            ph_old.id as ph_id,
            ph_old.old_price as ph_old_price,
            ph_old.new_price as ph_new_price,
            ph_old.detected_at as ph_detected_at
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN property_details pd ON p.id = pd.property_id
        LEFT JOIN tax_records t ON p.id = t.property_id
        LEFT JOIN counties c ON p.county_id = c.id
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
        LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
        LEFT JOIN price_history ph_old ON l.id = ph_old.listing_id
            AND ph_old.detected_at = (
                SELECT MAX(detected_at) FROM price_history
                WHERE listing_id = l.id AND new_price < old_price
            )
        WHERE {where_sql}
        ORDER BY l.list_price ASC
        LIMIT :lim OFFSET :off
    """, params).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        if d['market_value'] and d['market_value'] > 0 and d['list_price']:
            d['diff_pct'] = round((d['list_price'] - d['market_value']) * 100.0 / d['market_value'], 2)
            d['deal_tier'] = get_deal_tier(d['diff_pct'])
        else:
            d['diff_pct'] = None
            d['deal_tier'] = None
        if d['avm_value'] and d['avm_value'] > 0 and d['list_price']:
            d['avm_diff_pct'] = round((d['list_price'] - d['avm_value']) * 100.0 / d['avm_value'], 2)
        else:
            d['avm_diff_pct'] = None
        d['is_new'] = bool(d['listing_date'] and (
            datetime.datetime.now() - datetime.datetime.strptime(d['listing_date'][:10], '%Y-%m-%d')
        ).days <= 14)
        d['price_drop'] = bool(d['ph_id'])
        results.append(d)

    return jsonify({
        "listings": results,
        "total": count_row["cnt"],
        "filtered": len(results),
        "limit": limit,
        "offset": offset
    })

@app.route('/api/price-history/<int:listing_id>')
def get_price_history(listing_id):
    conn = get_conn()
    rows = conn.execute("""
        SELECT old_price, new_price, detected_at
        FROM price_history
        WHERE listing_id = ? AND new_price < old_price
        ORDER BY detected_at DESC
        LIMIT 20
    """, (listing_id,)).fetchall()
    return jsonify([{
        "old_price": int(r["old_price"] or 0),
        "new_price": int(r["new_price"] or 0),
        "detected_at": str(r["detected_at"] or "")[:10]
    } for r in rows])


@app.route('/api/stats')
def get_stats():
    conn = get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_listings,
            ROUND(AVG(best_price), 0) as avg_price,
            ROUND(MIN(best_price), 0) as min_price,
            ROUND(MAX(best_price), 0) as max_price,
            SUM(CASE WHEN market_value > 0 THEN 1 ELSE 0 END) as with_mkt_value,
            SUM(absentee) as absentee_owners,
            SUM(CASE WHEN best_price < market_value THEN 1 ELSE 0 END) as undervalued,
            SUM(CASE WHEN best_price >= market_value AND best_price <= market_value * 1.05 THEN 1 ELSE 0 END) as at_value,
            SUM(CASE WHEN best_price > market_value * 1.05 AND best_price <= market_value * 1.20 THEN 1 ELSE 0 END) as premium,
            SUM(CASE WHEN best_price > market_value * 1.20 THEN 1 ELSE 0 END) as overpriced
        FROM (
            SELECT
                p.id,
                MIN(l.list_price) as best_price,
                COALESCE(MAX(t.mkt_val_total), MAX(mp.amt_totalvalue), MAX(up.fmv_total), MAX(yp.apr_tot_val), MAX(a.assessed_value), MAX(l.zestimate)) as market_value,
                MAX(CASE
                    WHEN a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)') THEN 1
                    WHEN mp.txt_mailaddr1 IS NOT NULL AND (
                        UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state)
                    ) THEN 1
                    WHEN up.curr_city IS NOT NULL AND (
                        UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state)
                    ) THEN 1
                    WHEN yp.mail_city IS NOT NULL AND (
                        UPPER(yp.mail_city) != UPPER(p.city) OR UPPER(yp.mail_state) != UPPER(p.state)
                    ) THEN 1
                    ELSE 0
                END) as absentee
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            LEFT JOIN tax_records t ON p.id = t.property_id AND t.mkt_val_total > 0
            LEFT JOIN attom_cache a ON p.id = a.property_id
            LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
            LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
            LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
            LEFT JOIN union_parcels up ON ucm.pid = up.pid
            LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
            LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
            GROUP BY p.id
        ) deduped
        WHERE market_value > 0
    """).fetchone()
    return jsonify(dict(stats))

@app.route('/api/charts')
def get_charts():
    conn = get_conn()

    deals = conn.execute("""
        SELECT p.address, p.city, MIN(l.list_price) as list_price,
               MAX(COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate)) as market_value,
               ROUND((MIN(l.list_price) - MAX(COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate))) / NULLIF(MAX(COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate)), 0) * 100, 1) as diff_pct,
               MAX(COALESCE(a.lot_acres, mp.num_totalac, up.mapped_acres)) as acres,
               MIN(l.url) as url,
               MIN(l.broker_name) as broker_name,
               MIN(l.listing_date) as listing_date,
               MAX(CASE WHEN l.source = 'zillow' THEN l.zestimate END) as zestimate
        FROM properties p
        JOIN listings l ON p.id = l.property_id
        LEFT JOIN tax_records t ON p.id = t.property_id AND t.mkt_val_total > 0
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        WHERE l.list_price IS NOT NULL
          AND COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate) IS NOT NULL
          AND COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate) > 0
          AND l.list_price BETWEEN 10000 AND 10000000
          AND COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate) < 5000000
        GROUP BY p.id
    """).fetchall()

    price_drops = conn.execute("""
        SELECT p.address, p.city, MIN(l.list_price) as list_price,
               ph.old_price, ph.new_price, ph.detected_at,
               l.url, l.broker_name,
               COALESCE(a.owner_name, mp.full_owner_name, up.curr_name1, yp.owner1) as owner_name,
               (SELECT COUNT(*) FROM price_history WHERE listing_id = l.id) as total_changes,
               (SELECT COUNT(*) FROM price_history WHERE listing_id = l.id AND new_price < old_price) as total_drops
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        JOIN price_history ph ON l.id = ph.listing_id AND ph.new_price < ph.old_price
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
        LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
        WHERE l.list_price IS NOT NULL
        GROUP BY l.id
        ORDER BY ph.detected_at DESC
        LIMIT 20
    """).fetchall()

    city_deal_stats = conn.execute("""
        SELECT p.city,
               COUNT(*) as total,
               SUM(CASE WHEN l.list_price < COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate) THEN 1 ELSE 0 END) as undervalued,
               SUM(CASE WHEN a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)') OR (mp.txt_mailaddr1 IS NOT NULL AND (UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state))) OR (up.curr_city IS NOT NULL AND (UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state))) THEN 1 ELSE 0 END) as absentee,
               ROUND(AVG(l.list_price), 0) as avg_price
        FROM properties p
        JOIN listings l ON p.id = l.property_id
        LEFT JOIN tax_records t ON p.id = t.property_id AND t.mkt_val_total > 0
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        WHERE l.list_price IS NOT NULL
          AND COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, a.assessed_value, l.zestimate) IS NOT NULL
        GROUP BY p.city
        HAVING COUNT(*) >= 5
        ORDER BY COUNT(*) DESC
    """).fetchall()

    now = datetime.datetime.now()
    tiers = {"undervalued": 0, "at_value": 0, "premium": 0, "overpriced": 0}
    deals_list = []
    city_data_list = []
    new_count = 0
    old_count = 0

    for r in deals:
        price = r[2] or 0
        mv = r[3] or 0
        diff = r[4]
        city = r[1] or "Unknown"

        listing_date = r[8]
        if listing_date:
            try:
                age = (now - datetime.datetime.strptime(listing_date[:10], '%Y-%m-%d')).days
            except:
                age = 999
        else:
            age = 999

        if age <= 14:
            new_count += 1
        else:
            old_count += 1

        if diff is None:
            continue

        if diff < 0:
            tiers["undervalued"] += 1
        elif diff <= 5:
            tiers["at_value"] += 1
        elif diff <= 20:
            tiers["premium"] += 1
        else:
            tiers["overpriced"] += 1

        deals_list.append({
            "address": r[0], "city": city, "list_price": int(price),
            "market_value": int(mv), "diff_pct": diff,
            "acres": r[5] or 0, "url": r[6],
            "broker": r[7] or "",
            "age_days": age
        })

    undervalued = sorted([d for d in deals_list if d["diff_pct"] < 0], key=lambda x: x["diff_pct"])[:15]

    for r in city_deal_stats:
        city_data_list.append({
            "city": r[0] or "Unknown",
            "total": r[1],
            "undervalued": r[2],
            "absentee": r[3],
            "avg_price": r[4]
        })

    price_drop_list = []
    for r in price_drops:
        t = r[9] or 0
        d = r[10] or 0
        times = f"\u2195{t}/\u2193{d}" if t else ''
        price_drop_list.append({
            "address": r[0] or "",
            "city": r[1] or "",
            "list_price": int(r[2] or 0),
            "old_price": int(r[3] or 0),
            "new_price": int(r[4] or 0),
            "detected_at": str(r[5] or "")[:10],
            "url": r[6] or "",
            "owner_name": r[8] or "",
            "times": times
        })

    multi_owners = conn.execute("""
        SELECT owner, COUNT(*) as cnt, GROUP_CONCAT(address, '||') as addresses
        FROM (
            SELECT a.owner_name as owner, p.address
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            JOIN attom_cache a ON p.id = a.property_id
            WHERE a.owner_name IS NOT NULL AND a.owner_name != ''
            UNION ALL
            SELECT mp.full_owner_name, p.address
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            JOIN listing_county_match cm ON l.id = cm.listing_id
            JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
            WHERE mp.full_owner_name IS NOT NULL AND mp.full_owner_name != ''
            UNION ALL
            SELECT up.curr_name1, p.address
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            JOIN listing_county_union ucm ON l.id = ucm.listing_id
            JOIN union_parcels up ON ucm.pid = up.pid
            WHERE up.curr_name1 IS NOT NULL AND up.curr_name1 != ''
            UNION ALL
            SELECT yp.owner1, p.address
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            JOIN listing_county_york ycm ON l.id = ycm.listing_id
            JOIN york_parcels yp ON ycm.pid = yp.objectid
            WHERE yp.owner1 IS NOT NULL AND yp.owner1 != ''
        ) combined
        GROUP BY owner
        HAVING COUNT(*) >= 2
        ORDER BY COUNT(*) DESC
        LIMIT 30
    """).fetchall()

    top_brokers = conn.execute("""
        SELECT broker_name, COUNT(*) as cnt
        FROM listings
        WHERE broker_name IS NOT NULL AND broker_name != ''
        GROUP BY broker_name
        ORDER BY COUNT(*) DESC
        LIMIT 15
    """).fetchall()

    multi_owner_list = []
    for r in multi_owners:
        multi_owner_list.append({
            "owner": r[0],
            "count": r[1],
            "addresses": (r[2] or "").split("||")[:5]
        })

    broker_list = []
    for r in top_brokers:
        broker_list.append({
            "name": r[0],
            "count": r[1]
        })

    return jsonify({
        "tiers": tiers,
        "total_with_value": len(deals),
        "deals": deals_list,
        "undervalued": undervalued,
        "price_drops": price_drop_list,
        "cities": city_data_list,
        "new_count": new_count,
        "old_count": old_count,
        "multi_owners": multi_owner_list,
        "top_brokers": broker_list
    })


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
    app.run(host='0.0.0.0', port=port, debug=True)
