import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from db import get_conn

def backfill(json_path):
    conn = get_conn()
    with open(json_path) as f:
        data = json.load(f)

    done = 0
    skip = 0
    for d in data:
        zest = d.get("zestimate")
        if not zest or zest == "N/A" or not isinstance(zest, (int, float)):
            skip += 1
            continue

        addr = d.get("address_street") or d.get("address")
        zip_code = str(d.get("zip", "") or "")
        url = d.get("url")

        if not addr or not zip_code:
            skip += 1
            continue

        cur = conn.execute(
            "UPDATE listings SET zestimate = ? WHERE property_id IN "
            "(SELECT id FROM properties WHERE address = ? AND zip = ?) "
            "AND zestimate IS NULL",
            (zest, addr, zip_code)
        )
        if cur.rowcount > 0:
            done += 1
        else:
            cur = conn.execute(
                "UPDATE listings SET zestimate = ? WHERE url = ? AND zestimate IS NULL",
                (zest, url)
            )
            if cur.rowcount > 0:
                done += 1
            else:
                skip += 1

    conn.commit()
    conn.close()
    print(f"Backfilled {done} Zestimates ({skip} skipped/unmatched)")

if __name__ == "__main__":
    files = sys.argv[1:] or ["output/zillow_charlotte_50.json"]
    for f in files:
        if os.path.exists(f):
            print(f"Processing {f}...")
            backfill(f)
        else:
            print(f"File not found: {f}")
