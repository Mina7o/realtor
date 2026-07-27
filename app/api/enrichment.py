from flask import Blueprint, jsonify, request
from db import get_conn, upsert_tax_record

enrichment_bp = Blueprint("enrichment", __name__)


@enrichment_bp.route("/api/enrich-tax", methods=["POST"])
def enrich_tax():
    data = request.json
    address = data.get("address", "")
    city = data.get("city", "")
    state = data.get("state", "")
    property_id = data.get("property_id")

    if not address:
        return jsonify({"error": "Address required"}), 400

    conn = get_conn()
    try:
        if property_id:
            tax = conn.execute(
                "SELECT * FROM tax_records WHERE property_id=? ORDER BY tax_year DESC LIMIT 1",
                (property_id,),
            ).fetchone()
            if tax:
                return jsonify({
                    "taxYear": tax["tax_year"],
                    "taxValue": tax["mkt_val_total"],
                    "taxLand": tax["mkt_val_land"],
                    "taxImprovements": tax["mkt_val_building"],
                    "salePrice": tax["sale_price"],
                    "saleDate": tax["sale_date"],
                    "yearBuilt": tax["year_built"],
                    "sqft": tax["sqft"],
                    "bedrooms": tax["bedrooms"],
                    "bathrooms": tax["bathrooms"],
                    "source": "tax_records",
                })

        norm = address.strip().lower()
        for table, addr_col, val_col, land_col, bldg_col, sale_col, sale_date_col in [
            ("mecklenburg_parcels", "situsaddress1", "amt_totalvalue", "amt_landvalue", "amt_netbldgvalue", "amt_price", "dte_dateofsale"),
            ("union_parcels", "physstradd", "fmv_total", "fmv_land", "fmv_imprv", "s1_salesamt", "s1_saledate"),
            ("york_parcels", "property_address", "apr_tot_val", "apr_land_val", "apr_bldg_val", "sale_price", "date_sold"),
        ]:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE LOWER({addr_col}) LIKE ? LIMIT 1",
                (f"%{norm}%",),
            ).fetchone()
            if row:
                return jsonify({
                    "taxValue": row[val_col],
                    "taxLand": row[land_col],
                    "taxImprovements": row[bldg_col],
                    "salePrice": row[sale_col],
                    "saleDate": row[sale_date_col],
                    "source": table,
                })

        return jsonify({"error": "No tax data found locally"}), 404
    finally:
        conn.close()


@enrichment_bp.route("/api/enrich-batch", methods=["POST"])
def enrich_batch():
    conn = get_conn()
    listings = conn.execute("""
        SELECT p.id, p.address, p.city, p.state
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN tax_records t ON p.id = t.property_id
        WHERE t.id IS NULL
        LIMIT 100
    """).fetchall()

    enriched = 0
    for l in listings:
        norm = (l["address"] or "").strip().lower()
        if not norm:
            continue
        for table, addr_col, val_col, land_col, bldg_col, sale_col, sale_date_col in [
            ("mecklenburg_parcels", "situsaddress1", "amt_totalvalue", "amt_landvalue", "amt_netbldgvalue", "amt_price", "dte_dateofsale"),
            ("union_parcels", "physstradd", "fmv_total", "fmv_land", "fmv_imprv", "s1_salesamt", "s1_saledate"),
            ("york_parcels", "property_address", "apr_tot_val", "apr_land_val", "apr_bldg_val", "sale_price", "date_sold"),
        ]:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE LOWER({addr_col}) LIKE ? LIMIT 1",
                (f"%{norm}%",),
            ).fetchone()
            if row:
                upsert_tax_record(
                    property_id=l["id"],
                    mkt_val_total=row[val_col],
                    mkt_val_land=row[land_col],
                    mkt_val_building=row[bldg_col],
                    sale_price=row[sale_col],
                    sale_date=row[sale_date_col],
                )
                enriched += 1
                break
    conn.close()
    return jsonify({"enriched": enriched, "total": len(listings)})


@enrichment_bp.route("/api/evaluate/<int:property_id>")
def evaluate_property(property_id):
    from python.evaluate_property import evaluate_property as eval_prop
    result = eval_prop(property_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
