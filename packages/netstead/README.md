# Netstead

Python toolkit for the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS) — Zephyr Foundation's open standard for routable transportation network data.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/e-lo/netstead/blob/main/packages/netstead/docs/notebooks/quickstart.ipynb) — try the 5-cell quickstart in your browser, no install.

**Status:** v1.0 beta (1.0.0b2).

## What it provides

- **Read/write** GMNS networks from CSV, Parquet (partitioned), DuckDB, zipped CSV, or remote URLs (with credentials).
- **Validation** — schema, structural, foreign-key, sync-state (out-of-sync awareness on write), and configurable data-quality rules.
- **Network-aware scoping** — bbox, polygon, BFS / shortest-path subgraph, network-distance buffer, spatial buffer from any link/point/node.
- **Build from open data** (`netstead[osm]` / `netstead[overture]`) — `netstead build --source osm|overture` turns a place, bbox, or point + buffer into a validated GMNS network.
- **Editing** (`netstead[clean]`) — simplify geometry, merge close nodes, remove orphans, recompute lengths, with atomic rollback + audit log.
- **Browser Workbench** (`netstead app`, `netstead[server]`) — linked map and tables, validation with proposed fixes, selection verification, a natural-language assistant; extensible with pip-installable plugins.
- **Natural-language selection** (`netstead[nl]`) — `netstead select` resolves plain-English descriptions to link / node ids via Anthropic, OpenAI, Gemini, or a local Ollama model.
- **Self-hostable API server** (`netstead[server]`) — FastAPI + auto-OpenAPI for spinning up your own GMNS data service.
- **AI accessibility** (`netstead[mcp]`) — `netstead mcp serve` MCP server; Skills in the monorepo `skills/` directory.
- **CLI, notebook, Workbench, and programmatic API** — same operations, four surfaces.

## Install

Requires Python 3.11+. `netstead` 1.0.0b2 is not on PyPI yet (the packages were renamed from
`gmnspy` / `datagrove` in October 2026; the first release under the new names is pending), so install
the beta from GitHub, naming both packages:

```bash
# uv (recommended) — `uv pip install` and `pip install` take the same two arguments
uv add \
  "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral" \
  "netstead[all] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"
```

Once the PyPI release is out this becomes `uv add netstead` / `pip install netstead` / `pipx install netstead`,
and `corral` comes along automatically.

### Optional extras

The names below go inside the brackets of the install line above (`netstead[clean,graph] @ git+…`). The
short `uv add 'netstead[<extra>]'` forms work once the PyPI release is out.

```bash
uv add 'netstead[osm]'          # build a GMNS network from OpenStreetMap (requests + pyyaml)
uv add 'netstead[overture]'     # build a GMNS network from Overture Maps (pyyaml; place names also need [osm])
uv add 'netstead[clean]'        # network editing + cleanup (shapely + geopandas + pyproj)
uv add 'netstead[graph]'        # scope from nodes, connected components, quality rules (scipy + numpy + pandas)
uv add 'netstead[server]'       # self-hostable HTTP server + the Workbench (FastAPI + uvicorn)
uv add 'netstead[mcp]'          # MCP server for Claude Desktop / Code
uv add 'netstead[nl]'           # natural-language selection + LLM providers (jsonschema + httpx + keyring)
uv add 'netstead[bench]'        # process-memory metrics for netstead bench (psutil)
uv add 'netstead[notebook]'     # reserved for future notebook widgets (ipywidgets + anywidget)
uv add 'netstead[reports]'      # HTML map viewer + XLSX findings export (openpyxl + jinja2 + pyyaml)
uv add 'netstead[all]'          # everything above
```

Combine extras with commas: `uv add 'netstead[clean,server,mcp]'`.

> **Note:** the `_repr_html_` cards for `Network` / `ValidationReport` /
> `EditResult` work **without** `[notebook]` — that extra is reserved for
> future interactive widgets.

## Quickstart

```python
import netstead
from netstead.fixtures import leavenworth     # bundled example network

net = netstead.read(leavenworth.csv_dir())     # auto-detects format
report = netstead.validate(net)                # schema + FK + data-quality
print(report)                                # rich console
# report.to_html("report.html")              # writes interactive single-file HTML

# Network-aware scope: BFS subgraph + half-mile network buffer
# sub = net.scope.from_nodes([1, 2, 3], path_between=True).buffer_network("0.5mi")
# sub.write("subset.parquet")
```

## Repo

Developed in the [Netstead monorepo](https://github.com/e-lo/netstead) under `packages/netstead/`.
