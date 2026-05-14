"""
Find deals where list price is within X% of tax assessed value.
Outputs results as CSV and/or prints to console.
"""
import argparse
import csv
import os
from db import get_conn, find_deals

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'output')

def show_deals(diff_pct=15, output_csv=None):
    results = find_deals(diff_pct)
    if not results:
        print(f"No deals found within {diff_pct}% of tax value.")
        print("Make sure you have loaded both tax records AND listings into the database.")
        return

    print(f"\n{'='*100}")
    print(f"DEALS: Properties within {diff_pct}% of list price to tax value")
    print(f"Found {len(results)} properties")
    print(f"{'='*100}\n")

    header = f"{'Address':45s} {'City':15s} {'County':15s} {'Tax Value':12s} {'List Price':12s} {'Diff%':8s} {'Yr':6s} {'SqFt':7s} {'Bd':4s} {'Ba':4s}"
    print(header)
    print('-' * len(header))
    for r in results:
        print(f"{r['address']:45s} {r['city']:15s} {r['county']:15s} "
              f"${r['tax_value']:>9,.0f} ${r['list_price']:>9,.0f} "
              f"{r['diff_pct']:>7.2f}% "
              f"{r['year_built'] or 0:>6d} {r['sqft'] or 0:>7.0f} "
              f"{r['bedrooms'] or 0:>4d} {r['bathrooms'] or 0:>4.1f}")

    if output_csv:
        filepath = os.path.join(OUTPUT_DIR, output_csv)
        with open(filepath, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)
        print(f"\nSaved to {filepath}")

    # Summary stats
    diffs = [abs(r['diff_pct']) for r in results if r['diff_pct'] is not None]
    if diffs:
        print(f"\nSummary:")
        print(f"  Total properties: {len(results)}")
        print(f"  Avg |diff|: {sum(diffs)/len(diffs):.2f}%")
        print(f"  Min |diff|: {min(diffs):.2f}%")
        print(f"  Max |diff|: {max(diffs):.2f}%")
        below = sum(1 for r in results if r['diff_pct'] is not None and r['diff_pct'] < 0)
        above = sum(1 for r in results if r['diff_pct'] is not None and r['diff_pct'] > 0)
        print(f"  Listed below tax value: {below}")
        print(f"  Listed above tax value: {above}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", type=float, default=15, help="Max % difference (default: 15)")
    parser.add_argument("--csv", help="Output CSV filename (saved to output/)")
    args = parser.parse_args()

    show_deals(diff_pct=args.diff, output_csv=args.csv)

if __name__ == "__main__":
    main()
