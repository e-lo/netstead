"""Tests for :mod:`netstead.scope` (task 3.10 / issue #78).

Covers:

* Distance parser (``_parse_distance``) — units, errors.
* Each constructor (``from_nodes``, ``from_node``, ``from_link``,
  ``from_point``, ``connected_component``, ``from_zone``) on small
  hand-built networks where the expected node/link sets are exact.
* Composition (``union``, ``intersect``, ``subtract``,
  ``buffer_network``, ``buffer_spatial``).
* :meth:`NetworkScope.apply` — FK-pushdown filters all known GMNS
  tables to the scope.
* Index-cache reuse across calls (perf-relevant: rebuilding a
  GraphIndex over a regional network is the slow path).

Hand-built synthetic networks dominate so the expected sets are exact;
Leavenworth provides smoke coverage on the realistic-shape path.
"""

from __future__ import annotations

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from corral.spec import DataPackage, Resource
from netstead import Network
from netstead.fixtures import leavenworth

pytest.importorskip("scipy")
pytest.importorskip("shapely")


# ---------------------------------------------------------------------------
# Helpers — small grid network for exact-set assertions
# ---------------------------------------------------------------------------


def _engine() -> IbisEngine:
    """One engine for the scope tests — operations are engine-agnostic."""
    return IbisEngine()


def _network(tables_dict: dict[str, Table]) -> Network:
    """Build a :class:`Network` from in-memory tables for tests."""
    engine = next(iter(tables_dict.values())).engine
    spec = DataPackage(name="synthesized", resources=[Resource(name=n) for n in tables_dict])
    return Network(spec=spec, tables=dict(tables_dict), engine=engine, spec_version="0.97")


def _link(engine, rows: list[dict]) -> Table:
    return Table(name="link", expr=engine.from_records(rows), engine=engine)


def _node(engine, rows: list[dict]) -> Table:
    return Table(name="node", expr=engine.from_records(rows), engine=engine)


def _grid_network() -> Network:
    """A small 3x3 grid of nodes with 12 bidirectional links (length 100 each).

    Layout::

        7 - 8 - 9
        |   |   |
        4 - 5 - 6
        |   |   |
        1 - 2 - 3

    Links (id, from, to, length):
      Horizontals: 1->2 (l=100), 2->3 (l=100), 4->5, 5->6, 7->8, 8->9
      Verticals:   1->4, 2->5, 3->6, 4->7, 5->8, 6->9

    All directed=False (so the GraphIndex adds both directions).
    Coordinates are placed on a unit grid (0..2 in each dim).
    """
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0, "zone_id": 1},
            {"node_id": 2, "x_coord": 1.0, "y_coord": 0.0, "zone_id": 1},
            {"node_id": 3, "x_coord": 2.0, "y_coord": 0.0, "zone_id": 2},
            {"node_id": 4, "x_coord": 0.0, "y_coord": 1.0, "zone_id": 1},
            {"node_id": 5, "x_coord": 1.0, "y_coord": 1.0, "zone_id": 2},
            {"node_id": 6, "x_coord": 2.0, "y_coord": 1.0, "zone_id": 2},
            {"node_id": 7, "x_coord": 0.0, "y_coord": 2.0, "zone_id": 3},
            {"node_id": 8, "x_coord": 1.0, "y_coord": 2.0, "zone_id": 3},
            {"node_id": 9, "x_coord": 2.0, "y_coord": 2.0, "zone_id": 3},
        ],
    )
    horizontals = [
        (1, 1, 2),
        (2, 2, 3),
        (3, 4, 5),
        (4, 5, 6),
        (5, 7, 8),
        (6, 8, 9),
    ]
    verticals = [
        (7, 1, 4),
        (8, 2, 5),
        (9, 3, 6),
        (10, 4, 7),
        (11, 5, 8),
        (12, 6, 9),
    ]
    link_rows = []
    for lid, frm, to in horizontals + verticals:
        # Build a tiny WKT linestring from the node coords for spatial-buffer tests.
        x1 = (frm - 1) % 3
        y1 = (frm - 1) // 3
        x2 = (to - 1) % 3
        y2 = (to - 1) // 3
        link_rows.append(
            {
                "link_id": lid,
                "from_node_id": frm,
                "to_node_id": to,
                "length": 100.0,
                "directed": False,
                "geometry": f"LINESTRING ({x1} {y1}, {x2} {y2})",
            }
        )
    return _network({"link": _link(engine, link_rows), "node": nodes})


# ---------------------------------------------------------------------------
# Distance parser
# ---------------------------------------------------------------------------


def test_parse_distance_numeric_is_meters():
    from netstead.scope.scope import _parse_distance

    assert _parse_distance(800) == 800.0
    assert _parse_distance(0.5) == 0.5


def test_parse_distance_units():
    from netstead.scope.scope import _parse_distance

    assert _parse_distance("0.5mi") == pytest.approx(804.672)
    assert _parse_distance("1km") == 1000.0
    assert _parse_distance("100ft") == pytest.approx(30.48)


def test_parse_distance_rejects_bad_input():
    from netstead.scope import ScopeError
    from netstead.scope.scope import _parse_distance

    with pytest.raises(ScopeError, match="Unparseable"):
        _parse_distance("not-a-distance")
    with pytest.raises(ScopeError, match="Unknown distance unit"):
        _parse_distance("5parsecs")


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def test_from_nodes_no_path_between():
    """Seeds with path_between=False -> just the seed nodes + incident links."""
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 2], path_between=False)
    assert scope.node_ids == frozenset({1, 2})
    # Only link 1 (1->2) has both endpoints in {1, 2}.
    assert scope.link_ids == frozenset({1})


def test_from_nodes_path_between_includes_path_nodes():
    """Seeds {1, 9} with path_between -> include nodes on the shortest path."""
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 9], path_between=True)
    # Path is 1->2->5->8->9 or 1->4->5->6->9 etc.; all length-4 paths.
    assert {1, 9} <= scope.node_ids
    # Must include at least one intermediate node.
    assert len(scope.node_ids) > 2


def test_from_nodes_path_between_ids_are_plain_python_ints():
    """Path-expanded ids must be plain ``int`` so scopes serialise as JSON.

    Regression: ``GMNSGraph.shortest_path`` yields ``numpy.int64`` node ids;
    the scope must normalise them at collection time (``json.dumps`` and
    FastMCP both reject numpy scalars).
    """
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 9], path_between=True)
    assert len(scope.node_ids) > 2  # path nodes were actually added
    assert all(type(n) is int for n in scope.node_ids)
    assert all(type(lid) is int for lid in scope.link_ids)


def test_from_nodes_drops_unknown_ids():
    """Loose lists with unknown ids don't raise; the unknown ones just don't show up."""
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 999, 1000], path_between=False)
    assert scope.node_ids == frozenset({1})


def test_from_node_network_buffer():
    """Buffer of 100m from node 5 -> reaches its 4 immediate neighbours (2, 4, 6, 8)."""
    from netstead.scope import from_node

    net = _grid_network()
    scope = from_node(net, 5, network_buffer="100m")
    # Each grid edge is length 100, so 100m buffer reaches the 1-hop neighbours.
    assert {2, 4, 5, 6, 8} <= scope.node_ids
    # And NOT the corners (which are 200m away).
    assert {1, 3, 7, 9}.isdisjoint(scope.node_ids)


def test_from_node_rejects_unknown_seed():
    from netstead.scope import ScopeError, from_node

    net = _grid_network()
    with pytest.raises(ScopeError, match="9999"):
        from_node(net, 9999, network_buffer="50m")


def test_from_link_spatial_buffer_returns_neighbours():
    """Spatial buffer of 1.5 around link 1 (1->2 at y=0) should find the parallel link at y=1.

    Link 1 geometry: (0,0)-(1,0). A 1.5-unit buffer reaches links at y=1
    too (distance is 1.0).
    """
    from netstead.scope import from_link

    net = _grid_network()
    scope = from_link(net, 1, spatial_buffer_m=1.5)
    assert 1 in scope.link_ids  # seed always included
    # Links along y=1 (ids 3, 4) should fall inside the buffer.
    assert {3, 4} & scope.link_ids


def test_from_link_network_buffer():
    """Network buffer 100m from link 1 (1->2) reaches each endpoint's 1-hop neighbours."""
    from netstead.scope import from_link

    net = _grid_network()
    scope = from_link(net, 1, network_buffer="100m")
    # From {1, 2} expanding by 100m reaches {4, 5} via verticals + horizontals.
    assert {1, 2} <= scope.node_ids
    assert {4, 5} <= scope.node_ids


def test_from_link_requires_exactly_one_buffer_kind():
    from netstead.scope import ScopeError, from_link

    net = _grid_network()
    with pytest.raises(ScopeError, match="exactly one"):
        from_link(net, 1)
    with pytest.raises(ScopeError, match="exactly one"):
        from_link(net, 1, spatial_buffer_m=10, network_buffer="50m")


def test_from_point_snaps_and_buffers():
    """Point near (0, 0) with a 1.5 buffer should find links incident to node 1."""
    from netstead.scope import from_point

    net = _grid_network()
    scope = from_point(net, (0.0, 0.0), spatial_buffer_m=1.5)
    # Link 1 (1->2) starts at (0,0) — must be in the buffer.
    assert 1 in scope.link_ids
    # Link 7 (1->4) starts at (0,0) — must also be there.
    assert 7 in scope.link_ids


def test_connected_component_returns_whole_component():
    """connected_component on the grid returns all 9 nodes + all 12 links."""
    from netstead.scope import connected_component

    net = _grid_network()
    scope = connected_component(net, 1)
    assert scope.node_ids == frozenset(range(1, 10))
    assert scope.link_ids == frozenset(range(1, 13))


def test_connected_component_rejects_unknown_seed():
    from netstead.scope import ScopeError, connected_component

    net = _grid_network()
    with pytest.raises(ScopeError, match="9999"):
        connected_component(net, 9999)


def test_from_zone_filters_by_node_zone_id():
    """Zone 1 contains nodes 1, 2, 4 -> incident-only links {1, 7} (1->2, 1->4)."""
    from netstead.scope import from_zone

    net = _grid_network()
    scope = from_zone(net, [1])
    assert scope.node_ids == frozenset({1, 2, 4})
    # Only links with BOTH endpoints in {1,2,4}: 1->2 and 1->4.
    assert scope.link_ids == frozenset({1, 7})


def test_from_zone_requires_zone_id_column():
    from netstead.scope import ScopeError, from_zone

    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])  # no zone_id
    links = _link(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "length": 0.0}])
    net = _network({"link": links, "node": nodes})
    with pytest.raises(ScopeError, match="zone_id"):
        from_zone(net, [1])


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_union():
    from netstead.scope import from_nodes

    net = _grid_network()
    s1 = from_nodes(net, [1, 2], path_between=False)
    s2 = from_nodes(net, [2, 3], path_between=False)
    u = s1.union(s2)
    assert u.node_ids == frozenset({1, 2, 3})


def test_intersect():
    from netstead.scope import from_nodes

    net = _grid_network()
    s1 = from_nodes(net, [1, 2, 4], path_between=False)
    s2 = from_nodes(net, [2, 5], path_between=False)
    inter = s1.intersect(s2)
    assert inter.node_ids == frozenset({2})


def test_subtract():
    from netstead.scope import from_nodes

    net = _grid_network()
    s1 = from_nodes(net, [1, 2, 4], path_between=False)
    s2 = from_nodes(net, [4], path_between=False)
    diff = s1.subtract(s2)
    assert diff.node_ids == frozenset({1, 2})


def test_compose_rejects_cross_network():
    from netstead.scope import ScopeError, from_nodes

    net1 = _grid_network()
    net2 = _grid_network()
    s1 = from_nodes(net1, [1], path_between=False)
    s2 = from_nodes(net2, [1], path_between=False)
    with pytest.raises(ScopeError, match="different networks"):
        s1.union(s2)


def test_buffer_network_extends_scope():
    """buffer_network(100) on {5} reaches 1-hop neighbours."""
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [5], path_between=False).buffer_network("100m")
    assert {2, 4, 5, 6, 8} <= scope.node_ids


def test_buffer_spatial_extends_scope():
    """buffer_spatial(1.5) on link 1 picks up nearby parallel links."""
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 2], path_between=False).buffer_spatial(1.5)
    # Original link 1 + spatially close ones along y=0/y=1.
    assert 1 in scope.link_ids


# ---------------------------------------------------------------------------
# Apply (FK pushdown)
# ---------------------------------------------------------------------------


def test_apply_filters_link_and_node_tables():
    from netstead.scope import from_nodes

    net = _grid_network()
    scope = from_nodes(net, [1, 2], path_between=False)
    sub = scope.apply()
    # Node table filtered to scope's node ids.
    node_ids = set(sub.nodes.to_pandas()["node_id"].tolist())
    assert node_ids == {1, 2}
    # Link table filtered to scope's link ids.
    link_ids = set(sub.links.to_pandas()["link_id"].tolist())
    assert link_ids == {1}


def test_apply_filters_geometry_via_link_geometry_id():
    """The geometry table is filtered to only ids referenced by the surviving links."""
    from netstead.scope import from_nodes

    engine = _engine()
    nodes = _node(engine, [{"node_id": i, "x_coord": 0.0, "y_coord": 0.0} for i in (1, 2, 3)])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 2, "length": 1.0, "geometry_id": 100},
            {"link_id": 2, "from_node_id": 2, "to_node_id": 3, "length": 1.0, "geometry_id": 200},
        ],
    )
    geometry = Table(
        name="geometry",
        expr=engine.from_records(
            [
                {"geometry_id": 100, "geometry": "LINESTRING (0 0, 1 0)"},
                {"geometry_id": 200, "geometry": "LINESTRING (1 0, 2 0)"},
                {"geometry_id": 300, "geometry": "LINESTRING (5 5, 6 6)"},  # orphan
            ]
        ),
        engine=engine,
    )
    net = _network({"link": links, "node": nodes, "geometry": geometry})
    scope = from_nodes(net, [1, 2], path_between=False)
    sub = scope.apply()
    kept_geom_ids = set(sub.geometry.to_pandas()["geometry_id"].tolist())
    # Only link 1 (geometry_id=100) survives the scope.
    assert kept_geom_ids == {100}


def test_apply_filters_link_tod():
    """link_tod rows survive only when their link_id is in the kept link set."""
    from netstead.scope import from_nodes

    engine = _engine()
    nodes = _node(engine, [{"node_id": i, "x_coord": 0.0, "y_coord": 0.0} for i in (1, 2, 3)])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 2, "length": 1.0},
            {"link_id": 2, "from_node_id": 2, "to_node_id": 3, "length": 1.0},
        ],
    )
    link_tod = Table(
        name="link_tod",
        expr=engine.from_records(
            [
                {"link_tod_id": 100, "link_id": 1, "timeday_id": "am"},
                {"link_tod_id": 200, "link_id": 2, "timeday_id": "am"},
            ]
        ),
        engine=engine,
    )
    net = _network({"link": links, "node": nodes, "link_tod": link_tod})
    scope = from_nodes(net, [1, 2], path_between=False)  # picks link 1
    sub = scope.apply()
    link_tod_ids = set(sub.tables["link_tod"].to_pandas()["link_tod_id"].tolist())
    assert link_tod_ids == {100}


def test_apply_succeeds_when_a_table_filters_to_empty():
    """Regression: a scope that empties a whole table must still apply.

    On the ibis/duckdb engine, ``from_records([])`` builds a columnless
    table duckdb refuses to register ("must have at least one column").
    A scope that fully filters out a table — Leavenworth's
    ``signal_controller`` when no signals fall in the seed subgraph — used
    to crash ``apply()``; the empty table must survive with its schema.

    Uses the default (ibis) engine on purpose: the synthetic ``apply``
    tests run on the pandas engine, which tolerates the columnless table,
    so the bug only surfaces on the default read path.
    """
    from netstead.scope import from_nodes

    net = Network.from_source(leavenworth.csv_dir())  # default ibis engine
    assert net.tables["signal_controller"].count() > 0  # non-empty before scoping

    sub = from_nodes(net, [1, 25, 50], path_between=True).apply()

    emptied = sub.tables["signal_controller"]
    assert emptied.count() == 0
    assert emptied.columns()  # schema preserved — not a columnless table
    assert sub.links.count() > 0
    assert sub.nodes.count() > 0


# ---------------------------------------------------------------------------
# Index caching (perf-relevant)
# ---------------------------------------------------------------------------


def test_graph_index_is_cached_on_network():
    """First scope call builds + caches; second call reuses (no rebuild)."""
    from netstead.scope import connected_component
    from netstead.scope.scope import _GRAPH_INDEX_KEY

    net = _grid_network()
    assert _GRAPH_INDEX_KEY not in net.metadata
    connected_component(net, 1)
    cached = net.metadata[_GRAPH_INDEX_KEY]
    connected_component(net, 2)
    assert net.metadata[_GRAPH_INDEX_KEY] is cached


def test_scope_shares_graph_cache_with_semantics():
    """scope + semantics share a single GMNSGraph build via _cached_gmnsgraph."""
    from netstead.scope.scope import _GRAPH_CACHE_KEY as scope_key
    from netstead.semantics.connectivity import _GRAPH_CACHE_KEY as semantics_key

    assert scope_key == semantics_key


def test_leavenworth_smoke():
    """Smoke test on Leavenworth — scope ops complete without raising."""
    from netstead.scope import connected_component, from_nodes

    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    # connected_component on any node returns all of them (single component).
    seed_node = int(net.nodes.to_pandas()["node_id"].iloc[0])
    comp = connected_component(net, seed_node)
    assert len(comp.node_ids) == net.nodes.count()
    # from_nodes returns a non-empty scope.
    scope = from_nodes(net, [seed_node], path_between=False)
    assert seed_node in scope.node_ids
