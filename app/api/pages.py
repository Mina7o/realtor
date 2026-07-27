import os
from pathlib import Path
from flask import Blueprint, render_template

from app.api.common import PROJECT_ROOT

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("index.html")


@pages_bp.route("/charts")
def charts_page():
    return render_template("charts.html")


@pages_bp.route("/commercial")
def commercial():
    return render_template("commercial.html")


@pages_bp.route("/insights")
def insights():
    return render_template("insights.html")


@pages_bp.route("/commercial_charts")
def commercial_charts():
    return render_template("commercial_charts.html")


@pages_bp.route("/map")
def property_map():
    return render_template("map.html", google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY", ""))

@pages_bp.route("/code-graph")
def serve_code_graph():
    p = PROJECT_ROOT / "output/code_graph.html"
    if p.exists():
        return p.read_text(), 200, {"Content-Type": "text/html; charset=utf-8"}
    return "Run data_center/build_code_graph.py first", 404
