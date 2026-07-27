"""Scan Python files for SQL/MongoDB runtime patterns and add inferred edges to graph.json.

Tracks function boundaries so each SQL/Mongo call is attributed to the right function node.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
GRAPH_JSON = ROOT / "graphify-out" / "graph.json"
BLACKLIST_DIRS = {'venv', 'cache', 'output', '.opencode', '__pycache__', 'node_modules'}

SQL_KEYWORDS = {'OR','AND','IN','ON','AS','SET','VALUES','WHERE','FROM','JOIN','LEFT','RIGHT',
    'INNER','OUTER','FULL','CROSS','NULL','NOT','EXISTS','BETWEEN','LIKE','IS','HAVING',
    'GROUP','ORDER','BY','ASC','DESC','LIMIT','OFFSET','UNION','ALL','DISTINCT','CASE',
    'WHEN','THEN','ELSE','END','TRUE','FALSE','PRIMARY','KEY','FOREIGN','INDEX','UNIQUE',
    'CHECK','DEFAULT','CASCADE','CONSTRAINT','AUTOINCREMENT','ROWID','IF','WITH',
    'RECURSIVE','ABORT','FAIL','IGNORE','REPLACE','ROLLBACK'}

SQL_READ_PATTERNS = [
    re.compile(r'\bFROM\s+(\w+)', re.I),
    re.compile(r'\bJOIN\s+(\w+)', re.I),
]
SQL_WRITE_PATTERNS = [
    re.compile(r'\bINSERT\s+(?:INTO\s+)?(\w+)', re.I),
    re.compile(r'\bUPDATE\s+(\w+)', re.I),
    re.compile(r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', re.I),
    re.compile(r'\bDELETE\s+FROM\s+(\w+)', re.I),
    re.compile(r'\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)', re.I),
    re.compile(r'\bREPLACE\s+INTO\s+(\w+)', re.I),
]
MONGO_READ = re.compile(r'(?:db|collection|mongo_db)\s*\.\s*(\w+)\s*\.\s*(?:find|find_one|aggregate|count_documents|distinct)\s*\(')
MONGO_WRITE = re.compile(r'(?:db|collection|mongo_db)\s*\.\s*(\w+)\s*\.\s*(?:insert_one|insert_many|update_one|update_many|replace_one|delete_one|delete_many|bulk_write|create_index)\s*\(')
MONGO_COLLECTION_REF = re.compile(r'(?:db|collection|mongo_db)\s*\[\s*["\'](\w+)["\']\s*\]')


def is_valid_table(name):
    if len(name) < 3:
        return False
    if name in SQL_KEYWORDS:
        return False
    if name.startswith('sqlite_'):
        return False
    if name.endswith('_'):
        return False
    return True


def extract_sql_tables(sql):
    """Return {table: set('reads'|'writes')} from a single SQL string."""
    tables = {}
    for pat in SQL_READ_PATTERNS:
        for m in pat.finditer(sql):
            t = m.group(1).strip('"\'')
            if is_valid_table(t):
                tables.setdefault(t, set()).add('reads')
    for pat in SQL_WRITE_PATTERNS:
        for m in pat.finditer(sql):
            t = m.group(1).strip('"\'')
            if is_valid_table(t):
                tables.setdefault(t, set()).add('writes')
    return tables


def get_function_ranges(text):
    """Return list of (func_name, start_line, end_line) for each function in text."""
    lines = text.split('\n')
    ranges = []
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)def\s+(\w+)\s*\(', line)
        if m:
            func_indent = len(m.group(1))
            func_name = m.group(2)
            start = i
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j] and not lines[j].isspace():
                    next_indent = len(lines[j]) - len(lines[j].lstrip())
                    if next_indent <= func_indent and not lines[j].startswith((' ', '\t', '@', '#')):
                        end = j
                        break
            ranges.append((func_name, start, end))
    return ranges


def find_file_node(graph, source_file):
    for n in graph['nodes']:
        if n.get('source_file') == source_file:
            return n['id']
    return None


def find_function_node(graph, source_file, func_name):
    """Find the graph node for a function in a specific file."""
    for n in graph['nodes']:
        if n.get('source_file') == source_file and func_name in n.get('label', ''):
            return n['id']
    return None


def _extract_sql_string(lines, start_line, col):
    if start_line >= len(lines):
        return None
    rest = lines[start_line][col:].strip()
    if not rest:
        return None

    # Detect triple-quote start: f""" or """ or f''' or '''
    tq_start = re.match(r'''[fF]?(["']{3})''', rest)
    if tq_start:
        quote = tq_start.group(1)          # """ or '''
        content = rest[tq_start.end():]

        # Check if closing quotes on same line (after optional content)
        end_idx = content.find(quote)
        if end_idx != -1:
            return content[:end_idx]

        # Scan ahead for closing quotes
        parts = [content]
        for j in range(start_line + 1, len(lines)):
            line = lines[j]
            end_idx = line.find(quote)
            if end_idx != -1:
                parts.append(line[:end_idx])
                break
            parts.append(line)
        return '\n'.join(parts)

    # Single-quoted string on same line
    sq_match = re.match(r'''[fF]?['"]([^'"]*)['"]''', rest)
    if sq_match:
        return sq_match.group(1)

    return None


def main():
    if not GRAPH_JSON.exists():
        print(f"Graph file not found: {GRAPH_JSON}")
        return

    graph = json.loads(GRAPH_JSON.read_text())
    # Remove stale inferred edges and nodes before re-scanning
    graph['links'] = [e for e in graph['links'] if e.get('confidence') != 'INFERRED']
    graph['nodes'] = [n for n in graph['nodes'] if n.get('_origin') != 'runtime_inferred']
    existing_ids = {n['id'] for n in graph['nodes']}
    added_edges = 0
    added_nodes = 0

    py_files = sorted(ROOT.rglob('*.py'))
    for py_file in py_files:
        if any(b in str(py_file) for b in BLACKLIST_DIRS):
            continue
        rel = str(py_file.relative_to(ROOT))
        try:
            text = py_file.read_text()
        except Exception:
            continue

        lines = text.split('\n')
        func_ranges = get_function_ranges(text)

        # Find file-level node
        file_node = find_file_node(graph, rel)

        # Find all SQL execute() calls and extract the SQL string (handles multi-line)
        sql_calls = []
        execute_pat = re.compile(r'''(?:\w+\.)?(?:execute|executescript|executemany|exec|raw_sql|query|fetchone|fetchall)\s*\(\s*''', re.I)
        for i, line in enumerate(lines):
            for m in execute_pat.finditer(line):
                sql = _extract_sql_string(lines, i, m.end())
                if sql:
                    sql_calls.append((i, sql))

        # Map each SQL call to a function
        for line_no, sql in sql_calls:
            tables = extract_sql_tables(sql)
            if not tables:
                continue

            # Find which function this line belongs to
            src_node = None
            for func_name, fs, fe in func_ranges:
                if fs <= line_no < fe:
                    src_node = find_function_node(graph, rel, func_name)
                    break

            # If no function match, use file-level
            if not src_node:
                src_node = file_node

            if not src_node:
                continue

            for tname, ops in tables.items():
                nid = f"__db__{tname}"
                if nid not in existing_ids:
                    graph['nodes'].append({
                        "id": nid,
                        "label": f"{tname} (sqlite)",
                        "file_type": "database",
                        "source_file": "",
                        "source_location": "",
                        "_origin": "runtime_inferred",
                        "community": -1,
                        "norm_label": tname,
                        "strategic": False,
                        "community_name": "Database Tables",
                    })
                    existing_ids.add(nid)
                    added_nodes += 1

                for op in ops:
                    dup = False
                    for e in graph['links']:
                        if e['source'] == src_node and e['target'] == nid and e.get('relation') == op:
                            dup = True
                            break
                    if not dup:
                        graph['links'].append({
                            "relation": op,
                            "confidence": "INFERRED",
                            "source_file": rel,
                            "source_location": f"L{line_no + 1}",
                            "weight": 0.8,
                            "source": src_node,
                            "target": nid,
                            "confidence_score": 0.6,
                        })
                        added_edges += 1

        # Also scan for MongoDB calls with line numbers
        for i, line in enumerate(lines):
            for m in MONGO_READ.finditer(line):
                coll = m.group(1)
                if not is_valid_table(coll):
                    continue
                src_node = None
                for func_name, fs, fe in func_ranges:
                    if fs <= i < fe:
                        src_node = find_function_node(graph, rel, func_name)
                        break
                if not src_node:
                    src_node = file_node
                if not src_node:
                    continue

                nid = f"__db__{coll}"
                if nid not in existing_ids:
                    graph['nodes'].append({
                        "id": nid,
                        "label": f"{coll} (mongo)",
                        "file_type": "database",
                        "source_file": "",
                        "source_location": "",
                        "_origin": "runtime_inferred",
                        "community": -1,
                        "norm_label": coll,
                        "strategic": False,
                        "community_name": "Database Tables",
                    })
                    existing_ids.add(nid)
                    added_nodes += 1

                dup = False
                for e in graph['links']:
                    if e['source'] == src_node and e['target'] == nid and e.get('relation') == 'reads':
                        dup = True
                        break
                if not dup:
                    graph['links'].append({
                        "relation": "reads",
                        "confidence": "INFERRED",
                        "source_file": rel,
                        "source_location": f"L{i + 1}",
                        "weight": 0.8,
                        "source": src_node,
                        "target": nid,
                        "confidence_score": 0.6,
                    })
                    added_edges += 1

            for m in MONGO_WRITE.finditer(line):
                coll = m.group(1)
                if not is_valid_table(coll):
                    continue
                src_node = None
                for func_name, fs, fe in func_ranges:
                    if fs <= i < fe:
                        src_node = find_function_node(graph, rel, func_name)
                        break
                if not src_node:
                    src_node = file_node
                if not src_node:
                    continue

                nid = f"__db__{coll}"
                if nid not in existing_ids:
                    graph['nodes'].append({
                        "id": nid,
                        "label": f"{coll} (mongo)",
                        "file_type": "database",
                        "source_file": "",
                        "source_location": "",
                        "_origin": "runtime_inferred",
                        "community": -1,
                        "norm_label": coll,
                        "strategic": False,
                        "community_name": "Database Tables",
                    })
                    existing_ids.add(nid)
                    added_nodes += 1

                dup = False
                for e in graph['links']:
                    if e['source'] == src_node and e['target'] == nid and e.get('relation') == 'writes':
                        dup = True
                        break
                if not dup:
                    graph['links'].append({
                        "relation": "writes",
                        "confidence": "INFERRED",
                        "source_file": rel,
                        "source_location": f"L{i + 1}",
                        "weight": 0.8,
                        "source": src_node,
                        "target": nid,
                        "confidence_score": 0.6,
                    })
                    added_edges += 1

    GRAPH_JSON.write_text(json.dumps(graph, indent=2))
    print(f"Added {added_edges} runtime edges, {added_nodes} new table nodes")
    print(f"Total: {len(graph['nodes'])} nodes, {len(graph['links'])} edges")


if __name__ == "__main__":
    main()
