"""Small example GMNS network for Market Street, San Francisco (Overture Maps-derived).

Bundled inside the netstead wheel, no download needed. A few hundred metres
around Lotta's Fountain, where Market, Kearny, Geary and 3rd Street meet at odd
angles, with one-ways and short links. Built with netstead's own Overture
builder, so every link carries Overture GERS ids (``overture_segment_id``,
``overture_connector_ids``) and every node an ``overture_connector_id``; it is
the fixture for the Find tab's "Overture GERS" id system.

What's in it
------------
- 129 nodes, 186 links (roads only; a link is one direction of one sub-segment)
- Tables: ``node``, ``link`` and a ``config`` row (CRS and units); no lane or
  geometry tables, since the builder puts link shapes in ``link.geometry``.
- Provenance: Overture Maps release 2026-09-23.1, transportation theme.
  Data (c) OpenStreetMap contributors, available under the ODbL; data from
  TomTom. See :file:`README.md` for the full attribution.

Storage variants (same data): :func:`csv_dir`, :func:`parquet_dir`,
:func:`zip_path`. There is no DuckDB file; DuckDB's block overhead alone would
be about 1 MB for a network this small.

.. code-block:: python

    from netstead.fixtures import market_street

    print(market_street.summary())
    net = market_street.load()
    net.links.count()                          # -> 186
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from netstead.network import Network

__all__ = ["DATAPACKAGE", "ROOT", "csv_dir", "load", "parquet_dir", "spec_path", "summary", "zip_path"]

ROOT = Path(__file__).parent
DATAPACKAGE = ROOT / "datapackage.json"

# Updated whenever the fixture is regenerated (scripts/build_market_street.py);
# tests/test_fixtures_market_street.py pins it to the real files.
_FIXTURE_INFO = {
    "name": "market_street",
    "description": (
        "Market Street, San Francisco: a tangle of multi-leg intersections, one-ways and transit lanes "
        "around Lotta's Fountain (Overture)"
    ),
    "spec_version": "0.97",
    "provenance": "Overture Maps release 2026-09-23.1, transportation theme (roads)",
    "overture_release": "2026-09-23.1",
    "bbox_wgs84": "(-122.4056, 37.7866, -122.4016, 37.7894)",
    "attribution": "© OpenStreetMap contributors. Available under the Open Database License. Data from TomTom.",
    "tables": {"node": 129, "link": 186, "config": 1},
}


def csv_dir() -> Path:
    """Directory containing the canonical CSV form of the fixture."""
    return ROOT / "csv"


def parquet_dir() -> Path:
    """Directory containing the per-table Parquet form of the fixture."""
    return ROOT / "parquet"


def zip_path() -> Path:
    """Path to the zipped-CSV form of the fixture."""
    return ROOT / "market_street.csv.zip"


def spec_path() -> Path:
    """Path to the fixture's ``datapackage.json`` (alias for :data:`DATAPACKAGE`)."""
    return DATAPACKAGE


def load(format: str = "csv") -> Network:
    """Load the fixture as a :class:`netstead.Network`.

    Args:
        format: ``"csv"`` (default), ``"parquet"`` or ``"zip"``; all hold identical data.

    Raises:
        ValueError: For any other format.

    Examples:
        >>> from netstead.fixtures import market_street
        >>> market_street.load("parquet").links.count()
        186
    """
    from netstead import Network  # local import: avoids a cycle when this module loads first

    sources = {"csv": csv_dir(), "parquet": parquet_dir(), "zip": zip_path()}
    if format not in sources:
        raise ValueError(f"unknown format {format!r}; expected one of {sorted(sources)}")
    return Network.from_source(sources[format], spec_version=_FIXTURE_INFO["spec_version"])


def summary() -> str:
    """Return a human-readable multi-line description of the fixture."""
    lines = [
        "Market Street, San Francisco — bundled GMNS example fixture (Overture)",
        f"  spec_version : {_FIXTURE_INFO['spec_version']}",
        f"  provenance   : {_FIXTURE_INFO['provenance']}",
        f"  bbox (WGS84) : {_FIXTURE_INFO['bbox_wgs84']}",
        f"  attribution  : {_FIXTURE_INFO['attribution']}",
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
            f"  zip_path()    -> {zip_path()}",
            "",
            "Quick load:  net = market_street.load()    # returns a netstead.Network",
        ]
    )
    return "\n".join(lines)
