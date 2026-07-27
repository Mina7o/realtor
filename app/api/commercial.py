import json
import sqlite3
from pathlib import Path
from flask import Blueprint, jsonify, request
from db import get_conn
from mongo_db import get_mongo
from app.api.common import PROJECT_ROOT

commercial_bp = Blueprint("commercial", __name__)


@commercial_bp.route("/api/commercial-sites")
def get_commercial_sites():
    conn = get_conn()
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    tier = request.args.get("tier", "").strip()
    county = request.args.get("county", "").strip()
    min_acres = request.args.get("min_acres", type=float)
    min_score = request.args.get("min_score", type=int)
    max_sub_dist = request.args.get("max_sub_dist", type=float)
    owner_q = request.args.get("owner", "").strip()
    sort = request.args.get("sort", "").strip()

    clauses = ["1=1"]
    params = {}
    if max_sub_dist:
        clauses.append("substation_distance_miles IS NOT NULL AND substation_distance_miles <= :max_sub")
        params["max_sub"] = max_sub_dist
    if tier:
        tiers = [t.strip() for t in tier.split(",") if t.strip()]
        placeholders = [f":tier{i}" for i in range(len(tiers))]
        clauses.append(f"score_tier IN ({','.join(placeholders)})")
        for i, t in enumerate(tiers):
            params[f"tier{i}"] = t
    if county:
        clauses.append("county = :county")
        params["county"] = county
    if min_acres:
        clauses.append("CAST(acres AS REAL) >= :min_ac")
        params["min_ac"] = min_acres
    if min_score:
        clauses.append("CAST(score_total AS INTEGER) >= :min_sc")
        params["min_sc"] = min_score
    if owner_q:
        clauses.append("owner_name LIKE :owner")
        params["owner"] = f"%{owner_q}%"

    where = " AND ".join(clauses)
    params["lim"] = min(limit, 500)
    params["off"] = offset

    count = conn.execute(f"SELECT COUNT(*) FROM commercial_sites WHERE {where}", params).fetchone()[0]
    order = "CAST(score_total AS INTEGER) DESC, CAST(acres AS REAL) DESC"
    if sort == "acres":
        order = "CAST(acres AS REAL) DESC"
    elif sort == "acres_asc":
        order = "CAST(acres AS REAL) ASC"
    elif sort == "score_total":
        order = "CAST(score_total AS INTEGER) DESC"

    rows = conn.execute(f"""
        SELECT * FROM commercial_sites
        WHERE {where}
        ORDER BY {order}
        LIMIT :lim OFFSET :off
    """, params).fetchall()

    cols = [c[1] for c in conn.execute("PRAGMA table_info(commercial_sites)").fetchall()]
    sites = [dict(zip(cols, r)) for r in rows]
    conn.close()

    for site in sites:
        for k, v in list(site.items()):
            if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
                site[k] = None

    return jsonify({"sites": sites, "total": count, "limit": limit, "offset": offset})


@commercial_bp.route("/api/commercial-stats")
def get_commercial_stats():
    conn = get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            ROUND(SUM(CAST(acres AS REAL))) as total_acres,
            ROUND(AVG(CAST(acres AS REAL)), 1) as avg_acres,
            ROUND(SUM(CASE WHEN score_tier = 'A' THEN 1 ELSE 0 END)) as tier_a,
            ROUND(SUM(CASE WHEN score_tier = 'B' THEN 1 ELSE 0 END)) as tier_b,
            ROUND(SUM(CASE WHEN score_tier = 'C' THEN 1 ELSE 0 END)) as tier_c,
            ROUND(SUM(CASE WHEN score_tier = 'D' THEN 1 ELSE 0 END)) as tier_d,
            ROUND(AVG(CAST(score_total AS INTEGER)), 0) as avg_score,
            ROUND(AVG(CAST(substation_distance_miles AS REAL)), 2) as avg_sub_dist
        FROM commercial_sites
    """).fetchone()
    by_county = conn.execute("""
        SELECT county, COUNT(*) as cnt, ROUND(SUM(CAST(acres AS REAL))) as total_ac
        FROM commercial_sites
        GROUP BY county
    """).fetchall()
    by_tier = conn.execute("""
        SELECT score_tier, county, COUNT(*) as cnt
        FROM commercial_sites
        GROUP BY score_tier, county
    """).fetchall()
    a_tier_acres = conn.execute("""
        SELECT ROUND(SUM(CAST(acres AS REAL)))
        FROM commercial_sites WHERE score_tier = 'A'
    """).fetchone()[0] or 0
    ercot_risk = conn.execute("""
        SELECT ercot_risk_score, COUNT(*) as cnt, ROUND(SUM(CAST(acres AS REAL))) as total_ac
        FROM commercial_sites WHERE ercot_risk_score IS NOT NULL
        GROUP BY ercot_risk_score
        ORDER BY ercot_risk_score
    """).fetchall()
    conn.close()
    stats = dict(stats)
    est_dev_value = a_tier_acres * 1_000_000
    avg_sub = stats.get("avg_sub_dist") or 1
    power_density = round(stats["total"] / avg_sub, 1) if avg_sub > 0 else 0
    return jsonify({
        "stats": stats,
        "by_county": [dict(r) for r in by_county],
        "by_tier": [dict(r) for r in by_tier],
        "est_development_value": est_dev_value,
        "a_tier_acres": a_tier_acres,
        "power_density_score": power_density,
        "ercot_risk": [dict(r) for r in ercot_risk],
    })


@commercial_bp.route("/api/portfolio-quiet-counties")
def get_quiet_county_portfolio():
    conn = get_conn()
    quiet = conn.execute("""
        SELECT
            COUNT(*) as total,
            ROUND(SUM(CAST(acres AS REAL))) as total_acres,
            ROUND(AVG(CAST(acres AS REAL)), 1) as avg_acres,
            ROUND(SUM(CASE WHEN score_tier = 'A' THEN 1 ELSE 0 END)) as tier_a,
            ROUND(SUM(CASE WHEN score_tier = 'B' THEN 1 ELSE 0 END)) as tier_b,
            ROUND(SUM(CASE WHEN score_tier = 'C' THEN 1 ELSE 0 END)) as tier_c,
            ROUND(SUM(CASE WHEN score_tier = 'D' THEN 1 ELSE 0 END)) as tier_d,
            ROUND(AVG(CAST(score_total AS INTEGER)), 0) as avg_score,
            ROUND(AVG(CAST(score_assembly AS REAL)), 1) as avg_assembly,
            ROUND(SUM(CASE WHEN CAST(score_assembly AS INTEGER) >= 20 THEN 1 ELSE 0 END)) as assembly_ready_parcels,
            ROUND(SUM(CASE WHEN CAST(score_assembly AS INTEGER) >= 20 THEN CAST(acres AS REAL) ELSE 0 END)) as assembly_ready_acres
        FROM commercial_sites
        WHERE LOWER(county) IN ('hood', 'grayson', 'navarro')
    """).fetchone()
    by_county = conn.execute("""
        SELECT county, COUNT(*) as cnt, ROUND(SUM(CAST(acres AS REAL))) as total_ac,
               ROUND(AVG(CAST(score_total AS INTEGER)), 0) as avg_score,
               ROUND(AVG(CAST(score_assembly AS REAL)), 1) as avg_assembly
        FROM commercial_sites
        WHERE LOWER(county) IN ('hood', 'grayson', 'navarro')
        GROUP BY county
    """).fetchall()
    conn.close()

    q = dict(quiet)
    assembly_ac = float(q.get("assembly_ready_acres", 0) or 0)
    total_ac = float(q.get("total_acres", 0) or 0)
    est_dev_value = assembly_ac * 500_000 + max(0, total_ac - assembly_ac) * 250_000

    return jsonify({
        "portfolio": q,
        "by_county": [dict(r) for r in by_county],
        "est_development_value": est_dev_value,
        "institutional_narrative": {
            "cluster": "North Texas Frontier (Hood/Grayson/Navarro)",
            "status": "Institutional-Ready",
            "strategy": "Zero 345kV congestion → greenfield microgrid or ERCOT nodal build-to-suit",
            "acres": total_ac,
            "total_value_usd": est_dev_value,
            "value_per_acre": round(est_dev_value / total_ac, 2) if total_ac > 0 else 0,
        },
    })


@commercial_bp.route("/api/transmission-lines")
def get_transmission_lines():
    min_volt = request.args.get("min_volt", 345, type=float)
    state = request.args.get("state", "").strip().upper()

    infra = sqlite3.connect(str(PROJECT_ROOT / "infrastructure.db"))
    infra.row_factory = sqlite3.Row

    clauses = ["voltage >= :min_v AND voltage > 0"]
    params = {"min_v": min_volt}

    if state == "TX":
        clauses.append("state = :state")
        params["state"] = "TX"
    elif state:
        clauses.append("state = :state")
        params["state"] = state

    where = " AND ".join(clauses)
    lines = infra.execute(
        f"SELECT voltage, owner, geometry_geojson FROM transmission_lines WHERE {where}", params
    ).fetchall()
    infra.close()

    def simplify_coords(geom, precision=3):
        t = geom["type"]
        if t == "LineString":
            coords = []
            prev = None
            for c in geom["coordinates"]:
                r = (round(c[0], precision), round(c[1], precision))
                if r != prev:
                    coords.append(list(r))
                    prev = r
            geom["coordinates"] = coords
        elif t == "MultiLineString":
            lines = []
            for line in geom["coordinates"]:
                coords = []
                prev = None
                for c in line:
                    r = (round(c[0], precision), round(c[1], precision))
                    if r != prev:
                        coords.append(list(r))
                        prev = r
                lines.append(coords)
            geom["coordinates"] = lines
        return geom

    return jsonify({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": simplify_coords(json.loads(l["geometry_geojson"])),
            "properties": {"voltage": l["voltage"], "owner": l["owner"]},
        } for l in lines],
    })


@commercial_bp.route("/api/substations")
def get_substations():
    conn = get_conn()
    infra = sqlite3.connect(str(PROJECT_ROOT / "infrastructure.db"))
    infra.row_factory = sqlite3.Row

    county = request.args.get("county", "").strip()
    state = request.args.get("state", "").strip().upper()
    min_volt = request.args.get("min_volt", type=int)

    clauses = ["1=1"]
    params = {}
    if county:
        clauses.append("LOWER(county) = :county")
        params["county"] = county.lower()
    if state:
        clauses.append("state = :state")
        params["state"] = state
    if min_volt:
        clauses.append("max_volt >= :min_v")
        params["min_v"] = min_volt

    where = " AND ".join(clauses)
    deals_rows = conn.execute(
        f"SELECT name, city, county, state, max_volt, min_volt, status, lat, lng FROM substations WHERE {where}",
        params,
    ).fetchall()

    infra_clauses = ["1=1"]
    infra_params = {}
    if state:
        infra_clauses.append("state = :state")
        infra_params["state"] = state
    if min_volt:
        infra_clauses.append("max_volt >= :min_v")
        infra_params["min_v"] = min_volt

    infra_where = " AND ".join(infra_clauses)
    infra_rows = infra.execute(
        f"SELECT name, city, county, state, max_volt, min_volt, status, latitude as lat, longitude as lng FROM substations WHERE {infra_where} LIMIT 500",
        infra_params,
    ).fetchall()

    conn.close()
    infra.close()

    combined = [dict(r) for r in deals_rows] + [dict(r) for r in infra_rows]
    return jsonify({"substations": combined})


@commercial_bp.route("/api/grid-snap")
def grid_snap():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify({"error": "lat and lng required"}), 400
    limit = min(request.args.get("limit", 5, type=int), 20)

    db = get_mongo()
    pipeline = [
        {"$geoNear": {
            "near": {"type": "Point", "coordinates": [lng, lat]},
            "distanceField": "distance_meters",
            "spherical": True,
            "key": "geometry",
        }},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "source_id": 1, "voltage": 1, "owner": 1, "status": 1,
            "distance_miles": {"$divide": ["$distance_meters", 1609.34]},
        }},
    ]
    lines = list(db["transmission_lines"].aggregate(pipeline))
    return jsonify({"lat": lat, "lng": lng, "lines": lines})
