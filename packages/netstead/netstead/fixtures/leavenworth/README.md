# Leavenworth, WA — example GMNS network

## What this is

A small but realistic example network in [GMNS 0.97](https://github.com/zephyr-data-specs/GMNS) format for the historic Bavarian-themed downtown core of Leavenworth, Washington. Bundled with `netstead` so tests, examples, and the 5-minute getting-started doc all share a single canonical fixture. Provided in four storage variants (CSV, Parquet, DuckDB, zipped CSV) holding identical data, so format adapters can roundtrip and assert equality.

## Why Leavenworth

- **Visually distinctive small town.** The downtown core is a compact, walkable grid surrounded by mountains; easy to recognize on a map.
- **Realistic topology.** A handful of tertiary streets (Highway 2 / Front Street), one-way couplets, and a few signalized intersections — enough to exercise lane counts, TOD restrictions, and signal tables without the bloat of a regional network.
- **Small enough to load instantly.** Total fixture footprint is well under 5 MB, so it can ride along inside the netstead wheel without bloating pip installs.

## Coverage

| GMNS table              | Rows | Notes                                                                                |
| ----------------------- | ---- | ------------------------------------------------------------------------------------ |
| `node`                  | 121  | `ctrl_type` exercises 5 of the 7 v0.97 shared-categories values (incl. `signal`); `osm_node_id` carries OSM provenance |
| `link`                  | 339  | `bike_facility` / `ped_facility` / `parking` use v0.97 shared-categories enums; `osm_way_id` carries OSM provenance |
| `geometry`              | 339  | One WKT LineString per link (true OSM shape where present, else straight)            |
| `lane`                  | 429  | One row per travel lane per link                                                     |
| `use_definition`        | 5    | `auto`, `truck`, `transit`, `bike`, `walk`                                           |
| `use_group`             | 2    | `motorized`, `nonmotorized`                                                          |
| `time_set_definitions`  | 1    | `weekday_am_peak` (Mon-Fri 07:00-09:00)                                              |
| `link_tod`              | 1    | AM-peak parking removal on a downtown tertiary link                                  |
| `signal_controller`     | 2    | One per OSM-tagged signalized intersection                                           |

That's **9 distinct table types** including a TOD restriction and several uses of the v0.97 `shared_categories` enums (`ctrl_type`, `bike_facility_categories`, `ped_facility_categories`, `parking_categories`).

## Provenance

The bundled data was synthesized from OpenStreetMap via [`osmnx`](https://github.com/gboeing/osmnx) using this exact call:

```python
osmnx.graph_from_place(
    "Leavenworth, Washington, USA",   # resolves via Nominatim to the city polygon
    network_type="roads",
)
```

The `graph_from_place` call follows OSM's city-relation polygon for
Leavenworth rather than a bbox-clipped buffer around the geocoded
centroid, so the fixture covers the whole town (not just the downtown
core). Each link / node row carries its source OSM id in `osm_way_id` /
`osm_node_id` so validation findings can deep-link into the iD editor.

GMNS attributes are derived from OSM tags as follows:

| GMNS attribute        | OSM source                                                    |
| --------------------- | ------------------------------------------------------------- |
| `node.x_coord/y_coord`| OSM node `x`, `y` (WGS84)                                     |
| `node.ctrl_type`      | `highway=traffic_signals` -> `signal`; `highway=stop` -> `stop`; otherwise spread by node_id mod 7 across `stop_2_way`/`stop_4_way`/`yield`/`no_control` |
| `link.from/to_node_id`| OSM edge endpoints                                            |
| `link.length`         | OSM edge `length` attribute (meters)                          |
| `link.lanes`          | OSM `lanes` (default 1)                                       |
| `link.free_speed`     | OSM `maxspeed` parsed from `mph`/`km/h`; class default if absent |
| `link.facility_type`  | `highway` mapped via the table in `scripts/build_leavenworth.py` |
| `link.bike_facility`  | conservative default `none` (Leavenworth has shared-lane signage downtown but OSM doesn't tag it reliably) |
| `link.ped_facility`   | `service` -> `none`; everything else `sidewalk`                |
| `link.parking`        | `service` -> `none`; everything else `parallel`                |
| `geometry.geometry`   | OSM edge geometry (WKT), falling back to straight from->to    |

Node and link IDs are sequential integers assigned in sorted-OSM-id order to keep output byte-deterministic.

### How to regenerate

```bash
# install fixture-build extras (only needed to *rebuild*; not for *consuming*)
uv sync --extra dev-fixtures

# run the build
uv run python packages/netstead/netstead/fixtures/leavenworth/scripts/build_leavenworth.py
```

**Deterministic given an unchanged OSM snapshot.** Output is byte-identical when re-run against the same `osmnx` response (line terminators pinned, Parquet statistics disabled, zip member timestamps fixed, row + column order stable, ID assignment derived from sorted OSM ids). OSM itself drifts over time, so a real re-run will produce diffs — those reflect upstream changes, not script bugs. Treat any unexplained diff on a fresh run as expected OSM churn.

The build script is the **only** source of truth for the bundled files. Don't hand-edit the CSV/Parquet/DuckDB/zip outputs — re-run the build script.

### Future improvements

- **Pin an OSM snapshot** (e.g. a Geofabrik `*.osm.pbf` extract committed to a release asset) so the fixture is truly byte-deterministic across rebuilds. Currently out of scope; tracked as a follow-up.

## Spec version

Built against **GMNS 0.97** (vendored at `packages/netstead/netstead/spec/0.97/`). Uses these v0.97-specific features:

- `shared_categories.json` enum values for `ctrl_type`, `bike_facility_categories`, `ped_facility_categories`, `parking_categories`
- `link_tod` table with `timeday_id` FK into `time_set_definitions`

## Use

```python
from netstead.fixtures import leavenworth
import pandas as pd

# Where the bundled files live
print(leavenworth.csv_dir())            # .../csv/
print(leavenworth.parquet_dir())        # .../parquet/
print(leavenworth.duckdb_path())        # .../leavenworth.duckdb
print(leavenworth.zip_path())           # .../leavenworth.csv.zip
print(leavenworth.DATAPACKAGE)          # .../datapackage.json

# Quick load
nodes = pd.read_csv(leavenworth.csv_dir() / "node.csv")
links = pd.read_csv(leavenworth.csv_dir() / "link.csv")
```

To get a `Network` directly instead, use `leavenworth.load()` (CSV by default; `"parquet"` and `"duckdb"` also accepted).

## License / attribution

OpenStreetMap data is © OpenStreetMap contributors and licensed under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/). When using or redistributing this fixture (or anything derived from it) you must credit OpenStreetMap and apply the ODbL to derivative datasets.
