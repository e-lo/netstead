---
title: Build a GMNS network from OpenStreetMap or Overture
audience: users
kind: howto
summary: netstead build turns an area into a validated GMNS node + link network from OpenStreetMap (Overpass) or Overture Maps (GeoParquet), from the CLI or Python.
---

# Build a GMNS network from OpenStreetMap or Overture

## When to use this

You need a GMNS network for a study area and don't have one yet. Point `netstead` at a place name, a bounding box, or a lat/lon + buffer and it fetches the road network — from OpenStreetMap via Overpass (`--source osm`, the default) or from Overture Maps' transportation theme (`--source overture`) — and writes a validated GMNS `node` + `link` package. No `osmnx` or `osm2gmns` required.

Requires the matching optional extra: `pip install 'netstead[osm]'` or `pip install 'netstead[overture]'`.

## Quick example

Build the drivable network for a place and write it as CSV. The area is geocoded via Nominatim, the road network is fetched from Overpass, and the result is a GMNS package on disk:

```bash
netstead build --place "Leavenworth, WA" --network-type roads --source osm --format csv ./leavenworth-osm
```

The command prints a summary (`nodes`, `links`, `spec_version`); add `--json` for a machine-readable document.

## Step-by-step

### 1. Install the extra

```bash
pip install 'netstead[osm]'        # --source osm (default)
pip install 'netstead[overture]'   # --source overture
```

Overture is read as GeoParquet straight through corral's bundled DuckDB, so `[overture]` adds only the mapping-data loader. Geocoding a `--place` string uses Nominatim from the `[osm]` extra, so an Overture build by place name needs both.

### 2. Pick how you specify the area

One of three, mutually exclusive:

```bash
# A place name (city / county / neighborhood) — geocoded to a boundary polygon.
netstead build --place "Leavenworth, WA" ./net

# A bounding box: west,south,east,north (EPSG:4326).
netstead build --bbox -120.67,47.58,-120.64,47.61 ./net

# A point (lat,lon) plus a buffer in metres.
netstead build --point 47.596,-120.66 --buffer 800 ./net
```

### 3. Choose the source

`--source osm` (the default) fetches from Overpass. `--source overture` reads the pinned Overture release from cloud GeoParquet; `--overture-release` moves the pin and `--data-root` points at an alternative base URI or a local snapshot directory holding `segment.parquet` / `connector.parquet`. Both flags are rejected with `--source osm`:

```bash
netstead build --source overture --bbox -120.67,47.58,-120.64,47.61 ./leavenworth-overture
netstead build --source overture --overture-release 2026-09-23.1 --data-root /data/overture-snapshot --bbox -120.67,47.58,-120.64,47.61 ./net
```

### 4. Choose modes and output format

`--network-type` is `roads` (motorway through residential, service and other roads, links and busways), `paths` (footways, paths, pedestrian ways, steps, tracks, cycleways and bridleways), or both: repeat the option or comma-separate (`--network-type roads,paths`). Proposed, construction and corridor ways are never built. The old names still work but are deprecated: `drive` means `roads`; `walk`, `bike` and `all` mean `roads,paths`. Overture builds use the same two types, mapped onto Overture road classes (no busway class exists there). The output format is inferred from the destination extension, or set it explicitly with `--format` (`csv` / `parquet` / `duckdb` / `zip`). A destination with no extension — `./net` in the examples above — is written as a **Parquet** directory; pass `--format csv` for CSV:

```bash
netstead build --place "Leavenworth, WA" --network-type roads,paths --format parquet ./full-net
```

### 5. Validate what you built

The build already produces a schema-valid package; run `netstead validate` any time to confirm and to see the report:

```bash
netstead validate ./leavenworth-osm
```

## Python API

Programmatic equivalent — `netstead.osm.build_network_from_osm` returns a `Network` (a `corral` package) you can validate, scope, or write. The area is a place string, a `(lat, lon)` point with `buffer_m`, or a `(west, south, east, north)` bbox:

<!-- doctest: skip -->
```python
from netstead.osm import build_network_from_osm

net = build_network_from_osm("Leavenworth, WA", network_type="roads")
print(net.links.count(), net.nodes.count())
net.write("leavenworth-osm", format="csv", overwrite=True)
```

`netstead.overture.build_network_from_overture` has the same shape, plus `overture_release` (the pinned release string) and `data_root` (alternative base URI or local snapshot directory). Its `extra_tags` are Overture segment property paths (e.g. `"subclass"`, `"road_surface"`) rather than OSM tag keys:

<!-- doctest: skip -->
```python
from netstead.overture import build_network_from_overture

net = build_network_from_overture((-120.67, 47.58, -120.64, 47.61), network_type="roads")
net.write("leavenworth-overture")   # no extension → Parquet directory
```

If you already have node/link records (e.g. from your own fetch, or to benchmark the build path without the network), assemble a `Network` directly with `netstead.osm.network_from_records`:

```python
from netstead.osm import network_from_records

nodes = [
    {"node_id": 1, "x_coord": -120.66, "y_coord": 47.60},
    {"node_id": 2, "x_coord": -120.65, "y_coord": 47.60},
]
links = [
    {"link_id": 1, "from_node_id": 1, "to_node_id": 2, "directed": True,
     "facility_type": "residential", "geometry": "LINESTRING (-120.66 47.6, -120.65 47.6)"},
    {"link_id": 2, "from_node_id": 2, "to_node_id": 1, "directed": True,
     "facility_type": "residential", "geometry": "LINESTRING (-120.65 47.6, -120.66 47.6)"},
]
net = network_from_records(nodes, links)
print(net.links.count())
```

## How the conversion works

* **Topology.** A GMNS node is created at every OSM intersection (a node shared by two or more ways) and at each way's endpoints. Intermediate shape points are dropped as nodes and kept as the link's WKT geometry; their OSM ids are retained on the link's `osm_node_ids` column for provenance, alongside `osm_way_id`.
* **Direction.** Every link is `directed=True`. A two-way street becomes **two** directed links (one per direction); a oneway street becomes one, honoring `oneway=-1` and implied-oneway (motorways, roundabouts).
* **Attributes.** `name`, `lanes`, `free_speed`, and `facility_type` are mapped from OSM tags via the maintained data file `netstead/osm/mappings/osm_to_gmns.yaml`. Pass `--extra-tags surface,bridge` (or `extra_tags=[...]`) to carry additional OSM tags through as extra columns. Edit the YAML to change or extend the mapping — no code change needed.

## Common variations

???+ note "Point + buffer"
    A circle of road around a coordinate (metres).

    ```bash
    netstead build --point 47.596,-120.66 --buffer 800 ./net
    ```

??? note "Carry extra OSM tags"
    Add columns to the link table verbatim.

    ```bash
    netstead build --bbox -120.67,47.58,-120.64,47.61 --extra-tags surface,bridge ./net
    ```

??? note "Build from a local .osm XML or Overpass JSON file"
    No network access: `build_network_from_osm_file` reads an `.osm` XML file or an Overpass `[out:json]` export (`.json`) and applies the same `network_type` filter and tag mapping. It is Python-only (no `netstead build` flag) and still needs the `[osm]` extra for the mapping loader.

    <!-- doctest: skip -->
    ```python
    from netstead.osm import build_network_from_osm_file

    net = build_network_from_osm_file("leavenworth.osm", network_type="roads", extra_tags=["surface"])
    net.write("leavenworth-osm", format="csv")
    ```

??? note "Build from Overture Maps"
    Same area options; swap the source. Overture link geometry comes from segment shapes and nodes from Overture connectors.

    ```bash
    netstead build --source overture --bbox -120.67,47.58,-120.64,47.61 --format csv ./leavenworth-overture
    ```

??? note "Benchmark the build"
    The `scripts/bench_osm_build.py` harness times the convert + build path (DuckDB is the single compute engine); add `--baselines` for directional osmnx / osm2gmns comparisons.

    ```bash
    uv run python scripts/bench_osm_build.py --grids 10,40,120 --json
    ```

## Pitfalls

* **Units.** GMNS is unit-agnostic — units are declared per-dataset, so the build writes a `config` table stating them: `free_speed` is **mph** (faithful to US OSM `maxspeed` tags; unit-less values are treated as km/h and converted), `length` is geodesic **metres**, `crs` is `EPSG:4326`. Re-unit downstream if your convention differs.
* **Overpass limits.** The public Overpass endpoint rate-limits and times out on large areas. For regional extracts (e.g. a metro), point at a self-hosted endpoint (`endpoint=` in the Python API) rather than hammering the public server.
* **Nominatim policy.** Place-name geocoding uses the public Nominatim, which is not for bulk/automated use. Prefer `--bbox` for scripted/repeated runs.
* **Attribution.** OSM data is ODbL-licensed — attribute OpenStreetMap in anything you publish from these networks. Overture data is ODbL too: derived products must credit "© OpenStreetMap contributors, © Overture Maps Foundation" (see [Overture's attribution page](https://docs.overturemaps.org/attribution/)); netstead reads Overture data and does not redistribute it.
* **`netstead[osm]` / `netstead[overture]` required.** Without the matching extra the command exits with an install hint.

## See also

* [Run validation and read the report](validate-network.md) — interpret the report from `netstead validate`.
* [Run the bundled benchmarks](run-bench.md) — `netstead bench` for load/validate timings on an existing network.
