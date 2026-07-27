"""Post-process graphify output: add strategic highlighting + name communities."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
GRAPH_JSON = ROOT / "graphify-out" / "graph.json"
GRAPH_HTML = ROOT / "graphify-out" / "graph.html"
OUTPUT_HTML = ROOT / "output" / "code_graph.html"

STRATEGIC_KEYWORDS = {
    "roi", "nuclear", "grid", "assembly", "alpha", "loi", "jda",
    "power", "interconnect", "shortlist", "transmission", "substation",
    "pipeline", "overture", "cluster", "parcel", "zoning", "attom",
    "enrich", "deal", "absentee", "moratorium", "overlay", "hearing",
    "infrastructure", "commercial", "sentinel", "anchor", "arbiter",
}


def is_strategic(label):
    return any(k in label.lower() for k in STRATEGIC_KEYWORDS)


def main():
    if not GRAPH_JSON.exists():
        print("graph.json not found — run graphify update . first")
        return

    graph = json.loads(GRAPH_JSON.read_text())
    nodes = graph.get("nodes", [])

    community_nodes = {}
    for n in nodes:
        cid = n.get("community")
        if cid is not None:
            community_nodes.setdefault(cid, []).append(n)

    community_names = {}
    for cid, members in community_nodes.items():
        members.sort(key=lambda x: x.get("degree", 0), reverse=True)
        top = members[0]["label"] if members else f"Community {cid}"
        count = len(members)
        community_names[str(cid)] = f"{top} ({count})"

    strategic_count = 0
    for n in nodes:
        label = n.get("label", "")
        n["strategic"] = is_strategic(label)
        if n["strategic"]:
            strategic_count += 1
        cid = str(n.get("community", ""))
        n["community_name"] = community_names.get(cid, f"Community {cid}")

    graph["nodes"] = nodes
    GRAPH_JSON.write_text(json.dumps(graph, indent=2))
    print(f"Enhanced graph.json: {len(nodes)} nodes, {strategic_count} strategic")

    # --- Regenerate enhanced graph.html ---
    RAW_NODES_JSON = json.dumps([{
        "id": n["id"],
        "label": n["label"],
        "color": {
            "background": "#FFD700" if n.get("strategic") else n.get("color", {}).get("background", "#4E79A7"),
            "border": "#FFD700" if n.get("strategic") else n.get("color", {}).get("border", "#4E79A7"),
            "highlight": {"background": "#ffffff", "border": "#FFD700" if n.get("strategic") else (n.get("color", {}).get("background", "#4E79A7"))},
        },
        "size": n.get("size", 10) * (2.0 if n.get("strategic") else 1.0),
        "font": {"size": 14 if n.get("strategic") else 12, "color": "#FFD700" if n.get("strategic") else "#ffffff"},
        "title": f"{n['label']} {'⭐ STRATEGIC' if n.get('strategic') else ''}",
        "community": n.get("community"),
        "community_name": n.get("community_name", f"Community {n.get('community', '?')}"),
        "source_file": n.get("source_file", ""),
        "file_type": n.get("file_type", ""),
        "degree": n.get("degree", 0),
        "strategic": n.get("strategic", False),
        "description": n.get("description", ""),
    } for n in nodes])

    community_list = {}
    for n in nodes:
        cid = n.get("community")
        if cid is not None:
            community_list.setdefault(cid, {"count": 0, "name": n.get("community_name", f"Community {cid}")})
            community_list[cid]["count"] += 1

    LEGEND = sorted([
        {"cid": cid, "color": "#FFD700" if any(
            n.get("strategic") for n in nodes if n.get("community") == cid
        ) else "#4E79A7", "label": info["name"], "count": info["count"]}
        for cid, info in community_list.items()
    ], key=lambda x: -x["count"])

    # Generate edges JSON from graph
    RAW_EDGES = graph.get("links", [])
    RAW_EDGES_JSON = json.dumps([{
        "from": e["source"],
        "to": e["target"],
        "label": e.get("relation", ""),
        "title": f"{e.get('relation', '')} [{e.get('confidence', 'EXTRACTED')}]",
        "dashes": e.get("confidence") == "INFERRED",
        "width": 2,
        "color": {"color": "#6688aa", "opacity": 0.7 if e.get("confidence") == "INFERRED" else 1.0, "highlight": "#FFD700"},
    } for e in RAW_EDGES])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Code Graph — realtor</title>
<script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"
        integrity="sha384-Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1"
        crossorigin="anonymous"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f1a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; height: 100vh; overflow: hidden; }}
  #graph {{ flex: 1; }}
  #sidebar {{ width: 300px; background: #1a1a2e; border-left: 1px solid #2a2a4e; display: flex; flex-direction: column; overflow: hidden; }}
  #search-wrap {{ padding: 12px; border-bottom: 1px solid #2a2a4e; }}
  #search {{ width: 100%; background: #0f0f1a; border: 1px solid #3a3a5e; color: #e0e0e0; padding: 7px 10px; border-radius: 6px; font-size: 13px; outline: none; }}
  #search:focus {{ border-color: #FFD700; }}
  #search-results {{ max-height: 140px; overflow-y: auto; padding: 4px 12px; border-bottom: 1px solid #2a2a4e; display: none; }}
  .search-item {{ padding: 4px 6px; cursor: pointer; border-radius: 4px; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .search-item:hover {{ background: #2a2a4e; }}
  #info-panel {{ padding: 14px; border-bottom: 1px solid #2a2a4e; min-height: 140px; }}
  #info-panel h3 {{ font-size: 13px; color: #aaa; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }}
  #info-content {{ font-size: 13px; color: #ccc; line-height: 1.6; }}
  #info-content .field {{ margin-bottom: 5px; }}
  #info-content .field b {{ color: #e0e0e0; }}
  #info-content .empty {{ color: #555; font-style: italic; }}
  .neighbor-link {{ display: block; padding: 2px 6px; margin: 2px 0; border-radius: 3px; cursor: pointer; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-left: 3px solid #333; }}
  .neighbor-link:hover {{ background: #2a2a4e; }}
  .strategic-badge {{ color: #FFD700; font-weight: bold; }}
  #neighbors-list {{ max-height: 160px; overflow-y: auto; margin-top: 4px; }}
  #legend-wrap {{ flex: 1; overflow-y: auto; padding: 12px; }}
  #legend-wrap h3 {{ font-size: 13px; color: #aaa; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; border-radius: 4px; font-size: 12px; }}
  .legend-item:hover {{ background: #2a2a4e; padding-left: 4px; }}
  .legend-item.dimmed {{ opacity: 0.35; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  .legend-label {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .legend-count {{ color: #666; font-size: 11px; }}
  #stats {{ padding: 10px 14px; border-top: 1px solid #2a2a4e; font-size: 11px; color: #555; }}
  #legend-controls {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; padding: 4px 0; }}
  #legend-controls label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 12px; color: #aaa; user-select: none; }}
  #legend-controls label:hover {{ color: #e0e0e0; }}
  .legend-cb, #select-all-cb {{ appearance: none; -webkit-appearance: none; width: 14px; height: 14px; border: 1.5px solid #3a3a5e; border-radius: 3px; background: #0f0f1a; cursor: pointer; position: relative; flex-shrink: 0; }}
  .legend-cb:checked, #select-all-cb:checked {{ background: #FFD700; border-color: #FFD700; }}
  .legend-cb:checked::after, #select-all-cb:checked::after {{ content: ''; position: absolute; left: 3.5px; top: 1px; width: 4px; height: 7px; border: solid #1a1a2e; border-width: 0 2px 2px 0; transform: rotate(45deg); }}
  #select-all-cb:indeterminate {{ background: #FFD700; border-color: #FFD700; }}
  #select-all-cb:indeterminate::after {{ content: ''; position: absolute; left: 2px; top: 5px; width: 8px; height: 2px; background: #1a1a2e; border: none; transform: none; }}
</style>
</head>
<body>
<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="Search nodes..." autocomplete="off">
    <div id="search-results"></div>
  </div>
  <div id="info-panel">
    <h3>Node Info</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  <div id="legend-wrap">
    <h3>Communities</h3>
    <div id="legend-controls">
      <label><input type="checkbox" id="select-all-cb" checked onchange="toggleAllCommunities(!this.checked)">Select All</label>
    </div>
    <div id="legend"></div>
  </div>
  <div id="stats">loading...</div>
</div>
<script>
const RAW_NODES = {RAW_NODES_JSON};
const RAW_EDGES = {RAW_EDGES_JSON};
const LEGEND = {json.dumps(LEGEND)};

function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }}

const nodesDS = new vis.DataSet(RAW_NODES.map(n => ({{
  id: n.id, label: n.label, color: n.color, size: n.size,
  font: n.font, title: n.title,
  _community: n.community, _community_name: n.community_name,
  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,
  _strategic: n.strategic, _description: n.description,
}})));

const edgesDS = new vis.DataSet(RAW_EDGES.map((e, i) => ({{
  id: i, from: e.from, to: e.to,
  label: '', title: e.title,
  dashes: e.dashes, width: e.width, color: e.color,
  arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
}})));

const container = document.getElementById('graph');
const network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, {{
  physics: {{
    enabled: true, solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -60, centralGravity: 0.005, springLength: 120, springConstant: 0.08, damping: 0.4, avoidOverlap: 0.8 }},
    stabilization: {{ iterations: 200, fit: true }},
  }},
  interaction: {{ hover: true, tooltipDelay: 100, hideEdgesOnDrag: false, selectConnectedEdges: true, hoverConnectedEdges: true }},
  nodes: {{ shape: 'dot', borderWidth: 1.5, borderWidthSelected: 3, color: {{ highlight: {{ border: '#FFD700', background: '#FFD700' }} }} }},
  edges: {{ smooth: {{ type: 'continuous', roundness: 0.2 }}, selectionWidth: 3, color: {{ highlight: '#FFD700' }} }},
}});

network.once('stabilizationIterationsDone', () => network.setOptions({{ physics: {{ enabled: false }} }}));

let selectedNodeId = null;

function highlightConnected(nodeId) {{
  const neighborIds = network.getConnectedNodes(nodeId);
  const edgeIds = network.getConnectedEdges(nodeId);
  const active = new Set([nodeId, ...neighborIds]);
  const activeEdges = new Set(edgeIds);
  const nodeUpdates = [];
  const edgeUpdates = [];
  nodesDS.forEach(n => nodeUpdates.push({{ id: n.id, opacity: active.has(n.id) ? 1.0 : 0.12 }}));
  edgesDS.forEach(e => edgeUpdates.push({{ id: e.id, opacity: activeEdges.has(e.id) ? 1.0 : 0.04 }}));
  nodesDS.update(nodeUpdates);
  edgesDS.update(edgeUpdates);
  network.selectNodes([nodeId], true);
}}

function resetHighlight() {{
  selectedNodeId = null;
  network.unselectAll();
  const nodeUpdates = [];
  const edgeUpdates = [];
  nodesDS.forEach(n => nodeUpdates.push({{ id: n.id, opacity: 1.0 }}));
  edgesDS.forEach(e => edgeUpdates.push({{ id: e.id, opacity: 1.0 }}));
  nodesDS.update(nodeUpdates);
  edgesDS.update(edgeUpdates);
}}

function showInfo(nodeId) {{
  const n = nodesDS.get(nodeId);
  if (!n) return;
  const neighborIds = network.getConnectedNodes(nodeId);
  const badge = n._strategic ? ' <span class="strategic-badge">⭐</span>' : '';
  const neighborItems = neighborIds.map(nid => {{
    const nb = nodesDS.get(nid);
    const color = nb ? nb.color.background : '#555';
    return '<span class="neighbor-link" style="border-left-color:'+esc(color)+'" onclick="focusNode(\\''+nid+'\\')">'+esc(nb ? nb.label : nid)+'</span>';
  }}).join('');
  const neighborLabels = neighborIds.map(nid => {{ const nb = nodesDS.get(nid); return nb ? nb.label : nid; }});
  const descHtml = n._description
    ? '<div class="field" style="margin-top:10px;padding-top:10px;border-top:1px solid #2a2a4e;font-size:12px;color:#ccc"><b>Description</b><br>'+esc(n._description)+'</div>'
    : '';
  document.getElementById('info-content').innerHTML =
    '<div class="field"><b>'+esc(n.label)+badge+'</b></div>' +
    '<div class="field">Type: '+esc(n._file_type||'unknown')+'</div>' +
    '<div class="field">Community: '+esc(n._community_name)+'</div>' +
    '<div class="field">Source: '+esc(n._source_file||'-')+'</div>' +
    '<div class="field">Degree: '+n._degree+'</div>' +
    (n._strategic ? '<div class="field" style="color:#FFD700">STRATEGIC NODE</div>' : '') +
    (neighborIds.length ? '<div class="field" style="margin-top:8px;color:#aaa;font-size:11px">Neighbors ('+neighborIds.length+')</div><div id="neighbors-list">'+neighborItems+'</div>' : '') +
    descHtml;
  }}

function focusNode(nodeId) {{
  network.focus(nodeId, {{ scale: 1.4, animation: true }});
  highlightConnected(nodeId);
  selectedNodeId = nodeId;
  showInfo(nodeId);
}}

network.on('click', p => {{
  if (p.nodes.length > 0) {{
    const nodeId = p.nodes[0];
    if (selectedNodeId === nodeId) {{
      resetHighlight();
      document.getElementById('info-content').innerHTML = '<span class="empty">Click a node to inspect it</span>';
    }} else {{
      selectedNodeId = nodeId;
      highlightConnected(nodeId);
      showInfo(nodeId);
    }}
  }} else {{
    resetHighlight();
    document.getElementById('info-content').innerHTML = '<span class="empty">Click a node to inspect it</span>';
  }}
}});

const searchInput = document.getElementById('search');
const searchResults = document.getElementById('search-results');
searchInput.addEventListener('input', () => {{
  const q = searchInput.value.toLowerCase().trim();
  searchResults.innerHTML = '';
  if (!q) {{ searchResults.style.display = 'none'; return; }}
  const matches = RAW_NODES.filter(n => n.label.toLowerCase().includes(q)).slice(0, 20);
  if (!matches.length) {{ searchResults.style.display = 'none'; return; }}
  searchResults.style.display = 'block';
  matches.forEach(n => {{
    const el = document.createElement('div');
    el.className = 'search-item';
    el.textContent = n.label + (n.strategic ? ' ⭐' : '');
    el.style.borderLeft = '3px solid ' + (n.strategic ? '#FFD700' : n.color.background);
    el.style.paddingLeft = '8px';
    el.onclick = () => {{ focusNode(n.id); searchResults.style.display = 'none'; searchInput.value = ''; }};
    searchResults.appendChild(el);
  }});
}});
document.addEventListener('click', e => {{ if (!searchResults.contains(e.target) && e.target !== searchInput) searchResults.style.display = 'none'; }});

const hiddenCommunities = new Set();
const selectAllCb = document.getElementById('select-all-cb');
function updateSelectAllState() {{
  const total = LEGEND.length, hidden = hiddenCommunities.size;
  selectAllCb.checked = hidden === 0;
  selectAllCb.indeterminate = hidden > 0 && hidden < total;
}}
function toggleAllCommunities(hide) {{
  document.querySelectorAll('.legend-item').forEach(item => hide ? item.classList.add('dimmed') : item.classList.remove('dimmed'));
  document.querySelectorAll('.legend-cb').forEach(cb => cb.checked = !hide);
  LEGEND.forEach(c => {{ if (hide) hiddenCommunities.add(c.cid); else hiddenCommunities.delete(c.cid); }});
  nodesDS.update(RAW_NODES.map(n => ({{ id: n.id, hidden: hide }})));
  updateSelectAllState();
}}
const legendEl = document.getElementById('legend');
LEGEND.forEach(c => {{
  const item = document.createElement('div');
  item.className = 'legend-item';
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.className = 'legend-cb'; cb.checked = true;
  cb.addEventListener('change', (e) => {{
    e.stopPropagation();
    if (cb.checked) {{ hiddenCommunities.delete(c.cid); item.classList.remove('dimmed'); }}
    else {{ hiddenCommunities.add(c.cid); item.classList.add('dimmed'); }}
    nodesDS.update(RAW_NODES.filter(n => n.community === c.cid).map(n => ({{ id: n.id, hidden: !cb.checked }})));
    updateSelectAllState();
  }});
  item.innerHTML = '<div class="legend-dot" style="background:'+c.color+'"></div><span class="legend-label">'+esc(c.label)+'</span><span class="legend-count">'+c.count+'</span>';
  item.prepend(cb);
  item.onclick = (e) => {{ if (e.target === cb) return; cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }};
  legendEl.appendChild(item);
}});
document.getElementById('stats').innerHTML = RAW_NODES.length + ' nodes &middot; ' + RAW_EDGES.length + ' edges &middot; ' + LEGEND.length + ' communities &middot; ' + RAW_NODES.filter(n=>n.strategic).length + ' strategic ⭐';
</script>
</body>
</html>"""

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html)
    strategic_labels = [n["label"] for n in nodes if n.get("strategic")]
    print(f"Enhanced graph.html written to {OUTPUT_HTML}")
    print(f"Strategic nodes ({len(strategic_labels)}): {', '.join(strategic_labels[:20])}")
    if len(strategic_labels) > 20:
        print(f"  ... and {len(strategic_labels) - 20} more")


if __name__ == "__main__":
    main()
