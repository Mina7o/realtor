import re
import sys
from db import get_conn

SUFFIX_MAP = {
    "ST": "STREET", "DR": "DRIVE", "AVE": "AVENUE", "AV": "AVENUE",
    "RD": "ROAD", "LN": "LANE", "CT": "COURT",
    "CIR": "CIRCLE", "CR": "CIRCLE", "BLVD": "BOULEVARD",
    "PL": "PLACE", "PKWY": "PARKWAY", "HWY": "HIGHWAY",
    "TER": "TERRACE", "TR": "TRACE", "VW": "VIEW",
    "SQ": "SQUARE", "WAY": "WAY", "RUN": "RUN",
    "WALK": "WALK", "RIDGE": "RIDGE",
    "XING": "CROSSING", "CROSSING": "CROSSING",
}

SUFFIX_ABBR = {v: k for k, v in SUFFIX_MAP.items()}
SUFFIX_ABBR.update({k: k for k in SUFFIX_MAP})

DIR_MAP = {
    "N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST",
    "NO": "NORTH", "SO": "SOUTH", "EA": "EAST", "WE": "WEST",
}

def parse_street_parts(street_addr):
    street_addr = street_addr.strip().upper()
    street_addr = re.sub(r'[^\w\s]', ' ', street_addr)
    parts = street_addr.split()
    if not parts:
        return None, None, None, None

    # Extract street number (may have letter suffix like 9029J)
    num = None
    num_idx = 0
    m = re.match(r'^(\d+)[A-Z]?$', parts[0])
    if m:
        num = m.group(1)
        num_idx = 1
    elif parts[0].isdigit():
        num = parts[0]
        num_idx = 1
    # Handle hyphenated ranges like "433-68"
    elif '-' in parts[0] and parts[0].split('-')[0].isdigit():
        num = parts[0].split('-')[0]
        num_idx = 1

    # Extract remaining words
    remaining = parts[num_idx:]

    # Check for directional suffix at the end (e.g., "Lakeview Rd N")
    dir_suffix = None
    if remaining and remaining[-1] in DIR_MAP:
        dir_suffix = remaining[-1]
        remaining = remaining[:-1]

    # Check for suffix at end
    suffix = None
    if remaining and remaining[-1] in SUFFIX_MAP:
        suffix = remaining[-1]
        remaining = remaining[:-1]

    # Check for directional prefix
    direction = None
    if remaining and remaining[0] in DIR_MAP:
        direction = remaining[0]
        remaining = remaining[1:]

    # Add directional suffix back as part of base if no suffix was found
    if dir_suffix and not suffix:
        remaining.append(dir_suffix)

    base_name = " ".join(remaining) if remaining else ""
    return num, direction, base_name, suffix

def construct_county_key(num, direction, base, suffix):
    parts = [num] if num else []
    if direction:
        parts.append(DIR_MAP.get(direction, direction))
    if base:
        parts.append(base)
    if suffix:
        parts.append(SUFFIX_MAP.get(suffix, suffix))
    return " ".join(parts)

def normalize_for_match(text):
    text = text.strip().upper()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    for abbr, full in SUFFIX_MAP.items():
        text = re.sub(r'\b' + abbr + r'\b', full, text)
    for abbr, full in DIR_MAP.items():
        text = re.sub(r'\b' + abbr + r'\b', full, text)
    return text.strip()

def score_match(listing_parts, county_situs):
    """Score how well a listing matches a county situs address. Higher = better."""
    situs = normalize_for_match(county_situs)
    score = 0

    l_num, l_dir, l_base, l_suf = listing_parts
    l_key = construct_county_key(l_num, l_dir, l_base, l_suf)
    l_norm = normalize_for_match(l_key)

    # Exact match on normalized form
    if l_norm == situs:
        return 100

    # Street number match
    if l_num and l_num in situs:
        score += 30
    else:
        return 0  # Must have street number

    # Base name match
    if l_base and l_base in situs:
        score += 40
    else:
        return 0  # Must have base name

    # Suffix match
    if l_suf:
        full_suffix = SUFFIX_MAP.get(l_suf, l_suf)
        if full_suffix in situs or l_suf in situs:
            score += 20

    # Directional match
    if l_dir:
        full_dir = DIR_MAP.get(l_dir, l_dir)
        if full_dir in situs:
            score += 10

    return score

def match_listings():
    conn = get_conn()

    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listing_county_match (
            listing_id INTEGER PRIMARY KEY,
            property_id INTEGER,
            pid TEXT,
            situsaddress1 TEXT,
            match_score INTEGER,
            matched_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Count existing matches
    existing = conn.execute(
        "SELECT COUNT(*) FROM listing_county_match"
    ).fetchone()[0]
    if existing:
        print(f"Already have {existing} matches")
        yn = input("Re-match? (y/N): ")
        if yn.lower() != "y":
            print("Skipping")
            return

    # Get all listings with their properties
    listings = conn.execute("""
        SELECT l.id as listing_id, p.id as property_id,
               p.address, p.city, p.state
        FROM listings l
        JOIN properties p ON l.property_id = p.id
        WHERE p.state = 'NC'
        ORDER BY l.id
    """).fetchall()

    print(f"Matching {len(listings)} listings...")

    matched = 0
    unmatched = 0
    multi_match = 0

    for row in listings:
        lid = row["listing_id"]
        pid = row["property_id"]
        addr = row["address"]
        city = row["city"]
        state = row["state"]

        # Skip if already matched
        existing_match = conn.execute(
            "SELECT listing_id FROM listing_county_match WHERE listing_id=?",
            (lid,)
        ).fetchone()
        if existing_match:
            matched += 1
            continue

        # Parse listing address
        listing_parts = parse_street_parts(addr)
        l_num, l_dir, l_base, l_suf = listing_parts

        if not l_num or not l_base:
            print(f"  Cannot parse: {addr}")
            unmatched += 1
            continue

        # Build search pattern from street number + first word of base
        first_word = l_base.split()[0] if l_base else ""
        pattern = f"%{l_num}%{first_word}%"

        candidates = conn.execute("""
            SELECT pid, situsaddress1
            FROM mecklenburg_parcels
            WHERE situsaddress1 LIKE ?
              AND situsaddress1 LIKE ?
            ORDER BY situsaddress1
        """, (f"%{l_num}%", f"%{first_word}%")).fetchall()

        if not candidates:
            unmatched += 1
            continue

        # Score candidates
        scored = []
        for c in candidates:
            s = score_match(listing_parts, c["situsaddress1"])
            if s > 0:
                scored.append((s, c["pid"], c["situsaddress1"]))

        if not scored:
            unmatched += 1
            continue

        # Pick best match
        scored.sort(key=lambda x: -x[0])
        best = scored[0]

        if len(scored) > 1:
            multi_match += 1

        conn.execute(
            "INSERT OR REPLACE INTO listing_county_match "
            "(listing_id, property_id, pid, situsaddress1, match_score) "
            "VALUES (?, ?, ?, ?, ?)",
            (lid, pid, best[1], best[2], best[0])
        )
        conn.commit()
        matched += 1

        if matched % 50 == 0:
            print(f"  Progress: {matched} matched")

    conn.close()
    print(f"\nDone: {matched} matched, {unmatched} unmatched, {multi_match} had multiple candidates")

def analyze_results():
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            lcm.match_score,
            lcm.pid,
            lcm.situsaddress1 as county_addr,
            p.address as listing_addr,
            l.list_price,
            m.amt_totalvalue,
            m.full_owner_name,
            m.amt_price as last_sale_price,
            m.dte_dateofsale as last_sale_date,
            CASE
                WHEN m.txt_mailaddr1 IS NOT NULL
                 AND m.txt_city IS NOT NULL
                 AND (m.txt_city != p.city OR m.txt_state != p.state)
                THEN 1 ELSE 0
            END as absentee_flag
        FROM listing_county_match lcm
        JOIN listings l ON lcm.listing_id = l.id
        JOIN properties p ON l.property_id = p.id
        JOIN mecklenburg_parcels m ON lcm.pid = m.pid
        ORDER BY lcm.match_score DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        print(dict(r))
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "analyze":
        analyze_results()
    else:
        match_listings()
