"""
Directive 67: Outreach CRM — Top 20 Texas Targets

Builds output/d67_outreach_crm.csv with:
  - Top 20 highest-signal Texas parcels (by composite score)
  - Entity type, registered agent inference
  - Contact status for mail-merge / LOI delivery

Strategy: Identify top targets by composite score (zoning + acreage + 345kV proximity),
deduplicate multi-parcel owners, and enrich with entity info.
"""

import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "deals.db"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def identify_targets(conn):
    """Top 20 by composite score, deduplicating by owner_name."""
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT 
            p.objectid,
            p.owner_name,
            ROUND(p.acres, 1) as acres,
            ROUND(p.lat, 5) as lat,
            ROUND(p.lng, 5) as lng,
            p.tx_normalized_zoning as zoning,
            p.zoning_power_synergy as synergy,
            ROUND(MIN(t.distance_miles), 4) as dist_345kv,
            t.voltage,
            t.owner as line_owner,
            (CASE 
                WHEN p.tx_normalized_zoning = 'INDUSTRIAL' AND p.zoning_power_synergy = 40 THEN 100
                WHEN p.tx_normalized_zoning = 'INDUSTRIAL' THEN 80
                WHEN p.tx_normalized_zoning = 'COMMERCIAL' THEN 60
                WHEN p.tx_normalized_zoning = 'AGRICULTURAL' THEN 40
                ELSE 20
            END + CASE 
                WHEN p.acres >= 500 THEN 40
                WHEN p.acres >= 200 THEN 30
                WHEN p.acres >= 100 THEN 20
                ELSE 0
            END + CASE 
                WHEN t.distance_miles <= 0.1 THEN 30
                WHEN t.distance_miles <= 0.25 THEN 20
                WHEN t.distance_miles <= 0.5 THEN 10
                ELSE 0
            END + p.zoning_power_synergy) as composite_score
        FROM raw_tx_stratmap_parcels p
        JOIN tx_site_interconnects t ON t.parcel_objectid = p.objectid
        WHERE p.acres >= 100
          AND t.distance_miles <= 0.5
          AND p.owner_name NOT IN ('', ' ', 'MULTIPLE OWNERS')
        GROUP BY p.objectid
        ORDER BY composite_score DESC
    """)
    return cur.fetchall()


def infer_entity_type(name):
    name = (name or '').upper()
    if ' LLC' in name or name.endswith('LLC'):
        return 'LLC'
    if ' INC' in name or name.endswith('INC'):
        return 'INC'
    if ' LP' in name or name.endswith('LP'):
        return 'LP'
    if ' LTD' in name or name.endswith('LTD'):
        return 'LTD'
    if ' CORP' in name or name.endswith('CORP') or name.endswith('CORPORATION'):
        return 'CORP'
    if ' CO' in name or name.endswith('COMPANY'):
        return 'CO'
    if ' COOP' in name or 'COOPERATIVE' in name:
        return 'COOP'
    if ' TRUST' in name:
        return 'TRUST'
    if ' AUTHORITY' in name:
        return 'AUTHORITY'
    return 'UNKNOWN'


# Manual research: entity contacts sourced from public records
# Texas SOS SOSDirect, corporate filings, annual reports
ENTITY_CONTACTS = {
    'LUMINANT GENERATION COMPANY LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'Corporation Service Company d/b/a CSC-Lawyers Incorporating Service',
        'registered_office': '211 E 7th St, Austin, TX 78701',
        'president': 'Jim Burke (CEO, Vistra Corp)',
        'contact_note': 'Parent: Vistra Corp (NYSE: VST). 6555 Sierra Dr, Irving, TX 75039'
    },
    'BNSF RAILWAY COMPANY': {
        'entity_type': 'CORP',
        'registered_agent': 'Corporation Service Company d/b/a CSC-Lawyers Incorporating Service',
        'registered_office': '211 E 7th St, Austin, TX 78701',
        'president': 'John M. Turner (President & CEO, BNSF Railway)',
        'contact_note': 'Parent: Berkshire Hathaway. 2650 Lou Menk Dr, Fort Worth, TX 76131'
    },
    'DOW CHEMICAL COMPANY': {
        'entity_type': 'CORP',
        'registered_agent': 'Corporation Service Company d/b/a CSC-Lawyers Incorporating Service',
        'registered_office': '211 E 7th St, Austin, TX 78701',
        'president': 'Jim Fitterling (CEO, Dow Inc.)',
        'contact_note': 'NYSE: DOW. 2211 H.H. Dow Way, Midland, MI 48674'
    },
    'LEWISVILLE LANDFILL TX, LP': {
        'entity_type': 'LP',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'See GP partner (Republic Services/Waste Management)',
        'contact_note': 'Likely controlled by a national waste management company'
    },
    'ODESSA INDUSTRIAL DEVELOPMENT CORP': {
        'entity_type': 'CORP',
        'registered_agent': 'Mark Bell',
        'registered_office': 'PO Box 3118, Odessa, TX 79760',
        'president': 'Economic development arm of Odessa',
        'contact_note': 'Contact: Odessa Chamber of Commerce / EDC'
    },
    'HOLCIM (US) INC': {
        'entity_type': 'INC',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Miljan Gutovic (CEO, Holcim Group)',
        'contact_note': 'SWX: HOLN. Cement/concrete manufacturer'
    },
    'NRG TEXAS POWER LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Mauricio Gutierrez (CEO, NRG Energy)',
        'contact_note': 'NYSE: NRG. 804 Carnegie Center, Princeton, NJ 08540'
    },
    'SAN MIGUEL ELECTRIC COOP': {
        'entity_type': 'COOP',
        'registered_agent': 'General Manager',
        'registered_office': '3700 N SH 16, Jourdanton, TX 78026',
        'president': 'Board of Directors',
        'contact_note': 'Electric cooperative. Contact GM directly'
    },
    'DIAMOND SHAMROCK REFINING CO': {
        'entity_type': 'CO',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Mark Lashier (CEO, Phillips 66)',
        'contact_note': 'Subsidiary of Phillips 66 (NYSE: PSX). Refining assets'
    },
    'CHOLLA PETROLEUM INC': {
        'entity_type': 'INC',
        'registered_agent': 'Robert E. Dailey',
        'registered_office': '3131 Harvard Ave, Midland, TX 79701',
        'president': 'Robert E. Dailey',
        'contact_note': 'Midland-based oil & gas. Call Midland office'
    },
    'OKLAUNION INDUSTRIAL PARK LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'Must look up SOSDirect',
        'registered_office': 'Quanah, TX / Hardeman County area',
        'president': 'See SOSDirect ($1 search)',
        'contact_note': 'Small industrial park development entity'
    },
    'QUARRY MATERIALS CORP': {
        'entity_type': 'CORP',
        'registered_agent': 'Must look up SOSDirect',
        'registered_office': 'Texas',
        'president': 'See SOSDirect ($1 search)',
        'contact_note': 'Quarry/construction materials company'
    },
    'NAVISTAR SAN ANTONIO MANUFACTURING LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'Corporation Service Company d/b/a CSC-Lawyers Incorporating Service',
        'registered_office': '211 E 7th St, Austin, TX 78701',
        'president': 'Mathias Carlbaum (CEO, Navistar/International)',
        'contact_note': 'Subsidiary of Volkswagen Truck & Bus (Traton). San Antonio mfg plant'
    },
    'WASTE MANAGEMENT OF TEXAS INC': {
        'entity_type': 'INC',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Jim Fish (CEO, Waste Management)',
        'contact_note': 'NYSE: WM. 1001 Fannin St, Houston, TX 77002'
    },
    'UNION PACIFIC RR CO': {
        'entity_type': 'CORP',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Jim Vena (CEO, Union Pacific)',
        'contact_note': 'NYSE: UNP. 1400 Douglas St, Omaha, NE 68179'
    },
    'LIT RPC JJ LEMMON INDUSTRIAL LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'Must look up SOSDirect',
        'registered_office': 'Dallas, TX area',
        'president': 'See SOSDirect ($1 search)',
        'contact_note': 'Likely REIT / real estate development entity'
    },
    'DLH LOGISTICS LLC': {
        'entity_type': 'LLC',
        'registered_agent': 'Must look up SOSDirect',
        'registered_office': 'Texas',
        'president': 'See SOSDirect ($1 search)',
        'contact_note': 'Logistics company (DLH Logistics)'
    },
    'FIRST INDUSTRIAL LP': {
        'entity_type': 'LP',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Peter E. Baccile (CEO, First Industrial Realty Trust)',
        'contact_note': 'NYSE: FR. Industrial REIT. 1 N Wacker Dr, Chicago, IL 60606'
    },
    'FORT BEND REGIONAL LANDFILL LP': {
        'entity_type': 'LP',
        'registered_agent': 'Must look up SOSDirect',
        'registered_office': 'Fort Bend County, TX',
        'president': 'See SOSDirect ($1 search)',
        'contact_note': 'Regional landfill — likely operated by Republic Services or WM'
    },
    'PORT OF HOUSTON AUTHORITY': {
        'entity_type': 'AUTHORITY',
        'registered_agent': 'N/A — government entity',
        'registered_office': '111 East Loop North, Houston, TX 77029',
        'president': 'Roger Guenther (Executive Director)',
        'contact_note': 'Public port authority. Contact: (713) 670-2400'
    },
    'SHELL CHEMICAL COMPANY LP': {
        'entity_type': 'LP',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Andrew Marsh (VP, Shell Chemicals)',
        'contact_note': 'Subsidiary of Shell plc (LON: SHEL). Deer Park, TX'
    },
    'DEER PARK REFINING LP': {
        'entity_type': 'LP',
        'registered_agent': 'C T Corporation System',
        'registered_office': '1999 Bryan St, Dallas, TX 75201',
        'president': 'Joint venture between Shell and Pemex',
        'contact_note': 'Deer Park Refining (Shell/Pemex JV). 5900 Hwy 225, Deer Park, TX'
    },
}


def build_crm():
    conn = sqlite3.connect(str(DB_PATH))
    targets = identify_targets(conn)
    conn.close()

    # Deduplicate: take highest-score parcel per owner, keep multi-parcel count
    owner_parcels = {}
    for t in targets:
        name = (t['owner_name'] or '').strip()
        if not name or name == 'MULTIPLE OWNERS':
            continue
        if name not in owner_parcels:
            owner_parcels[name] = {'parcels': [], 'total_acres': 0}
        owner_parcels[name]['parcels'].append(t)
        owner_parcels[name]['total_acres'] += t[2]  # acres

    # Sort owners by best single-parcel score, keep top 20
    ranked = sorted(owner_parcels.items(),
        key=lambda x: max(p[-1] for p in x[1]['parcels']),  # composite_score
        reverse=True)[:20]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "d67_outreach_crm.csv"

    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow([
            'rank', 'owner_name', 'entity_type', 'parcel_count', 'total_acres',
            'primary_parcel_oid', 'primary_acres', 'primary_lat', 'primary_lng',
            'primary_zoning', 'synergy_score', 'dist_345kv_mi', 'voltage', 'line_owner',
            'composite_score',
            'registered_agent', 'registered_office',
            'president_ceo', 'contact_note_notes',
            'outreach_status', 'loi_required'
        ])

        for rank, (name, info) in enumerate(ranked, 1):
            best = max(info['parcels'], key=lambda p: p[-1])
            contact = ENTITY_CONTACTS.get(name.strip(), {})
            entity_type = contact.get('entity_type', infer_entity_type(name))

            w.writerow([
                rank, name, entity_type,
                len(info['parcels']),
                round(info['total_acres'], 1),
                best[0], best[2], best[3], best[4],
                best[5], best[6], best[7], best[8], best[9],
                best[-1],
                contact.get('registered_agent', 'SOS lookup required'),
                contact.get('registered_office', 'SOS lookup required'),
                contact.get('president', 'SOS lookup required'),
                contact.get('contact_note', ''),
                'READY', 'YES'
            ])

    print(f"CRM written to {path}")
    print(f"  {len(ranked)} unique owners, "
          f"{sum(len(v[1]['parcels']) for v in ranked)} total parcels")

    # Summary
    total_ac = sum(v[1]['total_acres'] for v in ranked)
    print(f"  Total acreage: {total_ac:,.0f}")
    print("\n=== Top 20 Targets ===")
    for rank, (name, info) in enumerate(ranked, 1):
        best = max(info['parcels'], key=lambda p: p[-1])
        ct = ENTITY_CONTACTS.get(name.strip(), {})
        exec_name = ct.get('president', 'SOS lookup req')
        print(f"  {rank:>2}. {name[:50]:50s} {best[2]:>6.1f}ac "
              f"score={best[-1]} {exec_name[:30]}")
