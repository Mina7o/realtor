"""
Load county tax/parcel CSV data into the database.
Supports flexible column mapping via CLI arguments.

Usage:
  python load_county_tax.py --file mecklenburg_parcels.csv --county "Mecklenburg" --state NC \
    --col-address SITEADDR --col-city SITECITY --col-state SITESTATE --col-zip SITEZIP \
    --col-tax-value MKTVALTOT --col-land-value MKTVALLAND --col-building-value MKTVALBLDG \
    --col-year-built YEARBUILT --col-sqft BUILDINGSQFT --col-bedrooms BEDROOMS \
    --col-bathrooms FULLBATH --col-sale-price SALEPRICE --col-sale-date SALEDATE
"""
import argparse
import csv
import os
import sys
from db import get_conn, upsert_county, upsert_property, upsert_tax_record

def parse_float(v):
    if v is None:
        return None
    try:
        return float(v.replace('$', '').replace(',', '').strip())
    except (ValueError, AttributeError):
        return None

def parse_int(v):
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, AttributeError):
        return None

def load_csv(filepath, county_name, state, col_map, tax_year, delimiter=','):
    county_id = upsert_county(county_name, state)
    conn = get_conn()
    count = 0
    errors = 0

    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            try:
                addr = row.get(col_map.get('address', ''), '').strip()
                city = row.get(col_map.get('city', ''), '').strip()
                st = row.get(col_map.get('state', ''), '').strip() or state
                zip_code = row.get(col_map.get('zip', ''), '').strip()

                if not addr or not city:
                    errors += 1
                    continue

                pid = upsert_property(
                    address=addr,
                    city=city,
                    state=st,
                    zip_code=zip_code[:5] if zip_code else None,
                    county_id=county_id
                )

                upsert_tax_record(
                    property_id=pid,
                    tax_year=tax_year,
                    mkt_val_total=parse_float(row.get(col_map.get('tax_value', ''))),
                    mkt_val_land=parse_float(row.get(col_map.get('land_value', ''))),
                    mkt_val_building=parse_float(row.get(col_map.get('building_value', ''))),
                    sale_price=parse_float(row.get(col_map.get('sale_price', ''))),
                    sale_date=row.get(col_map.get('sale_date', ''), '').strip() or None,
                    year_built=parse_int(row.get(col_map.get('year_built', ''))),
                    sqft=parse_float(row.get(col_map.get('sqft', ''))),
                    bedrooms=parse_int(row.get(col_map.get('bedrooms', ''))),
                    bathrooms=parse_float(row.get(col_map.get('bathrooms', ''))),
                )
                count += 1
            except Exception as e:
                errors += 1

    print(f"Loaded {count} tax records for {county_name} County, {state} ({errors} errors)")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="CSV file path")
    parser.add_argument("--county", required=True, help="County name")
    parser.add_argument("--state", default="NC", help="State code")
    parser.add_argument("--tax-year", type=int, default=2025, help="Tax year")
    parser.add_argument("--delimiter", default=",", help="CSV delimiter")

    # Column mapping
    parser.add_argument("--col-address", default="address", help="Street address column")
    parser.add_argument("--col-city", default="city", help="City column")
    parser.add_argument("--col-state", default="state", help="State column")
    parser.add_argument("--col-zip", default="zip", help="ZIP code column")
    parser.add_argument("--col-tax-value", default="mkt_val_total", help="Total market value column")
    parser.add_argument("--col-land-value", default="mkt_val_land", help="Land value column")
    parser.add_argument("--col-building-value", default="mkt_val_building", help="Building value column")
    parser.add_argument("--col-sale-price", default="sale_price", help="Last sale price column")
    parser.add_argument("--col-sale-date", default="sale_date", help="Last sale date column")
    parser.add_argument("--col-year-built", default="year_built", help="Year built column")
    parser.add_argument("--col-sqft", default="sqft", help="Square footage column")
    parser.add_argument("--col-bedrooms", default="bedrooms", help="Bedrooms column")
    parser.add_argument("--col-bathrooms", default="bathrooms", help="Bathrooms column")

    args = parser.parse_args()

    col_map = {
        'address': args.col_address,
        'city': args.col_city,
        'state': args.col_state,
        'zip': args.col_zip,
        'tax_value': args.col_tax_value,
        'land_value': args.col_land_value,
        'building_value': args.col_building_value,
        'sale_price': args.col_sale_price,
        'sale_date': args.col_sale_date,
        'year_built': args.col_year_built,
        'sqft': args.col_sqft,
        'bedrooms': args.col_bedrooms,
        'bathrooms': args.col_bathrooms,
    }

    load_csv(args.file, args.county, args.state, col_map, args.tax_year, args.delimiter)

if __name__ == "__main__":
    main()
