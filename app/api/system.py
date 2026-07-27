import json
import datetime
import sqlite3
from pathlib import Path
from flask import Blueprint, jsonify, request
from db import get_conn
from app.api.common import PROJECT_ROOT, request_count, request_latency_sum

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/pulse")
@system_bp.route("/api/health")
def api_health():
    status_path = PROJECT_ROOT / "data" / "status.json"
    if not status_path.exists():
        return jsonify({"status": "unknown", "message": "No status data yet"})
    try:
        data = json.loads(status_path.read_text())
        finished = datetime.datetime.fromisoformat(data["finished_at"])
        age = (datetime.datetime.now() - finished).total_seconds()
        healthy = data["success"] and age < 86400 * 7
        return jsonify({
            "status": "ok" if healthy else "warning",
            "job": data["job"],
            "errors": data["errors"],
            "success": data["success"],
            "finished_at": data["finished_at"],
            "age_hours": round(age / 3600, 1),
            "stale": age > 86400 * 2,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@system_bp.route("/api/cities")
def api_cities():
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT UPPER(TRIM(p.city)) as city, p.state
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        WHERE p.city IS NOT NULL AND p.city != ''
        ORDER BY p.state, city
    """).fetchall()
    conn.close()

    seen = set()
    clean = []
    for r in rows:
        c = r["city"].title().strip()
        s = r["state"].strip()
        key = (c, s)
        if key not in seen:
            seen.add(key)
            clean.append({"city": c, "state": s})
    return jsonify(clean)


@system_bp.route("/favicon.ico")
def favicon():
    return "", 204


@system_bp.route("/metrics")
def prometheus_metrics():
    lines = []
    lines.append("# HELP auto_heal_state Current auto-heal decision state per module")
    lines.append("# TYPE auto_heal_state gauge")
    state_path = PROJECT_ROOT / "data" / "auto_heal_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            for module, info in state.items():
                decision = info.get("decision", "UNKNOWN")
                retry_count = info.get("retry_count", 0)
                backoff_hours = info.get("backoff_hours", 0)
                status_code = info.get("status_code", 0)
                lines.append(f'auto_heal_state{{module="{module}",decision="{decision}"}} {1}')
                lines.append(f'auto_heal_retry_count{{module="{module}"}} {retry_count}')
                lines.append(f'auto_heal_backoff_hours{{module="{module}"}} {backoff_hours}')
                lines.append(f'auto_heal_status_code{{module="{module}"}} {status_code}')
        except Exception:
            pass

    lines.append("# HELP auto_heal_fatal_total Number of properties marked dead")
    lines.append("# TYPE auto_heal_fatal_total gauge")
    dead_path = PROJECT_ROOT / "data" / "dead_properties.json"
    if dead_path.exists():
        try:
            dead = json.loads(dead_path.read_text())
            lines.append(f"auto_heal_dead_properties_total {len(dead)}")
            by_source = {}
            for entry in dead:
                src = entry.get("source", "unknown")
                by_source[src] = by_source.get(src, 0) + 1
            for src, count in sorted(by_source.items()):
                lines.append(f'auto_heal_dead_properties{{source="{src}"}} {count}')
        except Exception:
            pass

    lines.append("# HELP flask_request_count Total requests processed")
    lines.append("# TYPE flask_request_count counter")
    lines.append(f"flask_request_count {request_count}")
    if request_count > 0:
        avg_latency = request_latency_sum / request_count
        lines.append("# HELP flask_request_latency_seconds Average request latency")
        lines.append("# TYPE flask_request_latency_seconds gauge")
        lines.append(f"flask_request_latency_seconds {avg_latency:.4f}")

    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@system_bp.route("/api/strategic-brief")
def get_strategic_brief():
    conn = get_conn()
    infra = sqlite3.connect(str(PROJECT_ROOT / "infrastructure.db"))
    infra.row_factory = sqlite3.Row

    top_sites = conn.execute("""
        SELECT address, county, acres, owner_name, score_tier as tier,
               score_total, score_transmission, distance_transmission_miles
        FROM commercial_sites
        WHERE score_tier = 'A' AND score_transmission >= 25
        ORDER BY score_total DESC LIMIT 5
    """).fetchall()

    ga_clusters = infra.execute("""
        SELECT rank, total_acres, parcel_count, county,
               top_parcel_id, top_zoning, top_assessed_value
        FROM ga_clusters
        ORDER BY total_acres DESC LIMIT 5
    """).fetchall()

    arc_total = conn.execute("""
        SELECT 'arc_parcels_fulton' as tbl, COUNT(*) as cnt FROM arc_parcels_fulton
        UNION ALL
        SELECT 'arc_parcels_dekalb', COUNT(*) FROM arc_parcels_dekalb
    """).fetchall()

    zoning = conn.execute("""
        SELECT
            CASE
                WHEN zoning IN ('I-1(CD)','I-2(CD)','IND','CBI','MUDD-O') THEN 'Industrial'
                WHEN zoning IN ('ML-1','ML-2','ML-2(ANDO)') THEN 'Light Industrial'
                WHEN zoning IN ('CC','CB','CG','RC','RB') THEN 'Commercial'
                WHEN zoning IN ('N1-A','N1-B','N2-B','NR') THEN 'Neighborhood Mixed'
                WHEN zoning IN ('RA-40','RA','RA-20','AR') THEN 'Ag/Rural Residential'
                WHEN zoning IN ('R-40','R-20','R-12MF(CD)','R-17MF(CD)','R1') THEN 'Residential'
                WHEN zoning IN ('CITY','Salisbury City','INST(CD)') THEN 'Urban/Institutional'
                WHEN zoning IS NULL OR zoning = '' OR zoning = 'UNKNOWN' THEN 'Unknown'
                ELSE 'Other'
            END as zone_group,
            COUNT(*) as cnt,
            ROUND(SUM(CAST(acres AS REAL))) as total_acres,
            ROUND(AVG(CAST(acres AS REAL)), 1) as avg_acres,
            ROUND(AVG(CAST(score_total AS REAL)), 0) as avg_score,
            ROUND(SUM(CASE WHEN score_tier='A' THEN 1 ELSE 0 END)) as tier_a,
            ROUND(SUM(CASE WHEN has_assembly_bonus=1 THEN 1 ELSE 0 END)) as assembly_eligible
        FROM commercial_sites
        GROUP BY zone_group
        ORDER BY total_acres DESC
    """).fetchall()

    zoning_by_county = conn.execute("""
        SELECT
            county,
            CASE
                WHEN zoning IN ('I-1(CD)','I-2(CD)','IND','CBI','MUDD-O') THEN 'Industrial'
                WHEN zoning IN ('ML-1','ML-2','ML-2(ANDO)') THEN 'Light Industrial'
                WHEN zoning IN ('RA-40','RA','RA-20','AR') THEN 'Ag/Rural Residential'
                WHEN zoning IS NULL OR zoning = '' OR zoning = 'UNKNOWN' THEN 'Unknown'
                ELSE 'Other'
            END as zone_group,
            COUNT(*) as cnt,
            ROUND(SUM(CAST(acres AS REAL))) as total_acres
        FROM commercial_sites
        WHERE acres IS NOT NULL AND CAST(acres AS REAL) >= 20
        GROUP BY county, zone_group
        ORDER BY county, total_acres DESC
    """).fetchall()

    conn.close()
    infra.close()

    return jsonify({
        "timeline": {
            "transformer_lead_time": "160 weeks (2029)",
            "gas_bridge_potential": "Q3 2027 (30MW)",
            "grid_interconnection": "5-7 years",
        },
        "zoning": [
            {
                "group": r[0],
                "count": r[1],
                "total_acres": r[2],
                "avg_acres": r[3],
                "avg_score": r[4],
                "tier_a": r[5],
                "assembly_eligible": r[6],
            }
            for r in zoning
        ],
        "zoning_by_county": [
            {
                "county": r[0],
                "group": r[1],
                "count": r[2],
                "total_acres": r[3],
            }
            for r in zoning_by_county
        ],
        "transmission_sites": [
            {
                "name": r["address"],
                "county": r["county"],
                "acres": r["acres"],
                "owner": r["owner_name"],
                "tier": r["tier"],
                "score": r["score_total"],
                "transmission_score": r["score_transmission"],
                "distance_miles": r["distance_transmission_miles"],
            }
            for r in top_sites
        ],
        "ga_clusters": [
            {
                "rank": r["rank"],
                "acres": r["total_acres"],
                "parcels": r["parcel_count"],
                "county": r["county"],
                "top_parcel": r["top_parcel_id"],
                "zoning": r["top_zoning"],
                "assessed_value": r["top_assessed_value"],
            }
            for r in ga_clusters
        ],
        "arc_ingest": {
            "fulton": arc_total[0]["cnt"],
            "dekalb": arc_total[1]["cnt"],
            "total": sum(r["cnt"] for r in arc_total),
        },
        "regulatory": {
            "charlotte_status": "Moratorium Vote June 8",
            "overlay_strategy": "AI-Readiness Overlay (Liquid Only)",
            "nc_deq_check": "Permit checklists drafted",
            "rowan_moratorium": "1-year passed May 4, 2026",
        },
    })
