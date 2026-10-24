# Market Street, San Francisco: Overture example network

## What this is

A tiny, topologically busy GMNS 0.97 network around Lotta's Fountain in downtown San Francisco, where Market Street, Kearny Street, Geary Street and 3rd Street meet at odd angles, with one-way streets, alleys and short links. It is built end to end with netstead's own Overture builder, so it carries Overture GERS ids and is the fixture for the Workbench Find tab's "Overture GERS" id system.

| | |
| --- | --- |
| Bounding box (west, south, east, north; WGS84) | `-122.4056,37.7866,-122.4016,37.7894` (about 350 m by 310 m, centered near -122.4036, 37.7880) |
| Overture release | `2026-09-23.1` (the builder's default pin), transportation theme |
| Network type | `roads` |
| Counts | 129 nodes, 186 links (48 Overture segments, 129 connectors); `config` has 1 row |
| Facility types | secondary 54, tertiary 53, residential 47, service 29, living_street 3 |
| Size | about 180 KB across csv, parquet, zip and datapackage.json |

Forms: `csv/`, `parquet/`, `market_street.csv.zip` and `datapackage.json` (no DuckDB file: its block overhead alone is about 1 MB for a network this small).

## GERS columns

- `link.overture_segment_id`: the Overture segment's GERS id. A segment cut at connectors becomes several sub-segments, and a two-way segment becomes two directed links, so this id repeats across links (186 links, 48 distinct ids).
- `link.overture_connector_ids`: the two connector ids of the link's ends, comma separated (from, to).
- `node.overture_connector_id`: the node's connector GERS id (unique, one per node).

Links may extend slightly past the bounding box: Overture segments that cross the box are kept whole, so the nodes span about -122.4069 to -122.4010 and 37.7863 to 37.7900.

## Rebuild

```bash
uv run --all-extras python packages/netstead/netstead/fixtures/market_street/scripts/build_market_street.py
```

The equivalent CLI call (CSV shown; the script writes all forms):

```bash
netstead build --source overture --overture-release 2026-09-23.1 \
  --bbox -122.4056,37.7866,-122.4016,37.7894 --network-type roads --format csv ./market-street
```

Overture releases are immutable, so a rebuild against the same release gives the same rows. Adding `--network-type roads,paths` brings in sidewalks and crossings (726 links, mostly footways), which clutters the map; the fixture is roads only.

Load it: `netstead.fixtures.market_street.load()`, or `netstead.read(market_street.parquet_dir())`.

## License and attribution

The transportation theme is derived from OpenStreetMap and published under the Open Database License (ODbL, <https://opendatacommons.org/licenses/odbl/>). Per [Overture's attribution page](https://docs.overturemaps.org/attribution/) (checked 2026-10-24), the transportation theme requires:

- "© OpenStreetMap contributors. Available under the Open Database License." (<https://www.openstreetmap.org/copyright>)
- "Data from TomTom." (<https://www.tomtom.com/>)

Overture Maps Foundation (overturemaps.org) publishes the data; credit it when you publish derived products. Derived products must stay open under the ODbL.
