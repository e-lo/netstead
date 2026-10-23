"""Tests for :mod:`netstead.semantics` (task 3.8 / issue #76).

Covers:

* :mod:`netstead.semantics.connectivity` — :func:`is_connected`,
  :func:`connected_components`, :func:`largest_component`,
  :func:`unreachable_from`. Mix of Leavenworth (integration) +
  hand-built micro networks (disconnect edge cases).
* :mod:`netstead.semantics.geometry` — :func:`assemble_link_geometry`
  exercises all three resolution paths (inline / geometry_id /
  node_endpoints) via a hand-built fixture.
* :mod:`netstead.semantics.tod` — :func:`resolve_link_attrs_at` flows
  ``link_tod`` overrides onto the base link table;
  :func:`tod_coverage` reports present periods. Leavenworth has one
  ``link_tod`` row so it serves as the smoke test.
"""

from __future__ import annotations

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from corral.spec import DataPackage, Resource
from netstead import Network
from netstead.fixtures import leavenworth

# connectivity now builds a netstead.graph.GMNSGraph (scipy); geometry uses
# shapely. Skip the module if the graph backend isn't installed.
pytest.importorskip("scipy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine() -> IbisEngine:
    """One engine for the semantics tests — backend-agnostic by design.

    Connectivity / geometry / TOD all materialize through pyarrow, so
    the engine choice doesn't change behavior. IbisEngine is the
    fastest setup for tiny networks.
    """
    return IbisEngine()


def _network_from_tables(tables_dict: dict[str, Table], spec_version: str = "0.97") -> Network:
    """Build a :class:`Network` from in-memory tables for tests.

    Uses :meth:`Package.from_tables` under the hood but stamps the
    ``spec_version`` so accessor error messages remain accurate.
    """
    engine = next(iter(tables_dict.values())).engine
    spec = DataPackage(name="synthesized", resources=[Resource(name=n) for n in tables_dict])
    return Network(
        spec=spec,
        tables=dict(tables_dict),
        engine=engine,
        spec_version=spec_version,
    )


def _link_table(engine, rows: list[dict]) -> Table:
    """Build a :class:`Table` named ``link`` from row dicts."""
    return Table(name="link", expr=engine.from_records(rows), engine=engine)


def _node_table(engine, rows: list[dict]) -> Table:
    """Build a :class:`Table` named ``node`` from row dicts."""
    return Table(name="node", expr=engine.from_records(rows), engine=engine)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_leavenworth_is_connected():
    """Leavenworth fixture should be one weakly-connected component."""
    from netstead.semantics import connected_components, is_connected

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    assert is_connected(net) is True
    comps = connected_components(net)
    assert len(comps) == 1


def test_disconnected_network_has_two_components():
    """Two clusters of nodes with no link between them -> 2 components."""
    from netstead.semantics import connected_components, is_connected, largest_component

    engine = _engine()
    nodes = _node_table(
        engine,
        [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2, "x_coord": 1.0, "y_coord": 0.0},
            {"node_id": 3, "x_coord": 10.0, "y_coord": 10.0},
            {"node_id": 4, "x_coord": 11.0, "y_coord": 10.0},
            {"node_id": 5, "x_coord": 12.0, "y_coord": 10.0},
        ],
    )
    links = _link_table(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 2, "length": 1.0, "directed": False},
            {"link_id": 2, "from_node_id": 3, "to_node_id": 4, "length": 1.0, "directed": False},
            {"link_id": 3, "from_node_id": 4, "to_node_id": 5, "length": 1.0, "directed": False},
        ],
    )
    net = _network_from_tables({"link": links, "node": nodes})

    assert is_connected(net) is False
    comps = connected_components(net)
    assert len(comps) == 2
    # Largest-first ordering invariant.
    assert len(comps[0]) >= len(comps[1])
    assert largest_component(net) == {3, 4, 5}


def test_connectivity_caches_graph_on_network():
    """First call builds + caches; second call reuses the cached graph."""
    from netstead.semantics.connectivity import _GRAPH_CACHE_KEY, connected_components

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    assert _GRAPH_CACHE_KEY not in net.metadata
    connected_components(net)
    assert _GRAPH_CACHE_KEY in net.metadata
    cached_first = net.metadata[_GRAPH_CACHE_KEY]
    connected_components(net)
    assert net.metadata[_GRAPH_CACHE_KEY] is cached_first


def test_unreachable_from_raises_on_unknown_seed():
    """Unknown source_node_id -> SemanticsError mentioning the id + count."""
    from netstead.semantics import SemanticsError, unreachable_from

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    with pytest.raises(SemanticsError, match="99999"):
        unreachable_from(net, 99999)


def test_unreachable_from_returns_set():
    """Smoke: leavenworth is bidirectional so the unreachable set may be empty,
    but the function must always return a set type."""
    from netstead.semantics import largest_component, unreachable_from

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    seed = next(iter(largest_component(net)))
    result = unreachable_from(net, seed)
    assert isinstance(result, set)


# ---------------------------------------------------------------------------
# Geometry assembly
# ---------------------------------------------------------------------------


def test_assemble_geometry_inline_wins():
    """A link with inline geometry resolves as ``source = "inline"``."""
    from netstead.semantics import assemble_link_geometry

    engine = _engine()
    nodes = _node_table(
        engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}, {"node_id": 2, "x_coord": 1.0, "y_coord": 1.0}]
    )
    links = _link_table(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 2,
                "geometry": "LINESTRING (0 0, 0.5 0.5, 1 1)",
                "geometry_id": None,
            }
        ],
    )
    net = _network_from_tables({"link": links, "node": nodes})
    tbl = assemble_link_geometry(net)
    assert tbl.column("source").to_pylist() == ["inline"]
    from netstead._wkt import linestring_points

    assert linestring_points(tbl.column("geometry").to_pylist()[0]) == [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]


def test_assemble_geometry_id_lookup():
    """A link with geometry_id but no inline geometry resolves via geometry table."""
    from netstead.semantics import assemble_link_geometry

    engine = _engine()
    nodes = _node_table(
        engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}, {"node_id": 2, "x_coord": 2.0, "y_coord": 2.0}]
    )
    links = _link_table(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 2,
                "geometry": None,
                "geometry_id": 42,
            }
        ],
    )
    geometry = Table(
        name="geometry",
        expr=engine.from_records([{"geometry_id": 42, "geometry": "LINESTRING (0 0, 1 1, 2 2)"}]),
        engine=engine,
    )
    net = _network_from_tables({"link": links, "node": nodes, "geometry": geometry})
    tbl = assemble_link_geometry(net)
    assert tbl.column("source").to_pylist() == ["geometry_table"]
    from netstead._wkt import linestring_points

    assert linestring_points(tbl.column("geometry").to_pylist()[0]) == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]


def test_assemble_geometry_falls_back_to_node_endpoints():
    """No inline + no geometry_id resolution => synthesised LINESTRING."""
    from netstead.semantics import assemble_link_geometry

    engine = _engine()
    nodes = _node_table(
        engine, [{"node_id": 1, "x_coord": 0.5, "y_coord": 0.25}, {"node_id": 2, "x_coord": 3.5, "y_coord": 4.121}]
    )
    links = _link_table(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 2,
                "geometry": None,
                "geometry_id": None,
            }
        ],
    )
    net = _network_from_tables({"link": links, "node": nodes})
    tbl = assemble_link_geometry(net)
    assert tbl.column("source").to_pylist() == ["node_endpoints"]
    # Synthesised straight segment, emitted as WKB from the two node endpoints.
    from netstead._wkt import linestring_points

    assert linestring_points(tbl.column("geometry").to_pylist()[0]) == [(0.5, 0.25), (3.5, 4.121)]


def test_assemble_geometry_leavenworth_uses_geometry_table():
    """Leavenworth links carry geometry_id => all rows resolve via geometry_table."""
    from netstead.semantics import assemble_link_geometry

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    tbl = assemble_link_geometry(net)
    sources = set(tbl.column("source").to_pylist())
    # Every Leavenworth link carries a geometry_id with a matching row.
    assert sources == {"geometry_table"}


# ---------------------------------------------------------------------------
# TOD resolution
# ---------------------------------------------------------------------------


def test_resolve_tod_none_returns_base():
    """``time_set_id=None`` returns the link table unchanged."""
    from netstead.semantics import resolve_link_attrs_at

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    base = resolve_link_attrs_at(net, None)
    # Same column set as the source link table.
    assert set(base.column_names) == set(net.links.columns())


def test_resolve_tod_applies_override():
    """A TOD row for the target time_set_id replaces the base value on the affected column."""
    from netstead.semantics import resolve_link_attrs_at

    engine = _engine()
    nodes = _node_table(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link_table(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 1,
                "capacity": 1800.0,
                "free_speed": 60.0,
            }
        ],
    )
    link_tod = Table(
        name="link_tod",
        expr=engine.from_records(
            [
                {
                    "link_tod_id": 100,
                    "link_id": 1,
                    "timeday_id": "am_peak",
                    "capacity": 1500.0,
                    # free_speed unset -> base value passes through.
                }
            ]
        ),
        engine=engine,
    )
    net = _network_from_tables({"link": links, "node": nodes, "link_tod": link_tod})

    resolved = resolve_link_attrs_at(net, "am_peak")
    row = resolved.to_pylist()[0]
    assert row["capacity"] == 1500.0  # overridden
    assert row["free_speed"] == 60.0  # passed through

    # Off-peak (no matching TOD row) => base table.
    off_peak = resolve_link_attrs_at(net, "pm_peak")
    assert off_peak.to_pylist()[0]["capacity"] == 1800.0


def test_tod_coverage_reports_link_tod_periods():
    """:func:`tod_coverage` lists timeday_ids present per TOD table."""
    from netstead.semantics import tod_coverage

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    coverage = tod_coverage(net)
    # Leavenworth has one link_tod row with timeday_id="weekday_am_peak".
    assert "link_tod" in coverage
    assert coverage["link_tod"] == ["weekday_am_peak"]


def test_tod_coverage_omits_absent_tables():
    """No TOD tables loaded => empty coverage dict (not KeyError)."""
    from netstead.semantics import tod_coverage

    engine = _engine()
    nodes = _node_table(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link_table(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "length": 0.0}])
    net = _network_from_tables({"link": links, "node": nodes})
    assert tod_coverage(net) == {}
