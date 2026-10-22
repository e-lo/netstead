---
title: View a network on a map (with or without findings)
audience: users
kind: howto
summary: Render a self-contained interactive Leaflet map of any GMNS network — useful for sanity-checks, demos, and bug reports. The same renderer is what `netstead validate --html` wraps; here you use it directly, with or without validation overlays.
---

# View a network on a map

## When to use this

You want a one-file, send-it-to-a-colleague visual of a GMNS network — without
having to spin up a Jupyter notebook, a geopandas plot, or a folium map. Maybe
to confirm an OSM build covered the right area, demo the result of an edit,
attach to a bug report, or check that quality-rule findings cluster where you
expect them.

The renderer is the same one `netstead validate --html` uses. Calling it
directly lets you pass any list of [`Issue`][issue] objects (validation,
quality, graph topology, custom) as overlays — or none at all.

## Quick example

Render the bundled Leavenworth fixture to a single HTML file. The renderer lives in `netstead.map`; nothing is overlaid because no issues are passed:

```python
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.map import render_network_html

net = Network.from_source(leavenworth.csv_dir())
open("net.html", "w").write(render_network_html(net, title="Leavenworth"))
```

Open `net.html` in any browser. You'll see the link geometry as a thin
underlay on a Leaflet base map. No overlay markers because no issues were
passed.

## Step-by-step

### 1. Overlay validation findings

`render_validation_html` takes the network and a `ValidationReport` and composes the map with the findings table — it is what `netstead validate --html` writes:

```python
from netstead.map import render_validation_html

report = net.validate()
open("net.html", "w").write(render_validation_html(net, report))
```

Each finding becomes a clickable marker, colour-coded by severity. A **Resources** table above the findings
lists every spec resource (required/optional, present, row count). Cross-cutting findings (no row, no coords)
sit in a collapsed "Unlocated findings" `<details>` block, with its count in the summary — still clickable for
the row-sync when expanded. Findings that share a code and differ only by resource (such as the sixteen
`structural.missing_optional_resource` lines a partial GMNS package produces) are grouped into one line there.

### 2. Overlay anything else

The renderer accepts a flat `list[Issue]`, so any `Issue` source plugs in
without renderer changes:

```python
from corral.reports import Category, Issue, Severity

custom = [
    Issue(
        severity=Severity.WARNING,
        category=Category.DATA_QUALITY,
        code="quality.signage_missing",
        message="no stop sign at intersection 42",
        extra={"lon": -120.66, "lat": 47.59},
    ),
]
open("net.html", "w").write(render_network_html(net, custom))
```

GMNS-specific data-quality rules (`netstead.quality.*`) already produce
`Issue` objects in this shape, so `run_quality(net).issues` drops straight in.

### 3. OpenStreetMap deep links

When the network has `osm_way_id` (and optionally `osm_node_id`) columns —
as produced by `netstead build`, the OSM importer — each marker's popup
carries an "Edit in OSM" link straight to the iD editor at
`openstreetmap.org/edit?editor=id&way=<id>`. Pass `osm_editor="josm"` to
target the desktop JOSM editor instead (via its localhost
RemoteControl plugin).

## Common variations

??? note "Different base map"
    ```python
    render_network_html(net, tile_provider="openstreetmap")  # default is "esri", a keyless light grey
    ```

??? note "Embed in a Jupyter notebook"
    ```python
    from IPython.display import IFrame
    open("/tmp/net.html", "w").write(render_network_html(net, report.issues))
    IFrame("/tmp/net.html", 900, 600)
    ```

??? note "From the CLI"
    `netstead validate --html` writes this same page with the findings overlaid:

    ```bash
    netstead validate <source> --html report.html  # findings overlay
    ```

    For *interactive* viewing — linked map and table, validation, editing —
    open the network in the Workbench instead; this renderer is for a
    self-contained, offline HTML file you can send to someone:

    ```bash
    netstead app <source>
    ```

    See [Explore in the Workbench](workbench.md).

## Pitfalls

* **Requires the `[reports]` extra.** `pip install 'netstead[reports]'`
  (pulls in `jinja2`, `openpyxl` and `pyyaml`). Leaflet itself is vendored —
  no internet required when the report is opened.
* **No marker clustering yet.** Past a few thousand findings the Leaflet
  canvas slows down. Filter the issue list before passing it in.
* **`title=` controls the `<title>` tag.** It does not override how the
  network's spec version / link / node counts appear in the subtitle.

## See also

* [Validate a network](validate-network.md) — `--html` / `--csv` / `--xlsx`
  CLI flags wrap the same renderer.
* [Build from OSM](build-from-osm.md) — produces a network with
  `osm_way_id` columns so the "Edit in OSM" links light up.
* [Customise quality](customise-quality.md) — quality rules feed the same
  `Issue` overlay path.

[issue]: https://e-lo.github.io/netstead/corral/api/reports/#corral.reports.Issue
