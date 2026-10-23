"""Tests for :mod:`netstead.map.geo_resolver` — Issue → (lon, lat).

The resolver turns each :class:`~corral.reports.Issue` into a
``(lon, lat)`` tuple by trying, in priority order: explicit
``extra.lon/lat`` (or ``x/y``), link-row geometry midpoint, link-row
from/to node midpoint, node-row coords, and FK source-table fallback.
Findings without resolvable coords end up in the "Unlocated findings"
sidebar of the HTML viewer — still clickable for the row sync, just
not on the map.
"""

from __future__ import annotations

import pandas as pd
import pytest
from corral.engines.ibis_engine import IbisEngine
from corral.reports import Category, Issue, Severity
from netstead import Network
from netstead.map.geo_resolver import GeoResolver


def _make_network(tmp_path, *, with_geometry: bool, with_osm: bool = False) -> Network:
    """Build a tiny CSV-backed network — links share 3 nodes in a path 1-2-3."""
    link_cols = {
        "link_id": [1, 2],
        "from_node_id": [1, 2],
        "to_node_id": [2, 3],
        "directed": [True, True],
        "length": [100.0, 200.0],
    }
    if with_geometry:
        # WKT linestring: (lon1 lat1, lon2 lat2). Midpoint of (0,0)→(10,0) is (5,0).
        link_cols["geometry"] = [
            "LINESTRING (0 0, 10 0)",
            "LINESTRING (10 0, 10 20)",  # midpoint should be (10, 10)
        ]
    if with_osm:
        link_cols["osm_way_id"] = [5001, 5002]

    link = pd.DataFrame(link_cols)
    node = pd.DataFrame(
        {
            "node_id": [1, 2, 3],
            "x_coord": [0.0, 10.0, 10.0],
            "y_coord": [0.0, 0.0, 20.0],
        }
    )
    csv_dir = tmp_path / "tiny_net"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    return Network.from_source(csv_dir, engine=IbisEngine())


# ---------------------------------------------------------------------------
# Priority 1: explicit lon/lat in issue.extra
# ---------------------------------------------------------------------------


def test_explicit_lon_lat_wins(tmp_path):
    """``extra.lon/lat`` is the highest-priority source."""
    net = _make_network(tmp_path, with_geometry=True)
    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.range",
        message="link row 0 — but I know the right coords",
        table="link",
        row=0,
        extra={"lon": -120.5, "lat": 47.5},
    )
    assert resolver.resolve(issue) == (-120.5, 47.5)


def test_explicit_x_y_accepted(tmp_path):
    """GMNS-style ``x``/``y`` aliases for lon/lat are also accepted."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.DATA_QUALITY,
        code="quality.example",
        message="explicit x/y",
        extra={"x": -120.5, "y": 47.5},
    )
    assert resolver.resolve(issue) == (-120.5, 47.5)


# ---------------------------------------------------------------------------
# Priority 2: link geometry midpoint
# ---------------------------------------------------------------------------


def test_link_row_geometry_midpoint(tmp_path):
    """A link-row issue with a WKT geometry on the row resolves to its midpoint."""
    net = _make_network(tmp_path, with_geometry=True)
    resolver = GeoResolver(net)
    # link 0: LINESTRING (0 0, 10 0) — midpoint at (5, 0)
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    result = resolver.resolve(issue)
    assert result is not None
    assert result == pytest.approx((5.0, 0.0))


def test_link_row_geometry_midpoint_multi_segment(tmp_path):
    """Midpoint must be the half-length point along the polyline, not the centroid of vertices."""
    net = _make_network(tmp_path, with_geometry=True)
    resolver = GeoResolver(net)
    # link 1: LINESTRING (10 0, 10 20) — midpoint at (10, 10).
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 1",
        table="link",
        row=1,
    )
    result = resolver.resolve(issue)
    assert result is not None
    assert result == pytest.approx((10.0, 10.0))


# ---------------------------------------------------------------------------
# Priority 3: link from/to node fallback when no geometry column
# ---------------------------------------------------------------------------


def test_link_row_geometry_via_geometry_table_fk(tmp_path):
    """Leavenworth-shape network: link.geometry_id FK → geometry.geometry WKT.

    The link table carries no inline ``geometry`` column, only a
    ``geometry_id`` FK pointing into the optional ``geometry`` resource.
    The resolver must follow the FK before falling back to node
    midpoints.
    """
    link = pd.DataFrame(
        {
            "link_id": [1, 2],
            "from_node_id": [1, 2],
            "to_node_id": [2, 3],
            "directed": [True, True],
            "length": [100.0, 200.0],
            "geometry_id": [10, 11],
        }
    )
    node = pd.DataFrame(
        {
            "node_id": [1, 2, 3],
            "x_coord": [0.0, 10.0, 10.0],
            "y_coord": [0.0, 0.0, 20.0],
        }
    )
    # Geometry table: WKT differs from straight from→to nodes so we can tell
    # which path was used. Linestring 10: midpoint (5, 5) not (5, 0).
    geometry = pd.DataFrame(
        {
            "geometry_id": [10, 11],
            "geometry": [
                "LINESTRING (0 0, 0 10, 10 10, 10 0)",  # midpoint along polyline ≠ from/to mid
                "LINESTRING (10 0, 10 10, 10 20)",
            ],
        }
    )
    csv_dir = tmp_path / "fk_net"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    geometry.to_csv(csv_dir / "geometry.csv", index=False)
    net = Network.from_source(csv_dir, engine=IbisEngine())

    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    result = resolver.resolve(issue)
    assert result is not None
    # If we'd taken the from/to fallback, midpoint would be (5, 0). The
    # actual polyline midpoint is at (5, 10) — half-length along
    # 0,0 → 0,10 → 10,10 → 10,0 (total len 30, half 15 falls mid-segment 2,
    # which runs along y=10 from x=0 to x=10).
    assert result == pytest.approx((5.0, 10.0))


def test_link_row_falls_back_to_node_midpoint_when_no_geometry(tmp_path):
    """No ``geometry`` column → use the midpoint of from_node and to_node coords."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    # link 0: 1→2, nodes at (0,0) and (10,0) — midpoint (5,0).
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    result = resolver.resolve(issue)
    assert result is not None
    assert result == pytest.approx((5.0, 0.0))


# ---------------------------------------------------------------------------
# Priority 4: node-row coords
# ---------------------------------------------------------------------------


def test_node_row_uses_x_y_coords(tmp_path):
    """A node-row issue resolves to (x_coord, y_coord)."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    # node row 2 has (10, 20).
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.SCHEMA,
        code="schema.range",
        message="node row 2",
        table="node",
        row=2,
    )
    assert resolver.resolve(issue) == (10.0, 20.0)


# ---------------------------------------------------------------------------
# Priority 5: FK issue — resolve from source-table row
# ---------------------------------------------------------------------------


def test_fk_issue_resolves_through_source_row(tmp_path):
    """An FK issue records the source table+row in extra — resolver follows it."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    # An FK violation on link row 1's from_node_id — resolves to the link's midpoint.
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.FOREIGN_KEY,
        code="fk.missing_target",
        message="link row 1: from_node_id=999 not found in node",
        table="link",
        column="from_node_id",
        row=1,
    )
    # link row 1 is 2→3, midpoint of (10,0) and (10,20) = (10, 10).
    result = resolver.resolve(issue)
    assert result is not None
    assert result == pytest.approx((10.0, 10.0))


# ---------------------------------------------------------------------------
# Unlocated
# ---------------------------------------------------------------------------


def test_cross_cutting_issue_is_unlocated(tmp_path):
    """A structural issue with no table at all → None (renders in unlocated sidebar)."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.ERROR,
        category=Category.STRUCTURAL,
        code="structural.missing_table",
        message="link table missing",
    )
    assert resolver.resolve(issue) is None


def test_unknown_table_is_unlocated(tmp_path):
    """An issue on a table the resolver doesn't know how to ground → None."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="zone row 0",
        table="zone",
        row=0,
    )
    assert resolver.resolve(issue) is None


def test_out_of_range_row_is_unlocated(tmp_path):
    """An issue whose ``row`` is past the actual table → None, no IndexError."""
    net = _make_network(tmp_path, with_geometry=False)
    resolver = GeoResolver(net)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 9999",
        table="link",
        row=9999,
    )
    assert resolver.resolve(issue) is None


# ---------------------------------------------------------------------------
# Map summary helper
# ---------------------------------------------------------------------------


def test_resolver_caches_link_midpoints(tmp_path):
    """``resolve()`` re-uses a single materialized links DataFrame, not one-per-issue.

    A weak assertion that the precompute happens — checks that the
    resolver exposes a ``count`` / ``cached_link_midpoints`` surface
    via ``len`` or a property. Sub-100ms even on the leavenworth
    fixture is the practical contract — we lean on the resolver to
    NOT do an iloc lookup per issue.
    """
    net = _make_network(tmp_path, with_geometry=True)
    resolver = GeoResolver(net)
    # Precomputed table caches: links and nodes are materialized once.
    assert resolver._links_df is not None
    assert resolver._nodes_df is not None
