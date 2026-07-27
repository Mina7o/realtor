"""
Directive 64: The "Texas Zoning" Normalizer

Analyzes raw_tx_stratmap_parcels owner names and parcel characteristics
to classify each site into a standardized 4-tier schema:

    [INDUSTRIAL, COMMERCIAL, AGRICULTURAL, RESIDENTIAL]

Stores tx_normalized_zoning column, then applies +40 Zoning/Power Synergy
bonus for any INDUSTRIAL site within 1.0mi of a 345kV transmission line.
"""

import json
import sqlite3
import time
from pathlib import Path
import re

from shapely.geometry import shape as shapely_shape, Point
from rtree import index as rtree_index

DB_PATH = Path(__file__).parent.parent / "deals.db"
INFRA_DB = Path(__file__).parent.parent / "infrastructure.db"
TABLE = "raw_tx_stratmap_parcels"

# ── INDUSTRIAL: high-precision patterns ──
INDUSTRIAL_PATTERNS = [
    r'\bINDUSTRIAL\b', r'\bCHEMICAL\b', r'\bREFINERY\b',
    r'\bPIPELINE\b', r'\bWAREHOUSE\b', r'\bLOGISTICS\b',
    r'\bFOUNDRY\b', r'\bQUARRY\b',
    r'\bCONCRETE\b', r'\bASPHALT\b',
    r'\bFEEDYARD\b', r'\bFEEDLOT\b', r'\bLANDFILL\b',
    r'\bRECYCLING\b', r'\bDISPOSAL\b',
    r'\bWASTE\s+(PROCESS|MANAGEMENT|DISPOSAL|TREATMENT)\b',
    r'\bREFINING\b', r'\bOIL\s+REFINERY\b', r'\bGAS\s+PLANT\b',
    r'\bPETROLEUM\b', r'\bDRILLING\b', r'\bOILFIELD\b',
    r'\bCOMPRESSOR\s+STATION\b', r'\bPUMPING\s+STATION\b',
    r'\bWATER\s+TREATMENT\b', r'\bWASTEWATER\b', r'\bSEWAGE\b',
    r'\bSANITARY\s+LANDFILL\b', r'\bTRANSFER\s+STATION\b',
    r'\bMINING\b', r'\bLIGNITE\b',
    r'\bCOAL\s+(MINE|COMPANY)\b', r'\bGRAVEL\s+(PIT|COMPANY)\b',
    r'\bSAND\s+(PIT|COMPANY)\b', r'\bCRUSHED\s+STONE\b',
    r'\bLUMINANT\b', r'\bVISTRA\b',
    r'\bNRG\s+(TEXAS|ENERGY)\b', r'\bTEXAS\s+CEMENT\b',
    r'\bHOLCIM\b', r'\bCEMEX\b',
    r'\bDOW\s+CHEMICAL\b', r'\bEQUISTAR\b', r'\bLYONDELL\b',
    r'\bCHEVRON\b', r'\bEXXON\b', r'\bMOBIL\b',
    r'\bSHELL\s+OIL\b', r'\bVALERO\b',
    r'\bRAILROAD\b', r'\bRAILWAY\b', r'\bBNSF\b',
    r'\bUNION\s+PACIFIC\b',
    r'\bPORT\s+(AUTHORITY|OF|TERMINAL)\b',
    r'\bTANK\s+FARM\b', r'\bSTORAGE\s+(TERMINAL|YARD|DEPOT)\b',
    r'\bLUMBER\s+(YARD|MILL)\b', r'\bSAWMILL\b',
    r'\bCOGENERATION\b', r'\bGENERATING\s+STATION\b',
    r'\bPOWER\s+(PLANT|STATION|GENERAT)\b',
    r'\bELECTRIC\s+(COOP|COOPERATIVE|GENERATING|POWER)\b',
    r'\bSUBSTATION\b', r'\bSWITCHYARD\b', r'\bTRANSFORMER\b',
    r'\bMANUFACTUR(ING|ER|ERS)\b', r'\bFABRICATION\b',
    r'\bFACTORY\b', r'\bSMELTING\b', r'\bFORGING\b',
    r'\bPLATING\b', r'\bAEROSPACE\b', r'\bAVIATION\b',
    r'\bAIRCRAFT\b', r'\bAIRPORT\b', r'\bAUTOMOTIVE\b',
    r'\bMACHINERY\b', r'\bPHARMACEUTICAL\b', r'\bBIOTECHNOLOGY\b',
    r'\bLABORATOR(Y|IES)\b', r'\bRENDERING\b',
    r'\bPACKING\s+(PLANT|HOUSE|COMPANY)\b',
    r'\bSLAUGHTER\s+(HOUSE|PLANT|COMPANY)\b',
    r'\bMEAT\s+PACKING\b', r'\bSTEEL\s+(MILL|WORKS|PLANT)\b',
    r'\bALUMINUM\b',
]

# ── COMMERCIAL ──
COMMERCIAL_PATTERNS = [
    r'\bSHOPPING\s+CENTER\b', r'\bSHOPPING\s+MALL\b',
    r'\bRETAIL\s+(CENTER|PLAZA|SPACE|STORE)\b',
    r'\bSTRIP\s+(MALL|CENTER)\b',
    r'\bOFFICE\s+(PARK|BUILDING|CENTER|PLAZA|TOWER)\b',
    r'\bMIXED\s+USE\b', r'\bHOTEL\b', r'\bMOTEL\b',
    r'\bRESTAURANT\b',
    r'\bCONVENIENCE\s+STORE\b', r'\bGROCERY\b', r'\bSUPERMARKET\b',
    r'\bWAL\s*MART\b', r'\bHOME\s+DEPOT\b', r'\bCOSTCO\b',
    r'\bKROGER\b', r'\bHOSPITAL\b', r'\bCLINIC\b', r'\bPHARMACY\b',
    r'\bMEDICAL\s+(CENTER|PLAZA|BUILDING|COMPLEX|OFFICE)\b',
    r'\bBANK\s+(OF|CORP|BUILDING|PLAZA)\b', r'\bCREDIT\s+UNION\b',
    r'\bSCHOOL\s+DISTRICT\b', r'\bUNIVERSITY\b', r'\bCOLLEGE\b',
    r'\bCHURCH\b', r'\bCONGREGATION\b', r'\bMINISTRY\b',
    r'\bDAYCARE\b', r'\bNURSING\s+HOME\b', r'\bASSISTED\s+LIVING\b',
    r'\bAPARTMENT\b', r'\bCONDOMINIUM\b', r'\bTOWNHOME\b',
    r'\bMULTIFAMILY\b', r'\bMULTI\s+FAMILY\b',
    r'\bGOLF\s+(CLUB|COURSE|RESORT)\b', r'\bCOUNTRY\s+CLUB\b',
    r'\bFITNESS\b', r'\bGYM\b', r'\bTHEATRE\b', r'\bTHEATER\b',
    r'\bENTERTAINMENT\b', r'\bSTADIUM\b', r'\bARENA\b',
    r'\bCONVENTION\s+CENTER\b', r'\bFAIRGROUNDS?\b',
    r'\bRACE\s+TRACK\b', r'\bSPEEDWAY\b', r'\bMUSEUM\b',
    r'\bLIBRARY\b', r'\bCEMETERY\b', r'\bMEMORIAL\s+PARK\b',
    r'\bCOMMERCIAL\b', r'\bBUSINESS\s+(PARK|CENTER|PLAZA)\b',
    r'\bRETAIL\b',
]

# ── AGRICULTURAL ──
AGRICULTURAL_PATTERNS = [
    r'\bRANCH\b', r'\bFARM\b', r'\bFARMING\b',
    r'\bAGRICULTUR(E|AL)\b', r'\bCATTLE\b', r'\bLIVESTOCK\b',
    r'\bDAIRY\b', r'\bPOULTRY\b', r'\bSWINE\b',
    r'\bFEED\s+(LOT|YARD)\b', r'\bCROP\b',
    r'\bGRAIN\s+(ELEVATOR|FARM|PRODUCERS?)\b', r'\bSILO\b',
    r'\bTIMBER\b', r'\bFOREST(RY| PRODUCTS?)\b', r'\bWOODLAND\b',
    r'\bPLANTATION\b', r'\bORCHARD\b', r'\bVINEYARD\b',
    r'\bWINERY\b', r'\bPASTURE\b',
    r'\bRANGE\s+(LAND|CATTLE|LLC|LTD)\b', r'\bFERTILIZER\b',
    r'\bCOTTON\s+(FARM|GIN|PRODUCERS?)\b',
    r'\bWHEAT\s+(FARM|RANCH)\b', r'\bCORN\s+(FARM|RANCH)\b',
]


def compile_patterns(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def classify_owner_name(name, acres, lat, lng):
    if not name or name.strip() in ('', ' ', 'MULTIPLE OWNERS'):
        return None
    name_upper = name.upper()

    for pat in INDUSTRIAL_RE:
        if pat.search(name):
            return 'INDUSTRIAL'
    for pat in COMMERCIAL_RE:
        if pat.search(name):
            return 'COMMERCIAL'
    for pat in AGRICULTURAL_RE:
        if pat.search(name):
            return 'AGRICULTURAL'

    entity_kw = [' LLC', ' INC', ' CORP', ' LP', ' LTD', ' TRUST',
                  ' TRUSTEE', ' JOINT', ' PARTNERSHIP', ' PROPERTIES',
                  ' PROPERTY', ' HOLDING', ' HOLDINGS']

    is_entity = any(kw in name_upper for kw in entity_kw)
    is_individual = not is_entity and not any(kw in name_upper for kw in
        [' LP', ' LLC', ' INC', ' CORP', ' LTD'])

    if acres >= 200:
        return 'AGRICULTURAL' if is_individual else 'COMMERCIAL'
    if acres >= 50:
        return 'AGRICULTURAL' if is_individual else 'COMMERCIAL'
    if acres < 50 and is_entity:
        return 'COMMERCIAL'
    if acres < 50:
        return 'RESIDENTIAL'
    return 'AGRICULTURAL'


def add_zoning_column(conn):
    try:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN tx_normalized_zoning TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def classify_all(conn):
    cur = conn.cursor()
    cur.execute(f"SELECT rowid, objectid, owner_name, acres, lat, lng FROM {TABLE} "
                f"WHERE tx_normalized_zoning IS NULL")
    rows = cur.fetchall()
    total = len(rows)
    print(f"Classifying {total:,} unclassified parcels...")

    updates = []
    for i, (rowid, oid, name, acres, lat, lng) in enumerate(rows):
        zoning = classify_owner_name(name, acres, lat, lng)
        if zoning:
            updates.append((zoning, rowid))
        if updates and (len(updates) >= 5000 or i == total - 1):
            conn.executemany(
                f"UPDATE {TABLE} SET tx_normalized_zoning = ? WHERE rowid = ?", updates
            )
            conn.commit()
            updates = []
        if (i + 1) % 20000 == 0:
            print(f"  {i+1:,}/{total:,}")
    if updates:
        conn.executemany(
            f"UPDATE {TABLE} SET tx_normalized_zoning = ? WHERE rowid = ?", updates
        )
        conn.commit()
    print("Classification complete.")


def compute_synergy_bonus(conn):
    try:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN zoning_power_synergy INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    cur = conn.cursor()
    cur.execute(f"SELECT rowid, objectid, lat, lng FROM {TABLE} "
                f"WHERE tx_normalized_zoning = 'INDUSTRIAL'")
    industrial = cur.fetchall()
    print(f"\nINDUSTRIAL parcels: {len(industrial):,}")
    if not industrial:
        return

    infra_conn = sqlite3.connect(str(INFRA_DB))
    infra_cur = infra_conn.cursor()
    infra_cur.execute("""
        SELECT rowid, geometry_geojson FROM transmission_lines
        WHERE state = 'TX' AND voltage >= 345
    """)
    lines = infra_cur.fetchall()
    infra_conn.close()
    print(f"TX 345kV+ lines: {len(lines):,}")

    idx = rtree_index.Index()
    line_by_id = {}
    for line_rowid, geom_json in lines:
        try:
            geom = json.loads(geom_json)
            shape = shapely_shape(geom)
            line_by_id[line_rowid] = shape
            idx.insert(line_rowid, shape.bounds)
        except Exception:
            continue
    print(f"R-Tree indexed {len(line_by_id):,} line segments")

    synergy_count = 0
    for i, (rowid, oid, lat, lng) in enumerate(industrial):
        pt = Point(lng, lat)
        nearby = list(idx.intersection(pt.bounds))
        min_dist = float('inf')
        for line_rowid in nearby:
            shape = line_by_id.get(line_rowid)
            if shape is None:
                continue
            dist_deg = pt.distance(shape)
            dist_mi = dist_deg * 53
            if dist_mi < min_dist:
                min_dist = dist_mi
        if min_dist <= 1.0:
            conn.execute(
                f"UPDATE {TABLE} SET zoning_power_synergy = 40 WHERE rowid = ?",
                (rowid,)
            )
            synergy_count += 1
        if (i + 1) % 1000 == 0:
            conn.commit()
            print(f"  Synergy: {i+1:,}/{len(industrial):,} (found {synergy_count:,} qualifying)")

    conn.commit()
    print(f"\nZoning/Power Synergy: {synergy_count:,} INDUSTRIAL parcels within 1.0mi of 345kV+")
    print(f"  Each receives +40 points to their aggregate score")


def summary(conn):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT tx_normalized_zoning, COUNT(*), ROUND(AVG(acres)), ROUND(SUM(acres)),
               SUM(zoning_power_synergy) / 40 as synergy_count
        FROM {TABLE}
        WHERE tx_normalized_zoning IS NOT NULL
        GROUP BY tx_normalized_zoning
        ORDER BY COUNT(*) DESC
    """)
    print(f"\n{'Zoning':15s} {'Count':>8s} {'AvgAc':>6s} {'TotalAc':>12s} {'Synergy':>8s}")
    print('-' * 55)
    for r in cur.fetchall():
        print(f"{r[0]:15s} {r[1]:>8,} {r[2]:>6.0f} {r[3]:>12,.0f} {r[4]:>8,}")
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE tx_normalized_zoning IS NULL")
    nulls = cur.fetchone()[0]
    if nulls:
        print(f"{'UNCLASSIFIED':15s} {nulls:>8,}")


if __name__ == "__main__":
    INDUSTRIAL_RE = compile_patterns(INDUSTRIAL_PATTERNS)
    COMMERCIAL_RE = compile_patterns(COMMERCIAL_PATTERNS)
    AGRICULTURAL_RE = compile_patterns(AGRICULTURAL_PATTERNS)

    conn = sqlite3.connect(str(DB_PATH))
    add_zoning_column(conn)
    classify_all(conn)
    compute_synergy_bonus(conn)
    summary(conn)
    conn.close()
