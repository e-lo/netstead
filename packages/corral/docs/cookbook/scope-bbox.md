---
title: Bounding-box and polygon scope
audience: users
kind: howto
summary: Spatial scope by bbox, polygon, or geometry-buffer — works on any Frictionless package with a WKT or WKB geometry column.
---

# Bounding-box and polygon scope

## When to use this

You have a spatial filter — a bounding box from a map UI, a polygon from a planning area, or a buffered route — and want only the rows whose geometry falls inside it. Works on any table with a geometry column; not GMNS-specific.

## Quick example

Load the bundled Leavenworth fixture through corral, build a bbox-scoped `Table` over the `geometry` table (the one GMNS table that carries inline WKT), and count the rows that intersect the box:

```python
from corral import Package
from corral.dataset.view import from_bbox
from netstead.fixtures import leavenworth

pkg = Package.from_source(leavenworth.csv_dir(), spec=leavenworth.spec_path())
geometry = pkg["geometry"]
scoped = from_bbox(geometry, minx=-120.67, miny=47.58, maxx=-120.65, maxy=47.60)
print(f"{scoped.count()} of {geometry.count()} geometries in bbox")
```

Expected:

```text
230 of 339 geometries in bbox
```

The result is a lazy `Table` — no rows materialize until you call `.to_pandas()`, `.count()` or similar. Note the table choice: Leavenworth's `link` table has no geometry column (links point at `geometry` rows via `geometry_id`), and a table without the geometry column is passed through **unfiltered** — `from_bbox(pkg["link"], ...)` silently returns all 339 links.

## Step-by-step

### 1. Bounding box (any table)

`from_bbox` is generic — it works on any `Table` with a WKT or WKB geometry column (default name `geometry`; pass `geometry_column=` for another). The predicate compiles to `ST_Intersects(geom, ST_MakeEnvelope(...))` and runs inside DuckDB's spatial extension; no shapely required:

```python
from corral.dataset.view import from_bbox

scoped = from_bbox(pkg["geometry"], minx=-120.67, miny=47.58, maxx=-120.65, maxy=47.60)
rows = scoped.head(5).collect()
```

### 2. Polygon (shapely or WKT)

For non-rectangular regions, pass a shapely `Polygon`. Shapely is optional for corral — `pip install shapely` (it also rides along with `netstead[clean]`):

```python
from corral.dataset.view import from_polygon
from shapely.geometry import Polygon

poly = Polygon([
    (-120.67, 47.58),
    (-120.65, 47.58),
    (-120.65, 47.60),
    (-120.67, 47.60),
    (-120.67, 47.58),
])
scoped = from_polygon(pkg["geometry"], poly)
```

If shapely isn't installed, pass a WKT string instead — it goes straight to DuckDB's `ST_GeomFromText`:

```python
ring = "POLYGON((-120.67 47.58, -120.65 47.58, -120.65 47.60, -120.67 47.60, -120.67 47.58))"
scoped = from_polygon(pkg["geometry"], ring)
print(scoped.count())
```

### 3. Buffered geometry

Buffer a geometry and keep anything that intersects the buffer. `distance_m` is in the **CRS units of the geometry column** — degrees for EPSG:4326 data like Leavenworth — and nothing is reprojected, so for "everything within 100 m of this corridor" on a lat/lon source you either convert metres to degrees yourself or pre-buffer in a projected CRS and pass the result to `from_polygon`:

```python
from corral.dataset.view import from_geometry_buffer
from shapely.geometry import LineString

corridor = LineString([(-120.67, 47.58), (-120.65, 47.60)])
scoped = from_geometry_buffer(pkg["geometry"], corridor, distance_m=0.001)  # ~100 m at this latitude
```

### 4. Predicate pushdown

The spatial predicate is a single DuckDB `WHERE` clause. When the underlying source is a Hive-partitioned Parquet dataset, that clause stays inside the `read_parquet` plan so DuckDB prunes partitions whose statistics fall entirely outside the box before reading them:

<!-- doctest: skip -->
```python
pkg = Package.from_source("s3://my-bucket/regional/", spec=spec)
# Only the parquet partitions overlapping the bbox are read off S3.
scoped = from_bbox(pkg["geometry"], minx=-120.67, miny=47.58, maxx=-120.65, maxy=47.60)
```

To see the compiled plan, take the underlying ibis expression and ask DuckDB directly: `ibis.to_sql(scoped.expr)` prints the SQL, and `scoped.expr.compile()` gives the same string for `EXPLAIN`.

### 5. Compose with other filters

Spatial scopes return ordinary `Table`s, so they compose with `Table.filter`, which takes a callable over the ibis expression. The bbox + attribute predicate fuse into a single SQL pass — no intermediate materialization:

```python
inside = from_bbox(pkg["geometry"], -120.67, 47.58, -120.65, 47.60)
low_ids = inside.filter(lambda t: t.filter(t.geometry_id < 100))
print(low_ids.count())
```

## Common variations

???+ note "Default — bbox filter on a single table"
    Returns a lazy `Table` over the matched rows.
<!-- doctest: skip -->

    ```python
    scoped = from_bbox(pkg["geometry"], minx, miny, maxx, maxy)
    ```

??? note "Scope every geometry-bearing table in the package"
    `Package.scope(bbox=...)` applies the same filter to every loaded table that has the geometry column; tables without it pass through untouched. On Leavenworth that trims `geometry` to 230 rows and leaves `link` / `node` (no geometry column) at full size.

    ```python
    sub = pkg.scope(bbox=(-120.67, 47.58, -120.65, 47.60))
    print(sub["geometry"].count(), sub["link"].count())
    ```

??? note "Filter the whole network with FK pushdown"
    For GMNS networks, use the network-aware scope ops in `netstead.scope` instead — `from_nodes`, `from_node`, `from_link`, `from_point`, `connected_component`, `from_zone`. They walk the FK graph and produce a fully consistent sub-network. There is no polygon entry point there today; feed a polygon through `from_polygon` on the `geometry` table and seed `from_nodes` from the result.

??? note "WGS84 bbox against a projected source"
    `from_bbox` compares coordinates as given; it does not reproject. Reproject the bbox to the source CRS first (`pyproj` ships with `netstead[clean]`).
<!-- doctest: skip -->

    ```python
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True)  # source CRS here
    minx, miny = t.transform(-120.67, 47.58)
    maxx, maxy = t.transform(-120.65, 47.60)
    scoped = from_bbox(pkg["geometry"], minx, miny, maxx, maxy)
    ```

??? note "Buffered point (everything near a location)"
    `from_geometry_buffer` accepts any shapely geometry — `Point`, `LineString`, `Polygon` — or a WKT string. The distance is in the column's CRS units.

    ```python
    from shapely.geometry import Point
    scoped = from_geometry_buffer(pkg["geometry"], Point(-120.66, 47.59), distance_m=0.005)
    ```

## Pitfalls

* **No geometry column means no filter.** `from_bbox` / `from_polygon` / `from_geometry_buffer` are a silent no-op on a table that lacks the geometry column — check `table.columns()` if a scope returns everything.
* **CRS mismatch is silent.** Degrees-vs-metres mismatches produce empty results, not errors. corral does not track a package CRS; know the CRS of your geometry column.
* **`distance_m` is not metres unless your CRS is.** The buffer runs in the geometry column's units with no reprojection — on EPSG:4326 that's degrees.
* **Shapely needed for polygon / geometry objects** (not for bbox or WKT strings). `pip install shapely`, or `pip install 'netstead[clean]'`, brings it in.
* **Single-table scope is *not* FK-aware.** If you bbox-filter `geometry` and want only the dependent `link` rows too, use the network-aware scope helpers in `netstead.scope` (`from_nodes`, `from_link`, `connected_component`) instead — those walk the FK graph.

## See also

* [Build a scope from seed nodes](https://e-lo.github.io/netstead/netstead/cookbook/scope-from-nodes/) — network-graph scope (vs spatial).
* [API reference](../reference/api.md) — `corral.dataset.view.from_bbox`, `from_polygon`, `from_geometry_buffer`, `Package.scope`.
