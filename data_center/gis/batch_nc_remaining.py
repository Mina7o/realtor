"""Batch fetch remaining NC metro counties and integrate into scoring pipeline."""
import sys, os, json, time

from data_center.gis.fetch_county_parcels import fetch_county, summary

# Counties to fetch: (name, state, min_acres, source)
# Priority-ordered by data quality and data center potential
BATCH = [
    # ── RTP / Triangle ──
    ("Durham",   "NC", 10, "nconemap"),    # has addresses + use codes
    ("Orange",   "NC", 10, None),           # direct ArcGIS (auto-detected)
    ("Johnston", "NC", 10, "nconemap"),    # has addresses + use codes
    ("Chatham",  "NC", 10, "nconemap"),    # has addresses (no use codes)

    # ── Triad ──
    ("Guilford", "NC", 10, "nconemap"),    # has use codes (IND!) + owner, no siteadd
    ("Forsyth",  "NC", 10, "nconemap"),    # has addresses + use codes

    # ── Charlotte metro ──
    ("Rowan",    "NC", 10, "nconemap"),    # has addresses
    ("Iredell",  "NC", 10, "nconemap"),    # has addresses + use codes
]

# Cabarrus: skipped - no public ArcGIS server found, NC OneMap data is garbage


def run():
    results = {}
    for county, state, min_ac, source in BATCH:
        print(f"\n{'='*60}")
        print(f"  {county}, {state} (>= {min_ac}ac, source={source or 'auto'})")
        print(f"{'='*60}")

        table, count = fetch_county(county, state, min_acres=min_ac, source=source)

        results[county] = {
            "table": table,
            "count": count,
        }
        print(f"  Result: {count} parcels -> {table}")
        time.sleep(1)

    print(f"\n{'='*60}")
    print("  BATCH COMPLETE")
    print(f"{'='*60}")
    for county, r in results.items():
        print(f"  {county:15s}: {r['count']:>8,} parcels in {r['table'] or 'N/A'}")

    summary()

    # Save results for next pipeline step
    with open(os.path.join(SCRIPT_DIR, "batch_nc_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("\nResults saved to batch_nc_results.json")


if __name__ == "__main__":
    run()
