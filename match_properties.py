"""Match listings to tax records by fixing parcel address normalization.
Fixes the root cause: parcel situs addresses include "CITY STATE" at end,
while listing addresses store city/state separately.

Usage:
  python3 match_properties.py           # dry run — show what would change
  python3 match_properties.py --apply   # actually update the database
"""
import argparse
import re
import sqlite3

from db import get_conn, normalize_address


def clean_parcel_address(address_str, known_cities=None):
    """Strip trailing CITY STATE from parcel situs address.
    e.g. '1000 WOODLAWN 203 CHARLOTTE NC' -> '1000 WOODLAWN 203'
    Uses known city names to avoid over-matching.
    """
    if known_cities is None:
        known_cities = []
    
    # Sort by length descending to match longer city names first
    cities_sorted = sorted(known_cities, key=len, reverse=True)
    
    for city in cities_sorted:
        # Match the city name followed by optional state (NC) at end
        pattern = re.escape(city) + r'\s+(?:NC)\s*$'
        cleaned = re.sub(pattern, '', address_str).strip()
        if cleaned != address_str:
            return re.sub(r'\s+', ' ', cleaned)
    
    # Fallback: strip any trailing uppercase word(s) + NC
    cleaned = re.sub(r'\s+[A-Z]{2,}\s+(?:NC)\s*$', '', address_str).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned


def find_matches(conn, apply=False):
    """Find and optionally fix parcel-derived properties with bad normalized_address."""
    # Collect known cities for smarter address cleaning
    known_cities = [r['city'] for r in conn.execute(
        "SELECT DISTINCT city FROM properties WHERE city IS NOT NULL AND city != '' AND state = 'NC'"
    ).fetchall()]
    
    cur = conn.execute("""
        SELECT p.id, p.address, p.city, p.state, p.zip, p.normalized_address
        FROM properties p
        JOIN tax_records t ON p.id = t.property_id
        GROUP BY p.id
        HAVING COUNT(t.id) > 0
        ORDER BY p.id
    """)
    parcel_props = cur.fetchall()

    fixed = 0
    merges = 0
    
    for pp in parcel_props:
        old_norm = pp['normalized_address']
        addr_part = old_norm.split('|')[0]
        
        # Check if address part still has city/state embedded
        needs_fix = False
        for city in known_cities:
            if re.search(r'\s+' + re.escape(city) + r'\s+(?:NC)\s*$', addr_part):
                needs_fix = True
                break
        
        if needs_fix:
            # Fix: strip city/state from address part
            clean_addr = clean_parcel_address(pp['address'], known_cities)
            new_norm = normalize_address(clean_addr, pp['city'], pp['state'], pp['zip'])
            
            if new_norm != old_norm:
                if apply:
                    existing = conn.execute(
                        "SELECT id FROM properties WHERE normalized_address = ? AND id != ?",
                        (new_norm, pp['id'])
                    ).fetchone()
                    
                    if existing:
                        merge_properties(conn, src_id=pp['id'], dst_id=existing['id'])
                        merges += 1
                        print(f"  MERGE: id={pp['id']} -> id={existing['id']}  ({old_norm[:50]} -> {new_norm[:50]})")
                    else:
                        conn.execute(
                            "UPDATE properties SET normalized_address = ? WHERE id = ?",
                            (new_norm, pp['id'])
                        )
                        print(f"  FIX: id={pp['id']}  ({clean_addr[:45]:45s} -> {new_norm})")
                        fixed += 1
                else:
                    print(f"  WOULD FIX: id={pp['id']}  ({old_norm[:55]} -> {new_norm[:55]})")
    
    return fixed, merges


def merge_properties(conn, src_id, dst_id):
    """Move tax_records and listings from src property to dst, then delete src."""
    # Move tax records (avoid duplicate)
    conn.execute("""
        UPDATE OR IGNORE tax_records SET property_id = ?
        WHERE property_id = ?
    """, (dst_id, src_id))
    
    # Move any remaining unmatched tax records (INSERT OR IGNORE might skip duplicates, 
    # so move what's left)
    conn.execute("""
        UPDATE OR IGNORE listings SET property_id = ?
        WHERE property_id = ?
    """, (dst_id, src_id))
    
    # Delete the now-orphaned source property
    conn.execute("DELETE FROM properties WHERE id = ?", (src_id,))


def main():
    parser = argparse.ArgumentParser(description="Match listings to tax records")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    args = parser.parse_args()

    mode = "DRY RUN" if not args.apply else "APPLY"
    print(f"\n=== Property Matching Pipeline ({mode}) ===\n")
    
    conn = get_conn()
    fixed, merges = find_matches(conn, apply=args.apply)
    
    if args.apply:
        conn.commit()
        print(f"\nApplied: {fixed} addresses fixed, {merges} properties merged")
    else:
        print(f"\nFound {fixed} addresses to fix, {merges} properties to merge")
        print("(Totals: %d parcel-derived properties checked)" % len(conn.execute(
            "SELECT COUNT(DISTINCT p.id) FROM properties p JOIN tax_records t ON p.id = t.property_id"
        ).fetchone()[0]))
        print("Run with --apply to execute.")
    
    conn.close()


if __name__ == "__main__":
    main()
