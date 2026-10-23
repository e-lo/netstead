"""Small example GMNS network for Leavenworth, WA (the whole town, OSM-derived).

Bundled inside the netstead wheel — no download needed. Used by tests,
the 5-minute quickstart, and every cookbook recipe so they share a
single canonical fixture.

What's in it
------------
- 121 nodes, 339 links, 339 geometries, 429 lanes
- 9 distinct GMNS tables, including one TOD restriction and several
  uses of the v0.97 ``shared_categories`` enums.
- ~3 MB total across all four storage variants below.
- Provenance: synthesized from OpenStreetMap via osmnx
  (``osmnx.graph_from_place("Leavenworth, Washington, USA",
  network_type="roads")``, which follows the city polygon) — see
  :file:`README.md` for the full attribute-derivation table.

Four storage variants
---------------------
All four hold the **same data**; they exist so format adapters can
roundtrip and assert equality.

- :func:`csv_dir`       — one CSV per table (most readable on disk)
- :func:`parquet_dir`   — one Parquet per table (smallest + fastest)
- :func:`duckdb_path`   — single-file DuckDB database
- :func:`zip_path`      — zipped CSV bundle (single distributable file)

Plus :data:`DATAPACKAGE` for the Frictionless ``datapackage.json``
manifest and :data:`ROOT` for the on-disk parent directory.

Quick use
---------

.. code-block:: python

    from netstead.fixtures import leavenworth

    print(leavenworth.summary())               # prints what's in the fixture
    net = leavenworth.load()                   # returns a Network — equivalent
                                                # to netstead.read(leavenworth.csv_dir())
    net.links.count()                          # -> 339

For other formats: ``netstead.read(leavenworth.parquet_dir())`` or
``netstead.read(leavenworth.duckdb_path())``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from netstead.network import Network

__all__ = [
    "DATAPACKAGE",
    "ROOT",
    "csv_dir",
    "duckdb_path",
    "load",
    "parquet_dir",
    "spec_path",
    "summary",
    "zip_path",
]

ROOT = Path(__file__).parent
DATAPACKAGE = ROOT / "datapackage.json"

# Static metadata about the bundled fixture. Updated whenever the
# fixture is regenerated (see scripts/build_leavenworth.py);
# tests/test_fixtures_leavenworth_metadata.py pins it to the real files.
_FIXTURE_INFO = {
    "name": "leavenworth",
    "description": "Small example GMNS network — Leavenworth, WA (the whole town)",
    "spec_version": "0.97",
    "provenance": "OpenStreetMap via osmnx (graph_from_place, drive)",
    "bbox_wgs84": "(-120.68, 47.58, -120.64, 47.61)",  # approximate
    "tables": {
        "node": 121,
        "link": 339,
        "geometry": 339,
        "lane": 429,
        "use_definition": 5,
        "use_group": 2,
        "time_set_definitions": 1,
        "link_tod": 1,
        "signal_controller": 2,
    },
}


def csv_dir() -> Path:
    """Directory containing the canonical CSV form of the fixture."""
    return ROOT / "csv"


def parquet_dir() -> Path:
    """Directory containing the per-table Parquet form of the fixture."""
    return ROOT / "parquet"


def duckdb_path() -> Path:
    """Path to the single-file DuckDB form of the fixture."""
    return ROOT / "leavenworth.duckdb"


def zip_path() -> Path:
    """Path to the zipped-CSV form of the fixture."""
    return ROOT / "leavenworth.csv.zip"


def spec_path() -> Path:
    """Path to the fixture's ``datapackage.json`` (the resolved Frictionless spec).

    Convenience alias for :data:`DATAPACKAGE` — exists so the
    documented form ``leavenworth.spec_path()`` works without
    callers having to know whether to use the function or the
    constant. Both return the same path.

    Examples:
        >>> from netstead.fixtures import leavenworth
        >>> leavenworth.spec_path() == leavenworth.DATAPACKAGE
        True
    """
    return DATAPACKAGE


def load(format: str = "csv") -> Network:
    """Load the fixture as a :class:`netstead.Network`.

    Convenience wrapper around :func:`netstead.read` for the bundled
    fixture so quickstart / notebook users don't need to know which
    storage variant to point at.

    Args:
        format: One of ``"csv"`` (default), ``"parquet"``, ``"duckdb"``,
            or ``"zip"``. All four hold identical data; pick whichever
            you want the read path to exercise.

    Returns:
        A :class:`netstead.Network` loaded through the default ibis +
        DuckDB engine. Tables are lazy expressions; nothing
        materializes until you call ``.count()`` / ``.to_pandas()`` /
        validate.

    Examples:
        >>> from netstead.fixtures import leavenworth
        >>> net = leavenworth.load()                         # CSV by default
        >>> net.links.count()
        339
        >>> net_pq = leavenworth.load("parquet")             # same data, different store
    """
    from netstead import Network  # local import — avoids circular when this module loads first

    sources = {
        "csv": csv_dir(),
        "parquet": parquet_dir(),
        "duckdb": duckdb_path(),
        "zip": zip_path(),
    }
    if format not in sources:
        raise ValueError(f"unknown format {format!r}; expected one of {sorted(sources)}")
    # Network.from_source() looks up the GMNS spec by version
    # (defaulting to netstead.DEFAULT_SPEC) — no need to pass the
    # datapackage.json path explicitly. The fixture is GMNS 0.97.
    return Network.from_source(sources[format], spec_version=_FIXTURE_INFO["spec_version"])


def summary() -> str:
    """Return a human-readable multi-line description of the fixture.

    Useful in interactive sessions where ``print(leavenworth)`` only
    shows the module path. Call ``leavenworth.summary()`` instead to
    see what's actually in the fixture without opening the README.

    Examples:
        >>> from netstead.fixtures import leavenworth
        >>> print(leavenworth.summary())              # doctest: +ELLIPSIS
        Leavenworth, WA — bundled GMNS example fixture
        ...
    """
    lines = [
        "Leavenworth, WA — bundled GMNS example fixture",
        f"  spec_version : {_FIXTURE_INFO['spec_version']}",
        f"  provenance   : {_FIXTURE_INFO['provenance']}",
        f"  bbox (WGS84) : {_FIXTURE_INFO['bbox_wgs84']}",
        "  tables       :",
    ]
    for name, count in _FIXTURE_INFO["tables"].items():
        lines.append(f"    {name:<22} {count:>4} rows")
    lines.extend(
        [
            "",
            "Storage variants (all hold the same data):",
            f"  csv_dir()     -> {csv_dir()}",
            f"  parquet_dir() -> {parquet_dir()}",
            f"  duckdb_path() -> {duckdb_path()}",
            f"  zip_path()    -> {zip_path()}",
            "",
            "Quick load:  net = leavenworth.load()    # returns a netstead.Network",
        ]
    )
    return "\n".join(lines)
