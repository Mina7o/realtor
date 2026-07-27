## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- After `graphify update`, run `python3 scripts/add_runtime_edges.py` to add runtime DB connections (SQL/Mongo table reads/writes inferred from code).
- Then run `python3 scripts/enhance_graph.py` to add strategic node highlighting (gold ⭐ for infrastructure, deals, zoning, commercial, etc.) and meaningful community names.
- The enhanced graph is served at `/code-graph` in the Flask app.

## Project Skills

This project has reusable skills in `.opencode/skills/`:
- **docker** — Docker patterns: `sg docker -c`, compose setup, Tempo v3 quirks, build context management
- **mongodb** — MongoDB queries, common gotchas (string→number types), index strategy
- **ui-ux-pro-max** — 659-line comprehensive design guide: 67 styles, 161 color palettes, 57 font pairings, 99 UX guidelines, 25 chart types across 16 tech stacks. Searchable BM25 engine via `.opencode/skills/ui-ux-pro-max/scripts/search.py`. Domains: style, color, chart, ux, typography, product, icon, app-interface, stack.
- **animate-text** — Curated text animation catalog with 22 effect specs (typewriter, blur-in, stagger, kinetic build, etc.) as portable JSON contracts. Use to pick or translate named effects into WAAPI, Framer Motion, GSAP, CSS, Lottie, or Rive. Node.js scripts: `find-spec.mjs`, `get-effect.mjs`, `get-spec.mjs`, `list-specs.mjs`.

These are auto-discovered by opencode and loadable via the `skill` tool when relevant tasks arise.

## Groq LLM for Graphify

For semantic extraction on docs/images (not needed for code-only updates), use:
```bash
source .env.groq  # sets GROQ_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL, GRAPHIFY_OPENAI_MODEL
graphify extract . --backend openai
```
Groq's Qwen3 32B (`qwen/qwen3-32b`) is ~$0.29/1M input, ~$0.59/1M output. API is OpenAI-compatible — use `client.chat.completions.create()` (NOT `client.responses.create()`, which Groq doesn't support).
