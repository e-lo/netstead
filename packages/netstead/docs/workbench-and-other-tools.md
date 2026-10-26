---
title: The Workbench and other tools
audience: users
kind: concept
summary: How the Netstead Workbench differs from GeoLibre, QGIS, ArcGIS and the GMNS modelling tools, and how to use them together.
---

# The Workbench and other tools

## What it is

The Netstead Workbench is a **network session**, not a GIS. It holds one or more GMNS networks in a live
DuckDB session and gives them a map and linked tables. What it adds that a GIS does not:

* **Spec-aware tables.** Foreign keys from the GMNS spec drive navigation, related-row tints, validation and
  cascading deletes across `link`, `node`, `lane`, `segment` and the rest.
* **Selections that resolve.** A phrase such as "I-90 EB between Exit 85 and Exit 87" becomes a path with
  anchors, candidates, confidence and diagnostics, and can be pinned, saved and exported.
* **Edits with an audit trail.** Every change is a typed action with undo, lineage, "copy as Python" and,
  through plugins, ProjectCard output.
* **Validation on the map.** Issues link to rows and markers; a fix is an edit, not a redraw.

What it deliberately leaves to other tools: cartography beyond color-by, width-by and filter; context layers
such as aerials, land use or census data; printing; 3D; and running a model.

## Why we have it

Transportation networks are relational, and GIS tools see one layer at a time. The Workbench exists so that
the relational work (does this lane belong to that link, does this edit break a movement, which links does
this phrase mean) happens in one place with a map attached, and so that the result can be handed to a GIS
or a model in a format it already reads.

## Mental model

Three roles, one network:

| Role | Tool | Owns |
|---|---|---|
| Network session | Netstead Workbench | tables, selection, validation, edits, history |
| GIS canvas | GeoLibre, QGIS, ArcGIS Pro | symbology, legends, context layers, print, 3D |
| Model engine | AequilibraE, DTALite, Path4GMNS, commercial packages | assignment, simulation, skims |

The hand-off between roles is a file. A network saved from the Workbench as Parquet is a directory of one
GeoParquet file per table, which every GIS canvas and most model engines open directly. Save it from
**Edits → Save a copy** with the Parquet format, or from Python:

<!-- doctest: skip -->
```python
from netstead import Network
from netstead.fixtures import leavenworth

net = Network.from_source(leavenworth.csv_dir())
net.write("./leavenworth.parquet")
```

Expected: a `leavenworth.parquet/` directory holding `link.parquet`, `node.parquet`, `geometry.parquet` and the
other tables the network carries. Shapes live wherever the network keeps them: on `link` when it has its own
geometry column, otherwise in `geometry` keyed by `geometry_id`, with nodes as `x_coord` / `y_coord` points.

## How it relates to ...

### GeoLibre

[GeoLibre](https://github.com/opengeos/GeoLibre) is a client-side GIS from the
[opengeos](https://github.com/opengeos) group: MapLibre and deck.gl rendering, DuckDB-WASM for import and SQL,
a large toolbox compiled to WebAssembly, and the same React app shipped as a desktop program, a web app and a
Jupyter widget. Its plugins are JavaScript bundles that add panels, controls and layers; Python drives it as a
widget but cannot extend its UI.

The two are complementary rather than competing. GeoLibre is the better canvas: categorised and graduated
symbology, legends, print layouts, basemaps, time slider, 3D, and any GeoParquet, COG, PMTiles or service layer
dropped beside the network. The Workbench is the better session: it keeps the network's tables, selection and
edit history, which GeoLibre's per-layer feature model has no place for.

Use them together by saving from the Workbench and opening in GeoLibre, on the desktop or from a notebook
with its `add_geoparquet`. A thin GeoLibre plugin that connects to a running `netstead app` and mirrors the
selection both ways is a natural later step; the Workbench's action API is designed to be driven that way.

### QGIS

[QGIS](https://qgis.org) reads GeoParquet natively in recent releases (3.36 and later, where its GDAL build
includes the Arrow driver) and CSV with WKT through its delimited-text layer. It is the usual desktop home
for cartography and for joining a network to parcels, zones or counts. The GMNS community's
[NeXTA4GMNS](https://github.com/asu-trans-ai-lab/NeXTA4GMNS) notes describe viewing and editing GMNS in QGIS.

Edits made in QGIS do not come back with lineage. Make attribute edits in the Workbench and use QGIS for
geometry work and maps.

### ArcGIS Pro

[ArcGIS Pro](https://www.esri.com/en-us/arcgis/products/arcgis-pro/overview) reads GeoParquet from 3.3 on,
and its Network Analyst extension builds routable network datasets from feature classes. The Workbench is
the place to validate and fix a GMNS network before it becomes such a dataset; ArcGIS is the place to publish
it as a feature service or run Network Analyst solvers on it.

### Network Wrangler and ProjectCard

[Network Wrangler](https://github.com/network-wrangler/network_wrangler) manages scenarios as a base network
plus an ordered set of [ProjectCards](https://github.com/network-wrangler/projectcard). Netstead reads and
writes the card format rather than depending on Wrangler's pandas stack: Workbench selections export as card
facility objects, the fix workflow downloads a card, and the project-cards plugin (`plugins/project-cards` in the
repository) authors cards from Workbench edits.
See [Triage and fix findings](cookbook/fix-findings.md) for the field mapping.

### Model engines

* [AequilibraE](https://www.aequilibrae.com) imports a network with `create_from_gmns` (link, node,
  use_group and geometry tables) and exports with `export_to_gmns`. Validate in the Workbench, then build the
  AequilibraE project from the saved CSV folder.
* [DTALite](https://github.com/asu-trans-ai-lab/DTALite) and [Path4GMNS](https://github.com/jdlph/Path4GMNS)
  read `node.csv` and `link.csv` directly; save as CSV and point them at the folder.
* Commercial packages (Cube, Visum, TransCAD, Emme) generally import GMNS link and node tables through their
  own scripting interfaces rather than a native reader. [What is GMNS?](what-is-gmns.md) explains why the
  spec exists as the common target.

### Builders and notebook maps

[osm2gmns](https://github.com/jiawlu/OSM2GMNS) and `osmnx` build GMNS from OpenStreetMap, as does
[`netstead build`](cookbook/build-from-osm.md). For a quick map inside a notebook without the Workbench,
[View a network on a map](cookbook/view-your-network.md) covers the built-in viewer, and a GeoDataFrame from
`netstead[clean]` drops into lonboard, pydeck or kepler.gl.

## See also

* [Explore networks in the Netstead Workbench](cookbook/workbench.md)
* [Write a Workbench plugin](cookbook/workbench-plugins.md)
* [What is GMNS?](what-is-gmns.md)
