"""Backfill zoning + land_use for remaining blank Texas parcels.

Uses the same owner-name regex classifier from D64 normalize_tx_zoning.py,
then derives attom_land_use_desc from the zoning value.
"""

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "deals.db"
TEXAS_COUNTIES = ("dallas", "travis", "grayson", "hood", "navarro")

ZONING_LABELS = {
    "INDUSTRIAL": "Industrial / Data Center Compatible",
    "COMMERCIAL": "Commercial / Mixed Use",
    "AGRICULTURAL": "Agricultural / Rural Land",
    "RESIDENTIAL": "Residential",
}

INDUSTRIAL_PATTERNS = [
    r'\bINDUSTRIAL\b', r'\bCHEMICAL\b', r'\bREFINERY\b',
    r'\bPIPELINE\b', r'\bWAREHOUSE\b', r'\bLOGISTICS\b',
    r'\bFOUNDRY\b', r'\bQUARRY\b',
    r'\bCONCRETE\b', r'\bASPHALT\b',
    r'\bFEEDYARD\b', r'\bFEEDLOT\b', r'\bLANDFILL\b',
    r'\bRECYCLING\b', r'\bDISPOSAL\b',
    r'\bGENERATING\b', r'\bPOWER\s+PLANT\b', r'\bELECTRIC\s+(COOP|COOPERATIVE|GENERATING|POWER)\b',
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
    r'\bDEVELOPMENT\b',
]

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

compiled = {
    'INDUSTRIAL': [re.compile(p, re.IGNORECASE) for p in INDUSTRIAL_PATTERNS],
    'COMMERCIAL': [re.compile(p, re.IGNORECASE) for p in COMMERCIAL_PATTERNS],
    'AGRICULTURAL': [re.compile(p, re.IGNORECASE) for p in AGRICULTURAL_PATTERNS],
}

ENTITY_KW = [' LLC', ' INC', ' CORP', ' LP', ' LTD', ' TRUST',
             ' TRUSTEE', ' JOINT', ' PARTNERSHIP', ' PROPERTIES',
             ' PROPERTY', ' HOLDING', ' HOLDINGS']


def classify(name, acres_str):
    acres = float(acres_str) if acres_str else 0
    if not name or name.strip() in ('', ' ', 'MULTIPLE OWNERS'):
        return None
    name_upper = name.upper()

    for pat in compiled['INDUSTRIAL']:
        if pat.search(name):
            return 'INDUSTRIAL'
    for pat in compiled['COMMERCIAL']:
        if pat.search(name):
            return 'COMMERCIAL'
    for pat in compiled['AGRICULTURAL']:
        if pat.search(name):
            return 'AGRICULTURAL'

    is_entity = any(kw in name_upper for kw in ENTITY_KW)

    if acres >= 200:
        return 'AGRICULTURAL' if not is_entity else 'COMMERCIAL'
    if acres >= 50:
        return 'AGRICULTURAL' if not is_entity else 'COMMERCIAL'
    if acres < 50 and is_entity:
        return 'COMMERCIAL'
    if acres < 50:
        return 'RESIDENTIAL'
    return 'AGRICULTURAL'


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    placeholders = ','.join('?' * len(TEXAS_COUNTIES))

    blank = conn.execute(f"""
        SELECT id, owner_name, acres FROM commercial_sites
        WHERE county IN ({placeholders})
          AND (zoning IS NULL OR zoning = '' OR zoning = '—')
    """, TEXAS_COUNTIES).fetchall()
    print(f"Sites without zoning: {len(blank)}")

    updated = 0
    for site in blank:
        zoning = classify(site['owner_name'], site['acres'])
        if zoning:
            conn.execute(
                "UPDATE commercial_sites SET zoning = ?, attom_land_use_desc = ? WHERE id = ?",
                (zoning, ZONING_LABELS[zoning], site['id']),
            )
            updated += 1

    conn.commit()
    print(f"Updated: {updated}")

    remaining = conn.execute(f"""
        SELECT COUNT(*) FROM commercial_sites
        WHERE county IN ({placeholders})
          AND (zoning IS NULL OR zoning = '' OR zoning = '—')
    """, TEXAS_COUNTIES).fetchone()[0]
    print(f"Still blank: {remaining}")

    print("\n=== Final Zoning Distribution ===")
    counts = conn.execute(f"""
        SELECT zoning, COUNT(*) as cnt, ROUND(SUM(CAST(acres AS REAL)), 0) as total_ac
        FROM commercial_sites
        WHERE county IN ({placeholders})
        GROUP BY zoning ORDER BY cnt DESC
    """, TEXAS_COUNTIES).fetchall()
    for c in counts:
        label = c['zoning'] or 'UNCLASSIFIED'
        print(f"  {label:15s} {c['cnt']:>6d} parcels  {c['total_ac']:>10,.0f} ac")

    print("\n=== By County ===")
    for cty in TEXAS_COUNTIES:
        has = conn.execute(
            "SELECT COUNT(*) as c FROM commercial_sites WHERE county = ? AND (zoning IS NOT NULL AND zoning != '' AND zoning != '—')",
            (cty,),
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) as c FROM commercial_sites WHERE county = ?", (cty,)).fetchone()[0]
        print(f"  {cty}: {has}/{total}")

    conn.close()


if __name__ == "__main__":
    main()
