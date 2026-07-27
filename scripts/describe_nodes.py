"""Generate rich descriptions for all graph nodes using per-category templates."""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent.parent
GRAPH_JSON = ROOT / "graphify-out" / "graph.json"

CATEGORY_RULES = [
    ("fetch_", "data-fetch"),
    ("enrich_", "enrichment"),
    ("commercial_", "commercial"),
    ("grid_", "gis"),
    ("snap_", "gis"),
    ("get_", "query"),
    ("load_", "query"),
    ("find_", "query"),
    ("query_", "query"),
    ("create_", "setup"),
    ("ingest_", "setup"),
    ("ensure_", "db-helper"),
    ("store_", "db-helper"),
    ("save_", "db-helper"),
    ("epoch_to_", "utility"),
]

TEMPLATES = {
    "data-fetch": "Scrapes {county} {data_type} from {source} and stores into {db_table} for {purpose}.",
    "enrichment": "Enriches listings with {data_type} via {api} for {purpose}.",
    "query": "Queries {db_table} for {data} — used by {callers} for {purpose}.",
    "commercial": "Commercial real estate: {operation} — queries {data_source} for {purpose}.",
    "gis": "GIS operation: {operation} using {data_source} for {purpose}.",
    "setup": "One-time setup: {operation} to prepare {target} for {purpose}.",
    "db-helper": "Database utility: {operation} on {db_table}.",
    "utility": "Utility: {operation}.",
    "db-table": "{db_type} table [{table_name}] — stores {content}. Queried by {consumers}.",
    "generic-code": "{file_type} node in {source_file}.{neighbor_suffix}",
}

COUNTIES = {
    "mecklenburg": "Mecklenburg County", "meck": "Mecklenburg County",
    "dekalb": "DeKalb County", "fulton": "Fulton County",
    "gwinnett": "Gwinnett County", "cobb": "Cobb County",
    "fayette": "Fayette County", "orange": "Orange County",
    "nassau": "Nassau County", "travis": "Travis County",
    "dcad": "Dallas County", "union": "Union County",
    "york": "York County", "forsyth": "Forsyth County",
    "wake": "Wake County",
}

SOURCE_COUNTY_MAP = {
    "mecklenburg": "Mecklenburg County", "dekalb": "DeKalb County",
    "fulton": "Fulton County", "orange": "Orange County",
    "union": "Union County", "york": "York County",
    "wake": "Wake County", "forsyth": "Forsyth County",
    "nassau": "Nassau County", "travis": "Travis County",
    "texas": "Texas",
}

KNOWLEDGE_MAP = {
    "fetch_mecklenburg": {"county": "Mecklenburg County", "data_type": "tax assessment", "source": "Mecklenburg County GIS/ARC via web scraping", "purpose": "property tax assessment analysis and valuation"},
    "fetch_dekalb": {"county": "DeKalb County", "data_type": "tax assessment", "source": "DeKalb County QPublic web portal", "purpose": "property tax assessment analysis"},
    "fetch_fulton": {"county": "Fulton County", "data_type": "tax assessment", "source": "Fulton County QPublic web portal", "purpose": "property tax assessment analysis"},
    "fetch_orange": {"county": "Orange County", "data_type": "tax assessment", "source": "Orange County Property Appraiser", "purpose": "property tax assessment analysis"},
    "fetch_forsyth": {"county": "Forsyth County", "data_type": "tax assessment", "source": "Forsyth County tax records", "purpose": "property tax assessment analysis"},
    "fetch_wake": {"county": "Wake County", "data_type": "tax assessment", "source": "Wake County tax records", "purpose": "property tax assessment analysis"},
    "fetch_travis": {"county": "Travis County", "data_type": "tax assessment", "source": "Travis Central Appraisal District", "purpose": "property tax assessment analysis"},
    "fetch_dcad": {"county": "Dallas County", "data_type": "parcel assessment", "source": "DCAD parcel data feed", "purpose": "property tax assessment analysis"},
    "fetch_zillow": {"data_type": "for-sale listings", "source": "Zillow", "purpose": "market listing aggregation"},
    "fetch_realtor": {"data_type": "for-sale listings", "source": "Realtor.com", "purpose": "market listing aggregation"},
    "fetch_fsbo": {"data_type": "FSBO listings", "source": "FSBO.com / ForSaleByOwner.com", "purpose": "FSBO listing aggregation"},
    "fetch_sellbyowner": {"data_type": "FSBO listings", "source": "SellByOwner.com", "purpose": "FSBO listing aggregation"},
    "fetch_landandfarm": {"data_type": "rural land listings", "source": "LandAndFarm.com", "purpose": "rural land opportunity analysis"},
    "fetch_county_download": {"county": "specified county", "data_type": "tax parcel", "source": "county ArcGIS REST endpoint", "purpose": "mass parcel ingestion"},
    "fetch_esri_layer": {"data_type": "geospatial layer", "source": "ArcGIS FeatureServer", "purpose": "GIS data ingestion"},
    "fetch_overture_data": {"data_type": "land-use polygons", "source": "Overture Maps", "purpose": "land-use classification analysis"},
    "fetch_all_facilities": {"data_type": "peering facility locations", "source": "PeeringDB", "purpose": "data center infrastructure analysis"},
    "fetch_centroids": {"data_type": "parcel geometry centroids", "source": "ArcGIS FeatureServer", "purpose": "parcel boundary mapping"},
    "enrich_tax": {"api": "RentCast", "data_type": "tax and valuation data", "purpose": "augmenting listing records with current tax assessments and estimated values"},
    "enrich_batch": {"api": "RentCast", "data_type": "tax and valuation data", "purpose": "batch-augmenting multiple listing records (max 45 per run under free tier)"},
    "enrich_properties": {"api": "RentCast", "data_type": "property details", "purpose": "filling in missing property metadata"},
    "enrich_with_attom": {"api": "ATTOM", "data_type": "detailed property profile", "purpose": "augmenting listings with ATTOM's expanded property data (avm, lot, building)"},
    "call_expandedprofile": {"api": "ATTOM", "data_type": "expanded property profile", "purpose": "fetching ATTOM's detailed property profile API"},
    "lookup_rentcast": {"api": "RentCast", "data_type": "property data", "purpose": "property data enrichment via RentCast API"},
    "get_deal_tier": {"data": "deal classification", "db_table": "listings", "purpose": "classifying deals into value tiers (undervalued, fair, premium)"},
    "get_nearby_deals": {"data": "undervalued deals", "db_table": "listings", "purpose": "finding undervalued properties within radius of a location"},
    "get_listings": {"data": "listing records", "db_table": "listings", "purpose": "serving filtered listing data to the frontend"},
    "get_stats": {"data": "listing statistics", "db_table": "listings", "purpose": "computing aggregate listing stats for dashboard"},
    "get_charts": {"data": "chart-ready aggregates", "db_table": "listings", "purpose": "generating chart data for the frontend dashboard"},
    "get_price_history": {"data": "price history", "db_table": "property_details", "purpose": "retrieving historical price data for a specific listing"},
    "get_property_details": {"data": "property details", "db_table": "property_details", "purpose": "retrieving detailed property records"},
    "get_commercial_sites": {"data": "commercial site records", "db_table": "commercial_sites", "purpose": "serving commercial site data to the frontend"},
    "get_commercial_stats": {"data": "commercial site statistics", "db_table": "commercial_sites", "purpose": "computing aggregate commercial site metrics"},
    "get_quiet_county_portfolio": {"data": "quiet county portfolio", "db_table": "commercial_sites", "purpose": "aggregating quiet-county acreage into portfolio summaries"},
    "get_strategic_brief": {"data": "strategic insights", "db_table": "commercial_sites", "purpose": "synthesizing strategic recommendations from site data"},
    "get_transmission_lines": {"data": "transmission line geometries", "db_table": "infrastructure", "purpose": "serving transmission line GIS data"},
    "get_substations": {"data": "substation locations", "db_table": "infrastructure", "purpose": "serving substation GIS data"},
    "get_landandfarm_listings": {"data": "rural land listings", "db_table": "listings", "purpose": "serving LandAndFarm listing data to the frontend"},
    "get_landandfarm_stats": {"data": "rural land statistics", "db_table": "listings", "purpose": "computing LandAndFarm aggregate metrics"},
    "get_tier": {"data": "zoning tier classification", "db_table": "commercial_sites", "purpose": "determining zoning tier for commercial site evaluation"},
    "fetch_nconemap_county": {"data_type": "North Carolina county parcel data", "source": "NCOneMap ArcGIS FeatureServer", "purpose": "North Carolina parcel data collection"},
    "fetch_arcgis_layer": {"data_type": "ArcGIS feature layer data", "source": "ArcGIS FeatureServer", "purpose": "geospatial data ingestion"},
    "fetch_county_parcels": {"county": "specified county", "data_type": "parcel records", "source": "county ArcGIS server", "purpose": "bulk parcel ingestion from county GIS"},
    "scrape_ercot": {"data_type": "ERCOT interconnection queue", "source": "ERCOT website", "purpose": "Texas grid interconnection analysis"},
    "fetch_sale_listings": {"data_type": "for-sale listings", "source": "RentCast API", "purpose": "RentCast listing data retrieval"},
    "fetch_property_details": {"data_type": "property detail records", "source": "RentCast API", "purpose": "RentCast property detail retrieval"},
    "fetch_overture_data": {"data_type": "land-use polygons", "source": "Overture Maps via DuckDB", "purpose": "land-use classification for site analysis"},
    "fetch_landandfarm": {"data_type": "rural land listings", "source": "LandAndFarm.com", "purpose": "rural land opportunity analysis"},
    "run_model": {"operation": "off-grid feasibility model", "source": "Texas infrastructure data", "purpose": "evaluating off-grid development potential"},
}

SOURCE_CONTEXT = {
    "app.py": "Flask API — serves dashboard pages and REST endpoints for listings, charts, and commercial site data.",
    "db.py": "SQLite database — manages tables for listings, properties, tax records, counties, and deal tiers.",
    "mongo_db.py": "MongoDB — atlas integration for commercial site scoring and opportunity mapping.",
    "fetch_mecklenburg.py": "Scraping pipeline — Mecklenburg County tax assessment data.",
    "fetch_union_county.py": "Scraping pipeline — Union County (NC) property tax data.",
    "fetch_york_county.py": "Scraping pipeline — York County (SC) property tax data.",
    "fetch_wake_county.py": "Scraping pipeline — Wake County (NC) property tax data.",
    "fetch_fulton_scrape.py": "Scraping pipeline — Fulton County (GA) property tax data.",
    "fetch_dekalb_scrape.py": "Scraping pipeline — DeKalb County (GA) property tax data.",
    "fetch_orange_scrape.py": "Scraping pipeline — Orange County (FL) property tax data.",
    "fetch_zillow_crawl4ai.py": "Zillow scraper — headless browser listing extraction.",
    "fetch_realtor_crawl4ai.py": "Realtor.com scraper — headless browser listing extraction.",
    "fetch_fsbo_crawl4ai.py": "FSBO.com scraper — headless browser for-sale listing extraction.",
    "fetch_sellbyowner_crawl4ai.py": "SellByOwner.com scraper — headless browser listing extraction.",
    "fetch_realtor_details.py": "Realtor.com — individual listing detail scraper with proxy rotation.",
    "fetch_zillow_details.py": "Zillow — individual listing detail scraper with proxy rotation.",
    "fetch_zillow_details_browser_use.py": "Zillow — listing detail scraper using browser-use automation framework.",
    "fetch_attom.py": "ATTOM enrichment — augments listings with expanded property profiles via ATTOM API.",
    "fetch_listings.py": "Listing ingestion — primary pipeline combining RentCast and Zillow sources.",
    "fetch_landandfarm.py": "LandAndFarm — rural land and farm listing ingestion.",
    "fetch_rentcast_land.py": "RentCast — land and property data lookup via RentCast API.",
    "find_deals.py": "Deal detection — scores listings for undervalued investment opportunities.",
    "sweep_sold.py": "Stale cleanup — removes sold/expired listings from the database.",
    "fetch_landandfarm_description.py": "LandAndFarm — fetches detailed listing descriptions.",
    "score_zoning.py": "Zoning scoring — evaluates zoning compatibility for commercial development.",
    "backfill_details.py": "Backfill — fills missing property detail records from RentCast.",
    "backfill_zestimates.py": "Backfill — fills missing Zillow Zestimate valuation data.",
    "create_infrastructure_db.py": "Infrastructure DB — creates and populates transmission line and substation tables.",
}

KNOWN_FUNCTIONS = {
    "main": "Pipeline entry point orchestrating data processing steps.",
    "index": "Renders the main dashboard page template.",
    "charts_page": "Renders the charts and analytics page template.",
    "commercial": "Renders the commercial real estate sites page.",
    "insights": "Renders the strategic insights page.",
    "serve_code_graph": "Serves the interactive code graph HTML visualization.",
    "favicon": "Serves the favicon placeholder response.",
    "api_health": "Returns cron and scraper health status from data/status.json.",
    "api_cities": "Returns distinct cities with active listings.",
    "no_cache": "Sets no-cache response headers on HTTP responses.",
    "_rentcast_call": "RentCast API call wrapper with daily quota tracking.",
    "build_listing_filters": "Builds MongoDB query filters from request parameters.",
    "backfill_details": "Fills in missing property detail records from RentCast.",
    "backfill_zestimates": "Backfills Zillow Zestimate valuation data.",
    "resolve_state": "Resolves U.S. state from lat/lng coordinates.",
    "nearest_substation": "Finds nearest electrical substation to a point.",
    "scrape_ercot": "Scrapes ERCOT interconnection queue data for Texas grid analysis.",
    "scrape_zillow": "Scrapes Zillow for-sale listings using headless browser.",
    "scrape_realtor": "Scrapes Realtor.com for-sale listings using headless browser.",
    "scrape_fsbo": "Scrapes FSBO for-sale listings using headless browser.",
    "scrape_sellbyowner": "Scrapes SellByOwner for-sale listings using headless browser.",
    "scrape_detail": "Scrapes individual listing detail pages from Realtor.com.",
    "scrape_listing": "Scrapes individual Zillow listing details via browser automation.",
    "scrape_property_page": "Scrapes individual Zillow property page for details.",
    "fetch_page": "Fetches a single paginated page of records from the data source.",
    "fetch_all": "Fetches all available records with automatic pagination.",
    "fetch_county": "Fetches county-specific data from the configured source.",
    "fetch_single_page": "Fetches one page of search results using headless browser.",
    "fetch_sale_listings": "Fetches for-sale listing data from RentCast API.",
    "fetch_property_details": "Fetches property detail records from RentCast API.",
    "fetch_state": "Fetches state-level infrastructure or land-use data.",
    "fetch_overture_data": "Fetches Overture Maps land-use polygon data via DuckDB.",
    "fetch_esri_layer": "Fetches a feature layer from an ArcGIS REST endpoint.",
    "fetch_arcgis_layer": "Fetches geographic data from ArcGIS FeatureServer.",
    "fetch_nconemap_county": "Fetches North Carolina county parcel data from NCOneMap.",
    "fetch_centroids": "Fetches parcel geometry centroids from ArcGIS FeatureServer.",
    "fetch_all_facilities": "Fetches peering facility locations from PeeringDB.",
    "fetch_landandfarm_listings": "Fetches rural land listings from LandAndFarm.com.",
    "fetch_with_duckdb": "Fetches Overture geospatial data using DuckDB SQL engine.",
    "fetch_combined": "Combines and normalizes Overture data from multiple fetches.",
    "get_listings_to_fetch": "Determines which listings need detail data fetched.",
    "epoch_to_date": "Converts Unix epoch timestamp to human-readable date string.",
    "call_api": "Makes a generic external REST API call.",
    "call_expandedprofile": "Calls ATTOM expanded property profile API endpoint.",
    "create_indexes": "Creates MongoDB collection indexes for query performance.",
    "validate_geometry": "Validates GeoJSON geometry coordinates.",
    "score_fiber_density": "Scores fiber optic internet density at commercial sites.",
    "score_substation_proximity": "Scores proximity to electrical substations.",
    "score_land_use_density": "Scores density of compatible land-use classifications.",
    "score_transmission_density": "Scores density of high-voltage transmission lines.",
    "update_site_scoring": "Updates all commercial site scoring metrics.",
    "ensure_columns": "Ensures required database columns exist on tables.",
    "get_raw_tables": "Gets list of source raw parcel table names.",
    "determine_tier": "Determines commercial site investment tier classification.",
    "classify_land_use": "Classifies land use type from zoning codes.",
    "classify_zoning": "Classifies and normalizes zoning code designations.",
    "load_zoning": "Loads zoning classification data into the database.",
    "load_utility_polygons": "Loads utility service territory polygon boundaries.",
    "load_land_use_to_db": "Loads Overture land-use data into the database.",
    "get_combined_bbox": "Gets combined bounding box for specified states and counties.",
    "create_facilities_table": "Creates the peering facilities database table.",
    "ingest_facilities": "Ingests peering facility records into the database.",
    "ingest_substations": "Ingests electrical substation location data.",
    "ingest_transmission": "Ingests high-voltage transmission line data.",
    "load_geojson": "Loads a GeoJSON file into the database.",
    "load_state_boundaries": "Loads U.S. state boundary polygon geometries.",
    "load_hv_lines": "Loads high-voltage transmission line GIS data.",
    "haversine": "Computes geodesic (Haversine) distance between two points.",
    "spatial_query": "Runs a spatial query against ArcGIS REST services.",
    "arcgis_query": "Queries an ArcGIS REST feature service endpoint.",
    "build_query": "Builds a parameterized geocoding or data query.",
    "point_to_line_dist": "Computes minimum distance from point to line segment.",
    "is_valid_lat": "Validates latitude coordinate value.",
    "is_valid_lng": "Validates longitude coordinate value.",
    "parse_arcgis_date": "Parses ArcGIS epoch millisecond date format.",
    "sleep": "Pauses execution for API rate limiting.",
    "get_env": "Reads an environment variable with optional default.",
    "get_mongo": "Gets a MongoDB database handle from connection pool.",
    "get_conn": "Gets a SQLite database connection, creating it if needed.",
    "score_listing": "Scores an individual listing for investment potential.",
    "score_parcel": "Scores an individual tax parcel for development potential.",
    "score_listings": "Scores all listings for investment potential.",
    "score_sites": "Scores all commercial sites for development suitability.",
    "score_overture": "Scores Overture land-use data for development suitability.",
    "_extract_sql_string": "Extracts SQL query string from Python source code.",
    "extract_sql_tables": "Parses table names from SQL query text.",
    "get_function_ranges": "Finds function boundary line ranges in Python source files.",
    "find_file_node": "Looks up a graph node by file path.",
    "find_function_node": "Looks up a graph node by function name.",
    "is_valid_table": "Validates a database table name against known tables.",
    "compute_assembly_bonus": "Computes assembly bonus score for aggregated parcels.",
    "update_db": "Updates database records with new computed values.",
    "compute_density_scores": "Computes density-based scoring for commercial sites.",
    "land_use_score": "Computes land-use compatibility score for sites.",
    "owner_type_score": "Scores sites by property owner type classification.",
    "save_sites": "Scores and saves commercial site records to database.",
    "build_meck_sites": "Builds Mecklenburg County commercial site records.",
    "build_union_sites": "Builds Union County commercial site records.",
    "build_york_sites": "Builds York County commercial site records.",
    "ingest_dcad": "Ingests Dallas Central Appraisal District parcel data.",
    "ingest_travis": "Ingests Travis Central Appraisal District parcel data.",
    "merge_texas": "Merges multiple Texas parcel data sources.",
    "transmission_match": "Matches commercial sites to nearby transmission lines.",
    "cluster_parcels": "Clusters parcels into development opportunity groups.",
    "extract_ga_parcels": "Extracts Georgia parcel geometry data from ArcGIS.",
    "save_clusters": "Saves parcel cluster analysis results to database.",
    "batch_site": "Batch-enriches commercial sites with scoring data.",
    "add_body": "Builds a body section in the generated report document.",
    "add_heading": "Builds a heading section in the generated report document.",
    "add_table": "Builds a table section in the generated report document.",
    "_point_segment_dist": "Computes minimum distance from point to line segment.",
    "simplify_coords": "Simplifies coordinate sequences by removing redundant points.",
    "get_county_conn": "Gets county-specific SQLite database connection.",
    "normalize_for_match": "Normalizes address text for parcel matching.",
    "score_match": "Scores confidence of an address-to-parcel match.",
    "analyze_results": "Analyzes parcel matching results for quality metrics.",
    "parse_street_parts": "Parses street address into components (number, name, suffix).",
    "find_matches": "Finds matching parcel records for a given address.",
    "clean_parcel_address": "Cleans and normalizes a parcel address for matching.",
    "merge_properties": "Merges duplicate property records in the database.",
    "upsert_county": "Inserts or updates a county record.",
    "upsert_listing": "Inserts or updates a listing record.",
    "upsert_property": "Inserts or updates a property record.",
    "upsert_tax_record": "Inserts or updates a tax record.",
    "normalize_address": "Normalizes an address string to a standard format.",
    "make_county_url": "Builds the URL for fetching county data.",
    "make_page_url": "Builds paginated URL for data fetching.",
    "save_listings": "Saves scraped listing records to the database.",
    "parse_acres": "Parses acreage value from text string.",
    "parse_price": "Parses price value from text string.",
    "manual_import": "Manually imports ERCOT queue data from file.",
    "calc_econ_score": "Calculates economic development score for sites.",
    "calc_flood_score": "Calculates flood risk score for sites.",
    "calc_zoning_score": "Calculates zoning compatibility score for sites.",
    "assembly_bonus": "Computes parcel assembly bonus feasibility score.",
    "score_proximity": "Scores proximity of sites to key infrastructure.",
    "load_offset": "Loads pagination offset position from tracking file.",
    "save_offset": "Saves pagination offset position for resumable scraping.",
    "get_total_count": "Gets total record count from data source.",
    "ensure_raw_table": "Creates raw data staging table if not exists.",
    "store_parcels": "Stores fetched parcel records to the database.",
    "construct_county_key": "Builds a county-specific matching key from address.",
    "match_and_store": "Matches listing to parcel and stores the result.",
    "ensure_indexes": "Creates database indexes for query performance.",
    "plot_sites": "Generates a visualization plot of commercial sites.",
    "plot_transmission": "Generates a visualization plot of transmission lines.",
    "fetch_buildings_overturemaps_py": "Fetches building footprint data from Overture Maps.",
    "fetch_landuse_overturemaps_py": "Fetches land-use classification data from Overture Maps.",
    "run_model": "Runs the off-grid feasibility model for Texas sites.",
    "classify": "Classifies a record using the configured logic.",
    "run": "Runs the batch processing pipeline step.",
    "is_strategic": "Determines if a graph node qualifies as strategic for gold-star marking.",
    "add_node": "Adds a node to the code graph during graph build.",
    "add_edge": "Adds an edge to the code graph during graph build.",
    "build_com": "Generates the commercial site report composite document.",
    "backfill_from_json": "Backfills property details from a JSON backup file.",
    "backfill": "Runs the backfill pipeline for missing data.",
    "score_zoning": "Scores a site's zoning classification for development suitability.",
    "normalize_zoning_code": "Normalizes a raw zoning code to a standard classification.",
    "normalize_land_use": "Normalizes land-use classifications from varied sources.",
    "batch_nc_remaining": "Batch processes remaining North Carolina counties.",
    "get_nearby_deals_sorted": "Returns undervalued deals sorted by distance from a point.",
    "build_report": "Builds the full commercial property report document.",
    "normalize_code": "Normalizes a single zoning or land-use code.",
    "batch_site_scoring": "Runs batch site scoring for all commercial sites.",
    "score_cell_tower": "Scores proximity to cell tower infrastructure.",
    "calc_proximity_score": "Calculates composite proximity score for site features.",
    "extract_density": "Extracts density metrics from geospatial data.",
    "get_estated_data": "Fetches property data from Estated API.",
    "match_property": "Matches a listing record to a property record.",
    "get_address_candidates": "Gets geocoding candidates for an address.",
    "reverse_geocode": "Resolves address from lat/lng coordinates.",
    "pick_best": "Picks the best match from geocoding candidates.",
    "get_counties": "Returns list of counties in the database.",
    "get_cities": "Returns list of cities in the database.",
    "get_listing": "Returns a single listing by ID.",
    "update_listing": "Updates a single listing record.",
    "delete_listing": "Deletes a listing record from the database.",
    "import_listings": "Imports listing records from an external source.",
    "export_listings": "Exports listing records for external use.",
    "run_pipeline": "Runs the full data pipeline end to end.",
    "build_cache": "Pre-computes and caches expensive query results.",
    "geocode": "Geocodes addresses to latitude/longitude coordinates.",
    "identify_targets": "Identifies outreach target records from site data.",
    "infer_entity_type": "Infers entity type classification from property data.",
    "build_crm": "Builds the outreach CRM database from commercial site data.",
    "point_to_line_distance": "Computes minimum distance from a point to a line geometry.",
    "probe_url": "Probes a URL to check if it is accessible.",
    "discover_county": "Discovers available county data sources.",
    "download_county": "Downloads county parcel data from ArcGIS server.",
    "summary": "Prints a summary of fetched data.",
    "discover_fields": "Discovers available field names from a data source.",
    "normalize_arcgis_record": "Normalizes an ArcGIS record to standard field names.",
    "transform_acres": "Transforms acreage values to standard format.",
    "extract_listings": "Extracts listing records from scraped data.",
    "extract_listing": "Extracts a single listing record from API response.",
    "api_get": "HTTP GET request wrapper with error handling.",
    "_check_quota": "Checks API daily quota before making a call.",
    "print_summary": "Prints a processing summary to stdout.",
    "download_fl_county": "Downloads Florida county parcel data.",
    "discover_va_endpoints": "Discovers Virginia county ArcGIS endpoints.",
    "purchase_price": "Computes estimated purchase price for a property.",
    "md_to_docx": "Converts markdown document to DOCX format.",
    "fill_loi_target": "Fills in LOI (letter of intent) target fields.",
    "process_features": "Processes ArcGIS feature records for ingestion.",
    "download_objectid_range": "Downloads ArcGIS features by OBJECTID range.",
    "merge_table": "Merges a source table into the commercial sites table.",
    "migrate_commercial_sites": "Migrates commercial site data to MongoDB.",
    "migrate_tx_stratmap_parcels": "Migrates Texas stratmap parcel data to MongoDB.",
    "migrate_transmission_lines": "Migrates transmission line data to MongoDB.",
}

SOURCE_CATEGORIES = {
    "data_center/": "data processing pipeline",
    "app.py": "Flask web application",
    "scripts/": "utility scripts and graph tools",
    "data/": "data analysis and model scripts",
}


def classify_node(n):
    label = n["label"]
    file_type = n.get("file_type", "")
    is_func = label.endswith("()")
    func_name = label.rstrip("()") if is_func else label

    if n["id"].startswith("__db__") or file_type == "database":
        return "db-table"
    if file_type == "rationale":
        return "rationale"
    if file_type == "document":
        return "document"

    if is_func:
        for prefix, cat in CATEGORY_RULES:
            if func_name.startswith(prefix):
                return cat

    return "generic-code"


def get_neighbor_map(nodes, links):
    node_map = {n["id"]: n for n in nodes}
    nbrs = {}
    for e in links:
        s, t = e["source"], e["target"]
        nbrs.setdefault(s, set()).add(t)
        nbrs.setdefault(t, set()).add(s)
    return node_map, nbrs


def extract_county(text):
    text_lower = text.lower().replace("_", "").replace("-", "")
    for key, val in COUNTIES.items():
        key_clean = key.lower().replace("_", "").replace("-", "")
        if key_clean in text_lower:
            return val
    return None


def extract_source_county(source_file):
    if not source_file:
        return None
    src_lower = source_file.lower()
    for key, val in SOURCE_COUNTY_MAP.items():
        if key in src_lower:
            return val
    return None


def knowledge_lookup(label_key):
    for known_key in sorted(KNOWLEDGE_MAP, key=len, reverse=True):
        if label_key.startswith(known_key) or label_key == known_key:
            return dict(KNOWLEDGE_MAP[known_key])
    return None


def fetch_fill(label, source_file, db_tables):
    func_name = label.rstrip("()") if label.endswith("()") else label
    fill = knowledge_lookup(func_name)
    if fill:
        if db_tables and "db_table" not in fill:
            fill["db_table"] = db_tables[0]
        fill.setdefault("db_table", "the database")
        fill.setdefault("county", extract_source_county(source_file) or "target county")
        return fill

    county = extract_county(func_name) or extract_source_county(source_file) or "target county"
    data_type = "property records"
    source = "external county data source"
    purpose = "property data collection"

    if "zillow" in func_name.lower():
        source = "Zillow"
        data_type = "for-sale listings"
        purpose = "market listing aggregation"
    elif "realtor" in func_name.lower():
        source = "Realtor.com"
        data_type = "for-sale listings"
        purpose = "market listing aggregation"
    elif "fsbo" in func_name.lower() or "sellbyowner" in func_name.lower():
        source = "FSBO / for-sale-by-owner sites"
        data_type = "FSBO listings"
        purpose = "FSBO listing aggregation"
    elif "landandfarm" in func_name.lower():
        source = "LandAndFarm.com"
        data_type = "rural land listings"
        purpose = "rural land opportunity analysis"
    elif "overture" in func_name.lower():
        source = "Overture Maps"
        data_type = "geospatial data"
        purpose = "land-use classification analysis"
    elif "ercot" in func_name.lower():
        source = "ERCOT"
        data_type = "interconnection queue"
        purpose = "Texas grid interconnection analysis"
    elif "esri" in func_name.lower() or "arcgis" in func_name.lower():
        source = "ArcGIS FeatureServer"
        data_type = "geospatial feature layer"
        purpose = "GIS data ingestion"
    elif "facility" in func_name.lower():
        source = "PeeringDB"
        data_type = "peering facility locations"
        purpose = "data center infrastructure analysis"
    elif "centroid" in func_name.lower():
        source = "ArcGIS FeatureServer"
        data_type = "parcel geometry centroids"
        purpose = "parcel boundary mapping"

    db_table = db_tables[0] if db_tables else "the database"
    county_str = f"{county}" if county else ""
    if county_str and not county_str.endswith("County"):
        county_str = f"{county_str} County"

    fill = {
        "county": county_str or "target county",
        "data_type": data_type,
        "source": source,
        "db_table": db_table,
        "purpose": purpose,
    }
    return fill


def enrich_fill(label, source_file, db_tables):
    func_name = label.rstrip("()") if label.endswith("()") else label
    fill = knowledge_lookup(func_name)
    if fill:
        return fill

    api = "third-party API"
    data_type = "enrichment data"
    purpose = "data enrichment"
    if "attom" in func_name.lower() or "attom" in (source_file or "").lower():
        api = "ATTOM"
        data_type = "expanded property profile (AVM, lot, building)"
        purpose = "augmenting listings with ATTOM property profiles"
    elif "rentcast" in func_name.lower() or "rentcast" in (source_file or "").lower():
        api = "RentCast"
        data_type = "property data"
        purpose = "filling in missing property metadata"

    return {"api": api, "data_type": data_type, "purpose": purpose}


def query_fill(label, source_file, db_tables, nid, nbrs, node_map):
    func_name = label.rstrip("()") if label.endswith("()") else label
    neighbor_funcs = []
    for nid2 in nbrs.get(nid, set()):
        other = node_map.get(nid2, {})
        if other.get("file_type") == "code":
            lbl = other.get("label", "")
            if lbl.endswith("()") and lbl != label:
                neighbor_funcs.append(lbl)
    caller_str = ", ".join(sorted(set(neighbor_funcs))[:3]) if neighbor_funcs else "other functions"

    fill = knowledge_lookup(func_name)
    if fill:
        if db_tables and "db_table" not in fill:
            fill["db_table"] = ", ".join(db_tables[:3])
        fill.setdefault("db_table", "the database")
        fill.setdefault("callers", caller_str)
        fill.setdefault("data", fill.get("data", "records"))
        return fill

    words = func_name.replace("get_", "").replace("load_", "").replace("find_", "").replace("query_", "").split("_")
    data = " ".join(words) if words else "records"
    db_table_str = ", ".join(db_tables[:3]) if db_tables else "the database"

    return {"db_table": db_table_str, "data": data, "callers": caller_str, "purpose": "data retrieval for analysis and display"}


def operation_name(cat, func_name):
    prefixes = {"commercial": "commercial_", "gis": "grid_", "setup": "create_", "db-helper": "ensure_", "utility": "epoch_to_"}
    if "snap_" in func_name and cat == "gis":
        prefixes["gis"] = "snap_"
    prefix = prefixes.get(cat, "")
    if cat == "db-helper":
        for p in ["ensure_", "store_", "save_"]:
            if func_name.startswith(p):
                prefix = p
                break
    words = func_name.replace(prefix, "", 1).split("_") if prefix else func_name.split("_")
    return " ".join(w for w in words if w)


def describe_node(n, node_map, nbrs):
    cat = classify_node(n)
    nid = n["id"]
    label = n["label"]
    func_name = label.rstrip("()") if label.endswith("()") else label
    source_file = n.get("source_file", "")

    if cat == "rationale":
        desc = n.get("label", "")
        if n.get("strategic"):
            desc += " ⭐ KEY NODE"
        return desc

    if cat == "document":
        desc = f"Module: {source_file or label}"
        if n.get("strategic"):
            desc += " ⭐ KEY NODE"
        return desc

    neighbor_funcs = []
    db_tables = []
    for nid2 in nbrs.get(nid, set()):
        other = node_map.get(nid2, {})
        ot = other.get("label", "")
        oft = other.get("file_type", "")
        if oft == "database":
            tbl_name = ot.split(" (")[0] if " (" in ot else ot
            db_tables.append(tbl_name)
        elif oft == "code" and ot.endswith("()") and ot != label:
            neighbor_funcs.append(ot)

    if cat == "generic-code":
        ncount = len(nbrs.get(nid, set()))
        is_func = label.endswith("()")

        if is_func and func_name in KNOWN_FUNCTIONS:
            desc = KNOWN_FUNCTIONS[func_name]

        elif not is_func:
            src_label = source_file or label
            ctx = SOURCE_CONTEXT.get(src_label)
            if ctx:
                base = f"File {label} — {ctx}"
            else:
                for prefix, cat_name in sorted(SOURCE_CATEGORIES.items(), key=lambda x: -len(x[0])):
                    if src_label.startswith(prefix):
                        base = f"File {label} — part of the {cat_name}."
                        break
                else:
                    base = f"File {label} — source module."
            desc = base

        else:
            desc = f"Function {label} in {source_file or '?'}."

        if ncount > 0 and not desc.endswith(f" {ncount} connections"):
            desc += f" Connected to {ncount} other nodes."
        if n.get("strategic"):
            desc += " ⭐ KEY NODE"
        return desc

    if cat == "db-table":
        tbl_label = label.split(" (")[0]
        is_mongo = "(mongo)" in label or "(mongodb)" in label
        db_type = "MongoDB" if is_mongo else "SQLite"
        consumers = []
        for nid2 in nbrs.get(nid, set()):
            other = node_map.get(nid2, {})
            if other.get("file_type") == "code":
                clbl = other.get("label", "")
                consumers.append(clbl if clbl.endswith("()") else clbl)
        unique_consumers = sorted(set(consumers))
        consumer_str = ", ".join(unique_consumers[:5])
        if len(unique_consumers) > 5:
            consumer_str += f" and {len(unique_consumers) - 5} more"
        if not consumer_str:
            consumer_str = "pipeline functions"
        content = tbl_label.replace("_", " ")
        desc = f"{db_type} table [{tbl_label}] — stores {content}. Queried by {consumer_str}."
        if n.get("strategic"):
            desc += " ⭐ KEY NODE"
        return desc

    if cat == "data-fetch":
        fill = fetch_fill(label, source_file, db_tables)
    elif cat == "enrichment":
        fill = enrich_fill(label, source_file, db_tables)
    elif cat == "query":
        fill = query_fill(label, source_file, db_tables, nid, nbrs, node_map)
    elif cat in ("commercial", "gis", "setup", "db-helper", "utility"):
        op = operation_name(cat, func_name)
        db_str = ", ".join(db_tables[:2]) if db_tables else "the database"
        fill = {"operation": op, "db_table": db_str, "data_source": db_str, "target": "the database", "purpose": "database and infrastructure setup"}
        purpose_map = {"commercial": "commercial real estate site analysis", "gis": "spatial analysis and geocoding", "setup": "database and infrastructure setup", "db-helper": "database operations", "utility": "supporting computation"}
        fill["purpose"] = purpose_map.get(cat, "operation")
        if cat == "db-helper" and op.startswith("table"):
            fill["operation"] = f"creating or verifying table structure"

    template = TEMPLATES.get(cat, "")
    try:
        desc = template.format(**fill)
    except KeyError:
        desc = f"{cat}: {func_name}"
    except ValueError:
        desc = f"{cat}: {func_name}"

    if source_file:
        desc += f" (in {source_file})"

    if n.get("strategic"):
        desc += " ⭐ KEY NODE"

    return desc


def main():
    graph = json.loads(GRAPH_JSON.read_text())
    nodes = graph["nodes"]
    links = graph.get("links", [])
    node_map, nbrs = get_neighbor_map(nodes, links)

    described = 0
    rationale_preserved = 0
    doc_preserved = 0

    for n in nodes:
        desc = describe_node(n, node_map, nbrs)
        if desc:
            n["description"] = desc
            described += 1
            if n.get("file_type") == "rationale":
                rationale_preserved += 1
            elif n.get("file_type") == "document":
                doc_preserved += 1

    GRAPH_JSON.write_text(json.dumps(graph, indent=2))
    print(f"Described {described}/{len(nodes)} nodes "
          f"(preserved {rationale_preserved} rationale + {doc_preserved} document descriptions).")


if __name__ == "__main__":
    main()
