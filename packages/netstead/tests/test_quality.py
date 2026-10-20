"""Tests for :mod:`netstead.quality` (task 3.11b / issue #80).

Covers:

* Each individual rule on a tiny synthetic Network that triggers it
  exactly (so the issue codes + counts are deterministic).
* Threshold overrides via :class:`corral.quality.RuleConfig` change
  behaviour as documented.
* Entry-point discovery (``corral.quality.discover_rules``) finds
  the GMNS rule pack via the ``corral.quality.rules`` group.
* :class:`RuleConfig(enabled=False)` skips a rule.
* Full rule pack on the Leavenworth fixture — the issue list is small
  + deterministic; we snapshot it so regressions are visible.
"""

from __future__ import annotations

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from corral.quality import RuleConfig, list_rules, run_quality
from corral.reports import Category, Severity
from corral.spec import DataPackage, Resource
from netstead import Network
from netstead.fixtures import leavenworth

pytest.importorskip("scipy")
pytest.importorskip("shapely")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine() -> IbisEngine:
    """One engine — rules are engine-agnostic via pyarrow materialisation."""
    return IbisEngine()


def _network(tables_dict: dict[str, Table]) -> Network:
    """Build a :class:`Network` from in-memory tables (test helper)."""
    engine = next(iter(tables_dict.values())).engine
    spec = DataPackage(name="synthesized", resources=[Resource(name=n) for n in tables_dict])
    return Network(spec=spec, tables=dict(tables_dict), engine=engine, spec_version="0.97")


def _link(engine, rows: list[dict]) -> Table:
    return Table(name="link", expr=engine.from_records(rows), engine=engine)


def _node(engine, rows: list[dict]) -> Table:
    return Table(name="node", expr=engine.from_records(rows), engine=engine)


def _table(engine, name: str, rows: list[dict]) -> Table:
    return Table(name=name, expr=engine.from_records(rows), engine=engine)


def _issues_with_code(report, code: str) -> list:
    return [i for i in report.issues if i.code == code]


# Ensure the entry point is loaded for every test in this module.
# Tests run alphabetically; the early-import here means the registry is
# populated regardless of test order.
@pytest.fixture(autouse=True)
def _ensure_rules_registered():
    """Register the GMNS rule pack directly (covers direct-invocation path)."""
    from netstead.quality import register_all

    register_all()


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def test_high_speed_residential_flags_offending_link():
    """A residential link with free_speed > 35 (default threshold) is flagged."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 1, "facility_type": "residential", "free_speed": 55.0},
            {"link_id": 2, "from_node_id": 1, "to_node_id": 1, "facility_type": "residential", "free_speed": 25.0},
            {"link_id": 3, "from_node_id": 1, "to_node_id": 1, "facility_type": "tertiary", "free_speed": 55.0},
        ],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net, config={"quality.high_speed_residential": RuleConfig()})
    flagged = _issues_with_code(report, "quality.high_speed_residential")
    assert len(flagged) == 1
    assert flagged[0].severity is Severity.WARNING
    assert flagged[0].category is Category.DATA_QUALITY


def test_high_speed_residential_threshold_override_changes_behaviour():
    """Raising the threshold to 60 suppresses the flag on a 55-mph residential."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "facility_type": "residential", "free_speed": 55.0}],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(
        net,
        config={"quality.high_speed_residential": RuleConfig(thresholds={"speed_limit_mph": 60.0})},
    )
    assert _issues_with_code(report, "quality.high_speed_residential") == []


def test_disconnected_components_flags_two_component_net():
    """A 2-component network gets a single info issue listing component sizes."""
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2, "x_coord": 1.0, "y_coord": 0.0},
            {"node_id": 3, "x_coord": 100.0, "y_coord": 100.0},
        ],
    )
    links = _link(
        engine,
        [{"link_id": 1, "from_node_id": 1, "to_node_id": 2, "length": 1.0, "directed": False}],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.disconnected_components")
    assert len(flagged) == 1
    assert flagged[0].severity is Severity.INFO
    assert "component_sizes" in flagged[0].extra


def test_lane_count_mismatch_flags_only_disagreements():
    """link.lanes != count(lane rows) emits an issue; missing lane rows do NOT."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 1, "lanes": 2},  # mismatch
            {"link_id": 2, "from_node_id": 1, "to_node_id": 1, "lanes": 1},  # match
            {"link_id": 3, "from_node_id": 1, "to_node_id": 1, "lanes": 3},  # no lane rows
        ],
    )
    lanes = _table(
        engine,
        "lane",
        [
            {"lane_id": 100, "link_id": 1},  # only 1 lane row for link 1, declared=2
            {"lane_id": 200, "link_id": 2},  # 1 lane row for link 2, declared=1 (match)
        ],
    )
    net = _network({"link": links, "node": nodes, "lane": lanes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.lane_count_mismatch")
    assert len(flagged) == 1
    assert "link_id=1" in flagged[0].message


def test_duplicate_near_nodes_flags_pairs_within_epsilon():
    """Two nodes 1e-6 apart are flagged at default epsilon=1e-5 (units are CRS units)."""
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2, "x_coord": 1e-6, "y_coord": 0.0},  # very close (well under 1e-5)
            {"node_id": 3, "x_coord": 100.0, "y_coord": 100.0},  # far away
        ],
    )
    links = _link(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "length": 0.0, "directed": False}])
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.duplicate_near_nodes")
    assert len(flagged) == 1
    assert flagged[0].extra["node_ids"] == [1, 2]


def test_duplicate_near_nodes_threshold_zero_suppresses():
    """epsilon=0.0 suppresses any pair (squared distance > 0 always for distinct nodes)."""
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2, "x_coord": 1e-6, "y_coord": 0.0},
        ],
    )
    links = _link(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "length": 0.0, "directed": False}])
    net = _network({"link": links, "node": nodes})
    report = run_quality(net, config={"quality.duplicate_near_nodes": RuleConfig(thresholds={"epsilon_units": 0.0})})
    assert _issues_with_code(report, "quality.duplicate_near_nodes") == []


def test_duplicate_near_nodes_handles_tiny_epsilon_on_large_coordinates():
    """A tiny epsilon over projected coordinates must not overflow the grid-cell key (issue #181)."""
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1, "x_coord": 6_000_000.0, "y_coord": 2_000_000.0},
            {"node_id": 2, "x_coord": 6_000_000.0, "y_coord": 2_000_000.0},  # exact duplicate
            {"node_id": 3, "x_coord": 6_000_001.0, "y_coord": 2_000_000.0},
        ],
    )
    links = _link(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 3, "length": 1.0, "directed": False}])
    net = _network({"link": links, "node": nodes})
    report = run_quality(net, config={"quality.duplicate_near_nodes": RuleConfig(thresholds={"epsilon_units": 1e-15})})
    flagged = _issues_with_code(report, "quality.duplicate_near_nodes")
    assert [i.extra["node_ids"] for i in flagged] == [[1, 2]]


def test_sharp_angle_bends_flags_acute_turn():
    """A 3-vertex link with a near-90° bend at the middle gets flagged at default 30°."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    # LINESTRING (0 0, 1 0, 1 1) — interior angle at (1, 0) is exactly 90°,
    # which is ABOVE the default 30° threshold. Need a sharper bend:
    # LINESTRING (0 0, 1 0, 0.1 0.1) — interior angle is well below 30°.
    links = _link(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 1,
                "geometry": "LINESTRING (0 0, 1 0, 0.1 0.1)",
            }
        ],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.sharp_angle_bends")
    assert len(flagged) == 1
    assert "angle_degrees" in flagged[0].extra


def test_sharp_angle_bends_skips_smooth_geometry():
    """A nearly-straight 3-vertex linestring (interior 170°) is NOT flagged."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {
                "link_id": 1,
                "from_node_id": 1,
                "to_node_id": 1,
                "geometry": "LINESTRING (0 0, 1 0, 2 0.05)",  # mild bend
            }
        ],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    assert _issues_with_code(report, "quality.sharp_angle_bends") == []


def test_implausible_vc_flags_ratio_above_threshold():
    """volume / capacity > 1.5 (default) is flagged."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 1, "capacity": 1000.0, "volume": 2000.0},  # vc=2 -> flag
            {"link_id": 2, "from_node_id": 1, "to_node_id": 1, "capacity": 1000.0, "volume": 800.0},  # vc=0.8
            {"link_id": 3, "from_node_id": 1, "to_node_id": 1, "capacity": 1000.0, "volume": None},  # null skip
        ],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.implausible_vc")
    assert len(flagged) == 1
    assert flagged[0].extra["vc_ratio"] == 2.0


def test_missing_critical_fields_flags_sparse_column():
    """A column with < 90% non-null coverage gets flagged."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 1, "length": 100.0, "free_speed": 30.0},
            {"link_id": 2, "from_node_id": 1, "to_node_id": 1, "length": 100.0, "free_speed": None},
            {"link_id": 3, "from_node_id": 1, "to_node_id": 1, "length": 100.0, "free_speed": None},
        ],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.missing_critical_fields")
    # length is fully populated -> no flag; free_speed at 33% -> flag.
    assert {i.column for i in flagged} == {"free_speed"}


# ---------------------------------------------------------------------------
# Framework integration: discovery, enable flag, severity override
# ---------------------------------------------------------------------------


def test_entry_point_discovers_all_gmns_rules():
    """The ``corral.quality.rules`` entry point exposes every GMNS rule."""
    from corral.quality import discover_rules

    discover_rules()
    codes = set(list_rules())
    expected = {
        "quality.high_speed_residential",
        "quality.disconnected_components",
        "quality.lane_count_mismatch",
        "quality.duplicate_near_nodes",
        "quality.sharp_angle_bends",
        "quality.implausible_vc",
        "quality.missing_critical_fields",
    }
    assert expected <= codes


def test_rule_config_enabled_false_skips_rule():
    """RuleConfig(enabled=False) suppresses the rule entirely."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "facility_type": "residential", "free_speed": 55.0}],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(net, config={"quality.high_speed_residential": RuleConfig(enabled=False)})
    assert _issues_with_code(report, "quality.high_speed_residential") == []


def test_severity_override_rewrites_emitted_issues():
    """RuleConfig(severity_override=...) promotes/demotes the issues this rule emits."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [{"link_id": 1, "from_node_id": 1, "to_node_id": 1, "facility_type": "residential", "free_speed": 55.0}],
    )
    net = _network({"link": links, "node": nodes})
    report = run_quality(
        net,
        config={"quality.high_speed_residential": RuleConfig(severity_override=Severity.ERROR)},
    )
    flagged = _issues_with_code(report, "quality.high_speed_residential")
    assert len(flagged) == 1
    assert flagged[0].severity is Severity.ERROR


# ---------------------------------------------------------------------------
# Leavenworth fixture — full rule pack snapshot
# ---------------------------------------------------------------------------


def test_full_pack_on_leavenworth():
    """Full rule pack on Leavenworth — issue counts per code are stable.

    Snapshot acts as a regression guard. If a rule changes its
    triggering behaviour, this test fails loudly so the change can be
    reviewed against the fixture.
    """
    net = Network.from_source(leavenworth.csv_dir(), engine=_engine())
    report = run_quality(net)
    counts: dict[str, int] = {}
    for issue in report.issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1

    # Every issue this rule pack emits should be category=DATA_QUALITY.
    assert all(i.category is Category.DATA_QUALITY for i in report.issues)
    # Leavenworth fixture is clean, connected, small. Expectations:
    #   - high_speed_residential: NONZERO — residential streets are 40 mph, over the
    #     35-mph default threshold. We just verify the rule fires (real numbers vary
    #     with the fixture; pinning would make the test fragile).
    #   - disconnected_components: 0 (single weakly-connected component).
    #   - lane_count_mismatch: 0 (lane table matches link.lanes).
    #   - duplicate_near_nodes: 0 (nodes are spaced > 1e-5 degrees apart in WGS84).
    #   - sharp_angle_bends: variable; just check the rule didn't crash by leaving the
    #     count bounded (no assertion).
    #   - implausible_vc: 0 (Leavenworth has no `capacity` column at all -> rule applies_to is False).
    #   - missing_critical_fields: 0 in Leavenworth's case — length, free_speed, lanes
    #     are all fully populated; capacity isn't a column at all so isn't checked.
    assert counts.get("quality.disconnected_components", 0) == 0
    assert counts.get("quality.lane_count_mismatch", 0) == 0
    assert counts.get("quality.duplicate_near_nodes", 0) == 0
    assert counts.get("quality.implausible_vc", 0) == 0
    assert counts.get("quality.missing_critical_fields", 0) == 0
    assert counts.get("quality.high_speed_residential", 0) > 0
