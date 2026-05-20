"""
Find deals: compare list prices to unified market value.

Matches listings to assessed/Zestimate values and computes deal tiers.

Usage:
  python3 find_deals.py                          # all matched, sorted by deal
  python3 find_deals.py --min-discount 5          # only 5%+ below market value
  python3 find_deals.py --csv deals.csv           # save to CSV
"""
import argparse
import csv
import json
import os
from db import get_conn

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def find_deals(conn, min_discount=None, max_premium=None):
    """Return one row per property (best price), deduplicated by property_id."""
    where_clauses = ["1=1"]
    if min_discount is not None:
        where_clauses.append(f"""
            d.best_price <= d.market_value * (1 - {min_discount / 100.0})
        """)
    if max_premium is not None:
        where_clauses.append(f"""
            d.best_price <= d.market_value * (1 + {max_premium / 100.0})
        """)

    where = " AND ".join(where_clauses)

    return conn.execute(f"""
        SELECT
            d.*,
            COALESCE(ph_stats.price_changes, 0) as price_changes,
            COALESCE(ph_stats.price_drops, 0) as price_drops,
            ROUND((d.best_price - d.market_value) * 100.0 / NULLIF(d.market_value, 0), 2) as diff_pct,
            CASE WHEN d.best_price < d.market_value THEN 'UNDERVALUED'
                 WHEN d.best_price <= d.market_value * 1.05 THEN 'AT_VALUE'
                 WHEN d.best_price <= d.market_value * 1.20 THEN 'PREMIUM'
                 ELSE 'OVERPRICED'
            END as deal_tier
        FROM (
            SELECT
                p.id as property_id,
                p.address, p.city, p.state, p.zip,
                p.lat, p.lng,
                MIN(l.list_price) as best_price,
                MAX(l.zestimate) as zestimate,
                MAX(t.mkt_val_total) as tax_value,
                MAX(a.assessed_value) as attom_assessed,
                MAX(a.avm_value) as attom_avm,
                MAX(mp.amt_totalvalue) as county_assessed,
                MAX(up.fmv_total) as union_assessed,
                MAX(yp.apr_tot_val) as york_assessed,
                MAX(wp.total_value_assd) as wake_assessed,
                COALESCE(MAX(t.mkt_val_total), MAX(mp.amt_totalvalue), MAX(up.fmv_total), MAX(yp.apr_tot_val), MAX(wp.total_value_assd), MAX(a.assessed_value), MAX(l.zestimate)) as market_value,
                CASE
                    WHEN MAX(t.mkt_val_total) > 0 THEN 'tax'
                    WHEN MAX(mp.amt_totalvalue) > 0 THEN 'county'
                    WHEN MAX(up.fmv_total) > 0 THEN 'union'
                    WHEN MAX(yp.apr_tot_val) > 0 THEN 'york'
                    WHEN MAX(wp.total_value_assd) > 0 THEN 'wake'
                    WHEN MAX(a.assessed_value) > 0 THEN 'attom'
                    WHEN MAX(l.zestimate) > 0 THEN 'zest'
                    ELSE NULL
                END as value_source,
                COALESCE(MAX(a.owner_name), MAX(mp.full_owner_name), MAX(up.curr_name1), MAX(yp.owner1), MAX(wp.owner)) as owner_name,
                MAX(a.corporate_indicator) as corporate_indicator,
                CASE
                    WHEN MAX(CASE WHEN a.absentee_status IN ('A', 'ABSENTEE(MAIL AND SITUS NOT =)') THEN 1 ELSE 0 END) = 1 THEN 'YES'
                    WHEN MAX(CASE WHEN mp.txt_mailaddr1 IS NOT NULL AND (
                        UPPER(mp.txt_city) != UPPER(p.city) OR UPPER(mp.txt_state) != UPPER(p.state)
                    ) THEN 1 ELSE 0 END) = 1 THEN 'YES'
                    WHEN MAX(CASE WHEN up.curr_city IS NOT NULL AND (
                        UPPER(up.curr_city) != UPPER(p.city) OR UPPER(up.curr_state) != UPPER(p.state)
                    ) THEN 1 ELSE 0 END) = 1 THEN 'YES'
                    WHEN MAX(CASE WHEN yp.mail_city IS NOT NULL AND (
                        UPPER(yp.mail_city) != UPPER(p.city) OR UPPER(yp.mail_state) != UPPER(p.state)
                    ) THEN 1 ELSE 0 END) = 1 THEN 'YES'
                    WHEN MAX(CASE WHEN wp.addr1 IS NOT NULL AND UPPER(wp.addr1) != UPPER(p.address) THEN 1 ELSE 0 END) = 1 THEN 'YES'
                    ELSE 'NO'
                END as absentee_flag,
                MAX(mp.txt_mailaddr1) as county_mail_addr,
                MAX(mp.txt_city) as county_mail_city,
                MAX(mp.txt_state) as county_mail_state,
                MAX(wp.addr1) as wake_mail_addr,
                MAX(wp.addr2) as wake_mail_addr2,
                MAX(wp.addr3) as wake_mail_addr3,
                MAX(mp.amt_price) as county_last_sale_price,
                MAX(mp.dte_dateofsale) as county_last_sale_date,
                MAX(t.sale_price) as tax_last_sale_price,
                MAX(t.sale_date) as tax_last_sale_date,
                COALESCE(MAX(t.year_built), MAX(wp.year_built)) as year_built,
                COALESCE(MAX(t.sqft), MAX(wp.heatedarea)) as sqft,
                MAX(t.bedrooms) as bedrooms,
                MAX(t.bathrooms) as bathrooms,
                MAX(t.land_use) as land_use,
                MAX(mp.txt_propertyuse_desc) as county_property_use,
                MAX(mp.num_totalac) as county_acres,
                MAX(up.mapped_acres) as union_acres,
                MAX(yp.gis_acres) as york_acres,
                MAX(wp.deed_acres) as wake_acres,
                MAX(up.sqft) as union_sqft,
                MAX(yp.finished_sqft) as york_sqft,
                MAX(wp.heatedarea) as wake_sqft,
                MAX(a.property_type) as attom_prop_type,
                MAX(a.quality) as quality,
                MAX(a.condition) as condition,
                MAX(a.lot_acres) as attom_acres
            FROM properties p
            JOIN listings l ON p.id = l.property_id
            LEFT JOIN tax_records t ON p.id = t.property_id AND t.mkt_val_total > 0
            LEFT JOIN attom_cache a ON p.id = a.property_id
            LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
            LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
            LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
            LEFT JOIN union_parcels up ON ucm.pid = up.pid
            LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
            LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
            LEFT JOIN listing_county_wake wcm ON l.id = wcm.listing_id
            LEFT JOIN wake_parcels wp ON wcm.pin_num = wp.pin_num
            GROUP BY p.id
        ) d
        LEFT JOIN (
            SELECT listing_id,
                   COUNT(*) as price_changes,
                   SUM(CASE WHEN new_price < old_price THEN 1 ELSE 0 END) as price_drops
            FROM price_history
            GROUP BY listing_id
        ) ph_stats ON ph_stats.listing_id IN (
            SELECT id FROM listings WHERE property_id = d.property_id
        )
        WHERE d.market_value > 0 AND {where}
        ORDER BY (d.best_price - d.market_value) * 100.0 / NULLIF(d.market_value, 0) ASC
    """).fetchall()


def show_recent_drops(conn, limit=30):
    rows = conn.execute("""
        SELECT p.address, p.city, p.state,
               ph.old_price, ph.new_price, ph.detected_at,
               COALESCE(a.owner_name, mp.full_owner_name, up.curr_name1, yp.owner1, wp.owner) as owner_name,
               (SELECT COUNT(*) FROM price_history WHERE listing_id = l.id) as total_changes,
               (SELECT COUNT(*) FROM price_history WHERE listing_id = l.id AND new_price < old_price) as total_drops
        FROM price_history ph
        JOIN listings l ON ph.listing_id = l.id
        JOIN properties p ON l.property_id = p.id
        LEFT JOIN attom_cache a ON p.id = a.property_id
        LEFT JOIN listing_county_match cm ON l.id = cm.listing_id
        LEFT JOIN mecklenburg_parcels mp ON cm.pid = mp.pid
        LEFT JOIN listing_county_union ucm ON l.id = ucm.listing_id
        LEFT JOIN union_parcels up ON ucm.pid = up.pid
        LEFT JOIN listing_county_york ycm ON l.id = ycm.listing_id
        LEFT JOIN york_parcels yp ON ycm.pid = yp.objectid
        LEFT JOIN listing_county_wake wcm ON l.id = wcm.listing_id
        LEFT JOIN wake_parcels wp ON wcm.pin_num = wp.pin_num
        WHERE ph.new_price < ph.old_price
        ORDER BY ph.detected_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return

    print(f"\n{'=' * 130}")
    print(f"  RECENT PRICE DROPS")
    print(f"{'=' * 130}\n")
    print(f"  {'Address':38s} {'City':16s} {'Old Price':>9s} {'New Price':>9s} {'Drop':>14s} {'Date':10s}  {'Times':<7s} {'Owner':24s}")
    print(f"  {'-'*38} {'-'*16} {'-'*9} {'-'*9} {'-'*14} {'-'*10}  {'-'*7} {'-'*24}")
    for r in rows:
        addr = (r['address'] or "")[:36]
        city = (r['city'] or "")[:14]
        old_p = r['old_price'] or 0
        new_p = r['new_price'] or 0
        drop = old_p - new_p
        pct = drop / old_p * 100 if old_p else 0
        drop_str = f"-${drop:,.0f} ({-pct:.0f}%)"
        date_str = (r['detected_at'] or "")[:10]
        owner = (r['owner_name'] or "")[:22]
        t = r['total_changes'] or 0
        d = r['total_drops'] or 0
        times = f"\u2195{t}/\u2193{d}" if t else ''
        print(f"  {addr:36s}  {city:14s}  ${old_p:>8,.0f}  ${new_p:>8,.0f}  {drop_str:>14s}  {date_str:10s}  {times:<7s} {owner:22s}")
    print()


def show_deals(rows, min_discount, max_premium):
    if not rows:
        print("No deals found.")
        return

    undervalued = [r for r in rows if r['diff_pct'] < 0]
    at_value = [r for r in rows if 0 <= r['diff_pct'] <= 5]
    premium = [r for r in rows if 5 < r['diff_pct'] <= 20]
    overpriced = [r for r in rows if r['diff_pct'] > 20]

    absentee = [r for r in rows if r['absentee_flag'] == 'YES']
    corporate = [r for r in rows if r['corporate_indicator'] == 'Y']

    print(f"\n{'=' * 110}")
    title = "DEAL FINDER"
    if min_discount:
        title += f" (min {min_discount}% below market value)"
    print(f"  {title}")
    print(f"  Properties with value: {len(rows)} total")
    print(f"  Undervalued: {len(undervalued)}  |  At value: {len(at_value)}  |  "
          f"Premium: {len(premium)}  |  Overpriced: {len(overpriced)}")
    print(f"  Absentee owners: {len(absentee)}  |  Corporate owners: {len(corporate)}")
    print(f"{'=' * 110}\n")

    def print_group(label, items):
        if not items:
            return
        print(f"  {label} ({len(items)}):")
        print(f"  {'Address':42s} {'Price':>9s} {'Mkt Val':>9s} {'Diff':>7s} "
              f"{'Src':<5s} {'Chg':<7s} {'Owner':25s} {'Flags':15s}")
        print(f"  {'-'*42} {'-'*9} {'-'*9} {'-'*7} {'-'*5} {'-'*7} {'-'*25} {'-'*15}")
        for r in items:
            addr = (r['address'] or "")[:40]
            owner = (r['owner_name'] or "")[:23]
            chg = f"↕{r['price_changes']}/↓{r['price_drops']}" if r['price_changes'] else ''
            flags_parts = []
            if r['absentee_flag'] == 'YES': flags_parts.append('ABSENTEE')
            if r['corporate_indicator'] == 'Y': flags_parts.append('CORP')
            flags = '|'.join(flags_parts) if flags_parts else ''
            print(f"  {addr:40s} ${r['best_price']:>8,.0f} ${r['market_value']:>8,.0f} "
                  f"{r['diff_pct']:>+6.1f}% {r['value_source']:<5s} {chg:<7s} {owner:25s} {flags:15s}")
        print()

    print_group("UNDERVALUED (list < market)", undervalued)
    print_group("AT VALUE (0-5% above)", at_value)
    print_group("PREMIUM (5-20% above)", premium)
    print_group("OVERPRICED (20%+ above)", overpriced)

    if absentee:
        print(f"\n  ABSENTEE OWNERS ({len(absentee)}):")
        print(f"  {'Address':42s} {'Price':>9s} {'Mrkt Val':>9s} {'Chg':<7s} {'Owner':25s} {'Mails To':30s}")
        print(f"  {'-'*42} {'-'*9} {'-'*9} {'-'*7} {'-'*25} {'-'*30}")
        for r in absentee[:20]:
            addr = (r['address'] or "")[:40]
            owner = (r['owner_name'] or "")[:23]
            chg = f"↕{r['price_changes']}/↓{r['price_drops']}" if r['price_changes'] else ''
            mail_city = f"{r['county_mail_city'] or ''}, {r['county_mail_state'] or ''}"
            wake_mail = f"{r['wake_mail_addr'] or ''} {r['wake_mail_addr2'] or ''} {r['wake_mail_addr3'] or ''}".strip()
            mailbox = mail_city or wake_mail or ''
            print(f"  {addr:40s} ${r['best_price']:>8,.0f} ${r['market_value']:>8,.0f} "
                  f"{chg:<7s} {owner:25s} {mailbox:30s}")
        if len(absentee) > 20:
            print(f"  ... and {len(absentee) - 20} more")


def save_csv(rows, filepath):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    if not rows:
        return
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in dict(rows[0]).keys()])
        w.writeheader()
        w.writerows(dict(r) for r in rows)
    print(f"Saved {len(rows)} deals to {filepath}")


def save_json(rows, filepath):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w") as f:
        json.dump([dict(r) for r in rows], f, indent=2, default=str)
    print(f"Saved {len(rows)} deals to {filepath}")


def main():
    parser = argparse.ArgumentParser(description="Find real estate deals")
    parser.add_argument("--min-discount", type=float, default=None,
                        help="Minimum % below market value (e.g. 5 = 5% below)")
    parser.add_argument("--max-premium", type=float, default=None,
                        help="Max % above market value (e.g. 20 = up to 20% above)")
    parser.add_argument("--csv", help="Output CSV path (relative to output/)")
    parser.add_argument("--json", help="Output JSON path (relative to output/)")
    parser.add_argument("--drops", type=int, nargs="?", const=30, default=20,
                        help="Show recent price drops (default 20)")
    args = parser.parse_args()

    conn = get_conn()
    rows = find_deals(conn, min_discount=args.min_discount, max_premium=args.max_premium)

    show_deals(rows, args.min_discount, args.max_premium)
    show_recent_drops(conn, limit=args.drops)

    if args.csv:
        save_csv(rows, os.path.join(OUTPUT_DIR, args.csv))
    if args.json:
        save_json(rows, os.path.join(OUTPUT_DIR, args.json))

    conn.close()


if __name__ == "__main__":
    main()
