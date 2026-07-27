"""Build an interactive force-directed code structure graph."""
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "output/code_graph.html"

NODE_COLORS = {
    'python': '#58a6ff', 'html': '#f0883e', 'csv': '#3fb950',
    'md': '#d2a8ff', 'api': '#79c0ff', 'db_table': '#f85149',
    'template': '#ffc657', 'function': '#56d4dd',
}

STDLIB = {'os','sys','re','json','csv','time','math','sqlite3','pathlib',
    'datetime','copy','io','typing','collections','itertools','functools',
    'shutil','subprocess','tempfile','uuid','dataclasses','enum','threading',
    'random','statistics','hashlib','base64','textwrap','string','logging',
    'warnings','traceback','pprint','inspect','pickle','shelve','configparser',
    'argparse','glob','fnmatch','abc','decimal','fractions','numbers','operator',
    'weakref','builtins','http','urllib','xml','html','email','zipfile','tarfile',
    'struct','socket','ssl','select','asyncio','signal','mmap','ctypes','platform'}

BLACKLIST_DIRS = {'venv', 'cache', 'output', '.opencode', '__pycache__', 'node_modules'}
STRATEGIC_KEYWORDS = {'roi', 'nuclear', 'grid', 'assembly', 'alpha', 'loi', 'jda', 'power', 'interconnect', 'shortlist'}
UTILITY_PREFIXES = {'_', 'parse_', 'format_', 'load_', 'save_', 'init_', 'test_', 'helper_'}

def is_strategic(name):
    name_lower = name.lower()
    return any(k in name_lower for k in STRATEGIC_KEYWORDS)

# ... (stdlib and constants) ...

# Group Tiering for Hierarchical Layout
GROUP_TIERS = {
    'md': 100,        # Top: Strategy & Directives
    'api': 300,       # Middle-Top: API Routes
    'html': 400,      # Middle: Frontend
    'template': 400,
    'python': 600,    # Middle-Bottom: Logic Scripts
    'function': 700,  # Bottom-Mid: Specific Logic
    'db_table': 900,  # Bottom: Data Source
    'csv': 1000       # Bottom: Data Output
}

def add_node(name, group, filepath=None, size=5):
    key = f'{group}::{name}'
    if key not in seen:
        seen.add(key)
        color = NODE_COLORS.get(group, '#8899aa')
        
        # Strategic Nodes: Gold and Massive
        is_strat = is_strategic(name)
        if is_strat:
            color = '#FFD700'
            size = size * 2.5
        
        nodes.append({
            'id': name, 
            'group': group,
            'tier': GROUP_TIERS.get(group, 500), # Default to middle
            'is_strategic': is_strat,
            'file': str(filepath.relative_to(ROOT)) if filepath else '',
            'size': size, 
            'color': color
        })

def add_edge(source, target, label=None):
    # Boss Move: Prune 'import' noise from the VISUAL graph
    # We still keep the metadata, but we only draw 'Strategic' lines
    if label == 'import': return 
    edges.append({'source': source, 'target': target, 'label': label})

# ... (crawling logic) ...

# In the HTML Generation (D3 Logic Update):
'''
var sim = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.edges).id(function(d){return d.id}).distance(100).strength(0.1))
  .force("charge", d3.forceManyBody().strength(-200))
  .force("center", d3.forceCenter(w/2, h/2))
  .force("y", d3.forceY(function(d){return d.tier}).strength(1.0)) // THE SUPERPOWER: Hierarchical Pinning
  .force("x", d3.forceX(w/2).strength(0.05))
  .force("collision", d3.forceCollide().radius(function(d){return Math.sqrt(d.size)*5+15}));
'''

seen = set()
nodes = []
edges = []

module_map = {}
for py_file in sorted(ROOT.rglob('*.py')):
    if any(b in str(py_file) for b in BLACKLIST_DIRS):
        continue
    rel = str(py_file.relative_to(ROOT))
    module_map[py_file.stem] = rel

for py_file in sorted(ROOT.rglob('*.py')):
    if any(b in str(py_file) for b in BLACKLIST_DIRS):
        continue
    rel = str(py_file.relative_to(ROOT))
    add_node(rel, 'python', py_file, 15)
    text = py_file.read_text()
    
    # ... (imports/routes/templates/queries) ...

    # Refined Function Extraction (Skip utility helpers)
    for m in re.finditer(r'def\s+(\w+)\s*\(', text):
        func_name = m.group(1)
        if func_name in ('main', 'init'): continue
        
        # Only index if it's strategic OR doesn't match utility prefixes
        if is_strategic(func_name) or not any(func_name.startswith(p) for p in UTILITY_PREFIXES):
            add_node(func_name, 'function', py_file, 5)
            add_edge(rel, func_name, 'defines')

for hf in sorted(ROOT.rglob('*.html')):
    if any(b in str(hf) for b in BLACKLIST_DIRS): continue
    rel = str(hf.relative_to(ROOT))
    # ... (html logic) ...

# Skip noisy CSV files unless they are the Shortlist
for cf in sorted(ROOT.rglob('*.csv')):
    if any(b in str(cf) for b in BLACKLIST_DIRS): continue
    if 'shortlist' in cf.name.lower():
        add_node(str(cf.relative_to(ROOT)), 'csv', cf, 8)

# Refined Markdown Indexing (Only Directives and Docs)
for mf in sorted(ROOT.rglob('*.md')):
    if any(b in str(mf) for b in BLACKLIST_DIRS): continue
    if not (mf.parent.name in ('docs', 'directives') or 'directive' in mf.name.lower()):
        continue
    rel = str(mf.relative_to(ROOT))
    add_node(rel, 'md', mf, 7)
    # ... (wiki links) ...

# ... (imports and constants) ...

# Final Assembly and Dead-Code Pruning
node_ids = {n['id'] for n in nodes}
# Initial edge filter for existing nodes
edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]

# Boss Move: Connectivity Audit
# Calculate 'degree' (total connections) for every node
connectivity = {nid: 0 for nid in node_ids}
for e in edges:
    connectivity[e['source']] += 1
    connectivity[e['target']] += 1

# THE PURGE: Keep only connected nodes OR strategic nodes
# If a node is isolated and not Gold, it's trash.
nodes = [n for n in nodes if connectivity.get(n['id'], 0) > 0 or n.get('is_strategic')]

# Final node set after purge
node_ids = {n['id'] for n in nodes}
edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]

# ... (HTML writing logic) ...
D3_SRC = ""
if D3_PATH.exists():
    import base64
    D3_SRC = "src=\"data:application/javascript;base64," + base64.b64encode(D3_PATH.read_bytes()).decode() + "\""

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Code Structure Graph</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:#0f1419;color:#e1e8ed;overflow:hidden}
#graph{width:100vw;height:100vh}
#info{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1a1f2ee0;border:1px solid #2a3040;border-radius:8px;padding:8px 16px;font-size:11px;color:#8899aa;pointer-events:none;z-index:100;backdrop-filter:blur(8px)}
#legend{position:fixed;top:20px;right:20px;background:#1a1f2ee0;border:1px solid #2a3040;border-radius:8px;padding:10px 14px;font-size:11px;z-index:100;backdrop-filter:blur(8px)}
#legend div{display:flex;align-items:center;gap:8px;margin:3px 0}
#legend .d{width:10px;height:10px;border-radius:50%;flex-shrink:0}
#stats{position:fixed;bottom:20px;right:20px;background:#1a1f2ee0;border:1px solid #2a3040;border-radius:8px;padding:6px 12px;font-size:10px;color:#8899aa;z-index:100;backdrop-filter:blur(8px)}
#search{position:fixed;top:20px;left:20px;z-index:100}
#search input{background:#1a1f2ee0;border:1px solid #2a3040;border-radius:6px;padding:6px 10px;font-size:12px;color:#e1e8ed;width:180px;outline:none;backdrop-filter:blur(8px)}
#search input:focus{border-color:#1d9bf0}
#search-results{position:fixed;top:56px;left:20px;background:#1a1f2ee0;border:1px solid #2a3040;border-radius:6px;max-height:300px;overflow-y:auto;display:none;z-index:100;backdrop-filter:blur(8px);min-width:200px}
#search-results div{padding:6px 10px;font-size:11px;cursor:pointer;border-bottom:1px solid #2a3040}
#search-results div:hover{background:#2a3040}
</style>
</head>
<body>
<div id="legend">
  <div><span class="d" style="background:#3572A5"></span> Python</div>
  <div><span class="d" style="background:#E34F26"></span> HTML</div>
  <div><span class="d" style="background:#218B7A"></span> CSV</div>
  <div><span class="d" style="background:#083FA1"></span> Markdown</div>
  <div><span class="d" style="background:#1d9bf0"></span> API Route</div>
  <div><span class="d" style="background:#f4212e"></span> DB Table</div>
  <div><span class="d" style="background:#ffad1f"></span> Template</div>
  <div><span class="d" style="background:#00ba7c"></span> Function</div>
</div>
<div id="search"><input id="search-input" type="text" placeholder="Search nodes... (Ctrl+F)" oninput="searchNodes(this.value)"></div>
<div id="search-results"></div>
<div id="graph"></div>
<div id="info">Scroll=zoom · Drag=pan · Click node=highlight · Drag node=reposition</div>
<div id="stats">''' + str(len(nodes)) + ''' nodes · ''' + str(len(edges)) + ''' edges</div>
<script ''' + D3_SRC + '''></script>
<script>
var data = ''' + json.dumps({'nodes': nodes, 'edges': edges}) + ''';
var w = window.innerWidth, h = window.innerHeight;
var svg = d3.select("#graph").append("svg").attr("width",w).attr("height",h);
var g = svg.append("g");
svg.call(d3.zoom().scaleExtent([0.1,5]).on("zoom", function(e){g.attr("transform",e.transform)}));
function r(s){return s*0.7}
var sim = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.edges).id(function(d){return d.id}).distance(70).strength(0.25))
  .force("charge", d3.forceManyBody().strength(-150))
  .force("center", d3.forceCenter(w/2,h/2))
  .force("collision", d3.forceCollide().radius(function(d){return r(d.size)+4}));
var link = g.selectAll("line").data(data.edges).join("line")
  .attr("stroke","#fff").attr("stroke-width",0.6).attr("stroke-opacity",0.15)
  .style("cursor","pointer")
  .on("mouseover",function(e,d){
    var src = typeof d.source==='object'?d.source.id:d.source;
    var tgt = typeof d.target==='object'?d.target.id:d.target;
    var grp = d.label?' <span style="color:#ffad1f">['+d.label+']</span>':'';
    ltt.style("display","block")
      .html('<b>'+src+'</b> '+grp+'<br><span style="color:#8899aa;font-size:10px">→ </span><b>'+tgt+'</b>')
      .style("left",(e.pageX+10)+"px").style("top",(e.pageY-10)+"px")
  })
  .on("mouseout",function(){ltt.style("display","none")});
var linkHover = g.selectAll("line.hover").data(data.edges).join("line")
  .attr("stroke","transparent").attr("stroke-width",8).attr("pointer-events","all")
  .style("cursor","pointer")
  .on("mouseover",function(e,d){
    var src = typeof d.source==='object'?d.source.id:d.source;
    var tgt = typeof d.target==='object'?d.target.id:d.target;
    var grp = d.label?' <span style="color:#ffad1f">['+d.label+']</span>':'';
    d3.select(this).attr("stroke","#fff").attr("stroke-opacity",0.3);
    ltt.style("display","block")
      .html('<b>'+src+'</b> '+grp+'<br><span style="color:#8899aa;font-size:10px">→ </span><b>'+tgt+'</b>')
      .style("left",(e.pageX+10)+"px").style("top",(e.pageY-10)+"px")
  })
  .on("mouseout",function(){d3.select(this).attr("stroke","transparent");ltt.style("display","none")});
var node = g.selectAll("circle").data(data.nodes).join("circle")
  .attr("r",function(d){return r(d.size)})
  .attr("fill",function(d){return d.color})
  .attr("stroke","#fff").attr("stroke-width",1.2).attr("stroke-opacity",0.5)
  .style("cursor","pointer")
  .on("click",function(e,d){
    var act = d3.select(this).attr("stroke")==="#ffad1f";
    node.attr("stroke","#fff").attr("stroke-width",1.2).attr("stroke-opacity",0.5).attr("opacity",0.25);
    link.attr("stroke","#fff").attr("stroke-opacity",0.04);linkHover.attr("stroke","transparent").attr("stroke-opacity",0);
    if(!act){
      d3.select(this).attr("stroke","#ffad1f").attr("stroke-width",3).attr("opacity",1);
      var connected = {};
      data.edges.forEach(function(e){
        if(e.source.id===d.id||e.target.id===d.id){connected[e.source.id]=1;connected[e.target.id]=1}
      });
      node.filter(function(n){return connected[n.id]}).attr("opacity",0.85);
      link.filter(function(l){return l.source.id===d.id||l.target.id===d.id}).attr("stroke","#ffad1f").attr("stroke-opacity",0.45);
    } else {node.attr("opacity",1);link.attr("stroke","#fff").attr("stroke-opacity",0.15);linkHover.attr("stroke","transparent")}
  })
  .on("mouseover",function(e,d){
    tt.style("display","block")
      .html('<b>'+d.id+'</b>'+(d.file?'<br><span style="color:#8899aa;font-size:10px">'+d.file+'</span>':'')+'<br><span style="color:#8899aa;font-size:10px">'+d.group+'</span>')
      .style("left",(e.pageX+10)+"px").style("top",(e.pageY-10)+"px")
  })
  .on("mouseout",function(){tt.style("display","none")});
var tt = d3.select("body").append("div")
  .style("position","fixed").style("display","none")
  .style("background","#1a1f2ee0").style("border","1px solid #2a3040")
  .style("border-radius","6px").style("padding","6px 10px")
  .style("font-size","11px").style("color","#e1e8ed")
  .style("pointer-events","none").style("z-index","200")
  .style("backdrop-filter","blur(8px)").style("max-width","280px");
var ltt = d3.select("body").append("div")
  .style("position","fixed").style("display","none")
  .style("background","#1a1f2ee0").style("border","1px solid #ffad1f")
  .style("border-radius","6px").style("padding","6px 10px")
  .style("font-size","11px").style("color","#e1e8ed")
  .style("pointer-events","none").style("z-index","200")
  .style("backdrop-filter","blur(8px)")  .style("max-width","320px");
node.call(d3.drag()
  .on("start",function(e,d){if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y})
  .on("drag",function(e,d){d.fx=e.x;d.fy=e.y})
  .on("end",function(e,d){if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}));
sim.on("tick",function(){
  link.attr("x1",function(d){return d.source.x}).attr("y1",function(d){return d.source.y})
      .attr("x2",function(d){return d.target.x}).attr("y2",function(d){return d.target.y});
  linkHover.attr("x1",function(d){return d.source.x}).attr("y1",function(d){return d.source.y})
      .attr("x2",function(d){return d.target.x}).attr("y2",function(d){return d.target.y});
  node.attr("cx",function(d){return d.x}).attr("cy",function(d){return d.y})
});
function searchNodes(q){
  var el = document.getElementById("search-results");
  el.innerHTML = ""; el.style.display = "none";
  if(!q) return;
  var matches = data.nodes.filter(function(n){return n.id.toLowerCase().includes(q.toLowerCase())}).slice(0,30);
  if(!matches.length) return;
  el.style.display = "block";
  matches.forEach(function(n){
    var d = document.createElement("div");
    d.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+n.color+';margin-right:6px"></span>'+n.id;
    d.onclick = function(){
      var found = node.filter(function(d2){return d2.id===n.id});
      if(found.size()){
        node.attr("opacity",0.25);
        link.attr("stroke-opacity",0.04);
        found.attr("opacity",1).attr("stroke","#ffad1f").attr("stroke-width",3);
        var connected = {};
        data.edges.forEach(function(e){
          if(e.source.id===n.id||e.target.id===n.id){connected[e.source.id]=1;connected[e.target.id]=1}
        });
        node.filter(function(n2){return connected[n2.id]}).attr("opacity",0.85);
        link.filter(function(l){return l.source.id===n.id||l.target.id===n.id}).attr("stroke","#ffad1f").attr("stroke-opacity",0.45);
        el.style.display = "none";
        document.getElementById("search-input").value = "";
      }
    };
    el.appendChild(d);
  });
}
document.addEventListener("keydown",function(e){if(e.ctrlKey&&e.key==="f"){e.preventDefault();document.getElementById("search-input").focus()}});
</script>
</body>
</html>'''

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(HTML)
print(f"Graph: {OUTPUT}")
print(f"  {len(nodes)} nodes, {len(edges)} edges")
