import os
import time
from flask import Flask, request
from flask_cors import CORS
from logger_setup import setup_logging, setup_request_logging
from app.api.common import request_count, request_latency_sum


def create_app():
    setup_logging("flask")
    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")
    CORS(app)
    app = setup_request_logging(app)

    api_key_env = os.getenv("API_KEY", "")

    @app.before_request
    def check_api_key():
        if api_key_env and request.path.startswith("/api/") and request.method != "OPTIONS":
            if request.headers.get("X-API-Key") != api_key_env:
                return {"error": "Unauthorized"}, 401

    @app.before_request
    def start_timer():
        request._start_time = time.time()

    @app.after_request
    def record_metrics(response):
        global request_count, request_latency_sum
        request_count += 1
        if hasattr(request, "_start_time"):
            request_latency_sum += time.time() - request._start_time
        return response

    @app.after_request
    def no_cache(response):
        if "text/html" in response.content_type:
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    from app.api.pages import pages_bp
    from app.api.listings import listings_bp
    from app.api.commercial import commercial_bp
    from app.api.enrichment import enrichment_bp
    from app.api.system import system_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(commercial_bp)
    app.register_blueprint(enrichment_bp)
    app.register_blueprint(system_bp)

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
