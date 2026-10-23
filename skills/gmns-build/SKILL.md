---
name: gmns-build
description: Build a GMNS network from OpenStreetMap for a place, bounding box, or point+buffer. Use when the user needs a GMNS node/link network and doesn't have one yet — "make me a network for <city>", "get the OSM roads for this bbox as GMNS", or before validating/scoping data they don't yet possess.
---

# gmns-build

Use this skill when the user wants to *create* a GMNS network from OpenStreetMap,
rather than validate, convert, or edit one they already have. The capability
lives in `netstead.osm` and the `netstead build` CLI command, and needs the optional
`[osm]` extra (`pip install 'netstead[osm]'`).

For *format* conversion of an existing network use `gmns-convert`; for fixing a
built network use `gmns-clean`; to interpret validation use `gmns-validate`.

## Workflow

1. **Confirm the extra is installed.** `pip install 'netstead[osm]'` (pulls
   `requests` + `pyyaml`). Without it the command exits with an install hint.
2. **Pick the area spec — exactly one:**
   - `--place "City, ST"` — geocoded via Nominatim to a boundary polygon.
   - `--bbox west,south,east,north` — EPSG:4326; best for scripted/repeated runs.
   - `--point lat,lon --buffer METRES` — a circle of road around a coordinate.
3. **Pick `--network-type`:** `drive` (default) / `walk` / `bike` / `all`.
4. **Build and write:**
   ```bash
   netstead build --place "Leavenworth, WA" --network-type drive --format csv ./net
   ```
5. **Validate the result:** `netstead validate ./net` — the build is already
   schema-valid, but confirm and surface any warnings.

## Programmatic API

```python
from netstead.osm import build_network_from_osm

net = build_network_from_osm("Leavenworth, WA", network_type="roads")
net.write("net", format="csv", overwrite=True)
```

`build_network_from_osm` accepts a place string, a `(lat, lon)` point (with
`buffer_m=`), or a `(west, south, east, north)` bbox, plus `extra_tags=[...]`,
`spec_version=`, and `engine=`. It returns a `netstead.network.Network`.

## What the conversion does

- A GMNS node is created at OSM intersections (a node shared by 2+ ways) and at
  way endpoints; intermediate shape points become the link's WKT geometry, with
  OSM ids retained on `osm_node_ids` / `osm_way_id`.
- Every link is `directed=True`: a two-way street → two directed links, a oneway
  → one (honoring `oneway=-1` and implied-oneway for motorways/roundabouts).
- `name`, `lanes`, `free_speed`, `facility_type` are mapped from OSM tags via the
  maintained `netstead/osm/mappings/osm_to_gmns.yaml`; `--extra-tags a,b` carries
  additional tags through as columns.

## Pitfalls

- **Units:** `free_speed` is mph; `length` is geodesic metres.
- **Overpass/Nominatim limits:** prefer `--bbox` for repeated runs; point at a
  self-hosted Overpass (`endpoint=` in the API) for regional-scale areas.
- **Attribution:** OSM data is ODbL — attribute OpenStreetMap in published work.
- **Overture:** `--source overture` builds from Overture Maps transportation
  GeoParquet (needs the `overture` extra; `--overture-release` pins a release,
  `--data-root` points at a mirror or local snapshot). `--source osm` is the default.

## See also

- `gmns-validate` — interpret `netstead validate` on the built network.
- `gmns-clean` — simplify geometry / merge nodes / drop orphans with rollback.
- Cookbook: `packages/netstead/docs/cookbook/build-from-osm.md`.
- Benchmark harness: `scripts/bench_osm_build.py`.
