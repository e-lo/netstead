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


def _shape_network(engine, shapes: list[str | None], extra_links: dict | None = None) -> Network:
    """One link per WKT shape (link_id 1..n), all on a single node so only the geometry matters."""
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    rows = [
        {"link_id": i, "from_node_id": 1, "to_node_id": 1, "length": 1.0, "directed": True, "geometry": wkt}
        for i, wkt in enumerate(shapes, start=1)
    ]
    return _network({"link": _link(engine, rows), "node": nodes})


def _overlaps(report) -> list[tuple[list, float]]:
    return [
        (i.extra["link_ids"], i.extra["overlap_length"]) for i in _issues_with_code(report, "quality.overlapping_links")
    ]


def test_overlapping_links_flags_a_shared_stretch():
    """Two links sharing part of their length are flagged once; touching or crossing links are not (issue #5)."""
    net = _shape_network(
        _engine(),
        [
            "LINESTRING (0 0, 2 0)",
            "LINESTRING (1 0, 3 0)",  # shares 1 -> 2 with link 1
            "LINESTRING (2 0, 2 1)",  # touches links 1 and 2 at a point
            "LINESTRING (0.5 -1, 0.5 1)",  # crosses link 1
            "LINESTRING (5 5, 6 6)",
            None,
        ],
    )
    report = run_quality(net)
    assert _overlaps(report) == [([1, 2], 1.0)]
    issue = _issues_with_code(report, "quality.overlapping_links")[0]
    assert issue.severity is Severity.WARNING
    assert issue.category is Category.DATA_QUALITY
    assert (issue.table, issue.column, issue.row) == ("link", "geometry", 0)
    assert issue.extra["rows"] == [0, 1]


def test_overlapping_links_flags_a_link_lying_along_another():
    """A shorter link running along part of a longer one is an overlap too."""
    net = _shape_network(_engine(), ["LINESTRING (0 0, 3 0)", "LINESTRING (1 0, 2 0)"])
    assert _overlaps(run_quality(net)) == [([1, 2], 1.0)]


def test_overlapping_links_ignores_identical_shapes():
    """Reverse-direction twins with the same shape are the normal two-way encoding, not an overlap."""
    net = _shape_network(_engine(), ["LINESTRING (0 0, 1 0)", "LINESTRING (1 0, 0.5 0, 0 0)"])
    assert _overlaps(run_quality(net)) == []


def test_overlapping_links_min_overlap_threshold():
    """``min_overlap_units`` ignores shared stretches no longer than it."""
    net = _shape_network(_engine(), ["LINESTRING (0 0, 2 0)", "LINESTRING (1 0, 3 0)"])
    config = {"quality.overlapping_links": RuleConfig(thresholds={"min_overlap_units": 1.0})}
    assert _overlaps(run_quality(net, config=config)) == []


def test_overlapping_links_reads_shapes_from_the_geometry_table():
    """Links without inline geometry take their shape from the geometry table via geometry_id."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 10, "from_node_id": 1, "to_node_id": 1, "length": 1.0, "geometry_id": 100},
            {"link_id": 20, "from_node_id": 1, "to_node_id": 1, "length": 1.0, "geometry_id": 200},
        ],
    )
    geometry = _table(
        engine,
        "geometry",
        [
            {"geometry_id": 100, "geometry": "LINESTRING (0 0, 2 0)"},
            {"geometry_id": 200, "geometry": "LINESTRING (0.5 0, 1.5 0, 1.5 1)"},
        ],
    )
    net = _network({"link": links, "node": nodes, "geometry": geometry})
    assert _overlaps(run_quality(net)) == [([10, 20], 1.0)]


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


def _tod_network(engine, tod_rows: list[dict], extra: dict[str, Table] | None = None) -> Network:
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [{"link_id": i, "from_node_id": 1, "to_node_id": 1, "length": 1.0, "directed": True} for i in (1, 2)],
    )
    tables = {"link": links, "node": nodes, "link_tod": _table(engine, "link_tod", tod_rows)}
    tables.update(extra or {})
    return _network(tables)


def test_non_unique_combination_flags_repeated_link_and_time_day():
    """Two link_tod rows for the same (link_id, time_day) give one issue naming both rows (issue #7)."""
    engine = _engine()
    net = _tod_network(
        engine,
        [
            {"link_tod_id": 1, "link_id": 1, "time_day": "1111100_0700_0900"},
            {"link_tod_id": 2, "link_id": 1, "time_day": "1111100_1600_1800"},  # different window: fine
            {"link_tod_id": 3, "link_id": 2, "time_day": "1111100_0700_0900"},  # different link: fine
            {"link_tod_id": 4, "link_id": 1, "time_day": "1111100_0700_0900"},  # repeats row 0
            {"link_tod_id": 5, "link_id": None, "time_day": "1111100_0700_0900"},  # null key part: skipped
            {"link_tod_id": 6, "link_id": None, "time_day": "1111100_0700_0900"},
        ],
    )
    report = run_quality(net)
    flagged = _issues_with_code(report, "quality.non_unique_combination")
    assert len(flagged) == 1
    issue = flagged[0]
    assert issue.severity is Severity.WARNING
    assert issue.category is Category.DATA_QUALITY
    assert issue.table == "link_tod"
    assert issue.row == 0
    assert issue.extra["columns"] == ["link_id", "time_day"]
    assert issue.extra["values"] == {"link_id": 1, "time_day": "1111100_0700_0900"}
    assert issue.extra["rows"] == [0, 3]
    assert issue.extra["count"] == 2
    assert "link_id=1" in issue.message


def test_non_unique_combination_checks_timeday_id_and_lane_num_by_default():
    """The defaults also cover (link_id, timeday_id) on link_tod and (link_id, lane_num) on lane."""
    engine = _engine()
    lanes = _table(
        engine,
        "lane",
        [
            {"lane_id": 1, "link_id": 1, "lane_num": 1},
            {"lane_id": 2, "link_id": 1, "lane_num": 2},
            {"lane_id": 3, "link_id": 1, "lane_num": 1},  # repeats lane 1's number
        ],
    )
    net = _tod_network(
        engine,
        [
            {"link_tod_id": 1, "link_id": 2, "timeday_id": "am"},
            {"link_tod_id": 2, "link_id": 2, "timeday_id": "am"},
        ],
        extra={"lane": lanes},
    )
    flagged = _issues_with_code(run_quality(net), "quality.non_unique_combination")
    assert sorted((i.table, tuple(i.extra["columns"]), i.row) for i in flagged) == [
        ("lane", ("link_id", "lane_num"), 0),
        ("link_tod", ("link_id", "timeday_id"), 0),
    ]


def test_non_unique_combination_combinations_threshold_replaces_the_defaults():
    """``thresholds["combinations"]`` names the tables and column combinations to check, replacing the defaults."""
    engine = _engine()
    net = _tod_network(
        engine,
        [
            {"link_tod_id": 1, "link_id": 1, "time_day": "x", "toll": 1.5},
            {"link_tod_id": 2, "link_id": 1, "time_day": "x", "toll": 2.5},
        ],
    )
    custom = RuleConfig(
        thresholds={"combinations": {"link_tod": [["link_id", "toll"]], "link": [["from_node_id", "to_node_id"]]}}
    )
    flagged = _issues_with_code(
        run_quality(net, config={"quality.non_unique_combination": custom}), "quality.non_unique_combination"
    )
    # link_tod (link_id, time_day) is no longer checked; link (from, to) repeats on both links.
    assert [(i.table, i.extra["rows"]) for i in flagged] == [("link", [0, 1])]


def test_non_unique_combination_skips_absent_tables_and_columns():
    """A default combination whose table or columns are missing is skipped silently, not an error."""
    engine = _engine()
    net = _tod_network(engine, [{"link_tod_id": 1, "link_id": 1}])  # neither time_day nor timeday_id
    report = run_quality(net)
    assert _issues_with_code(report, "quality.non_unique_combination") == []


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
        "quality.non_unique_combination",
        "quality.overlapping_links",
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
    assert counts.get("quality.non_unique_combination", 0) == 0  # lane numbers and link_tod rows are unique
    assert counts.get("quality.overlapping_links", 0) == 0  # no two links share a stretch of road
    assert counts.get("quality.high_speed_residential", 0) > 0


# ---------------------------------------------------------------------------
# Review follow-ups on PR #246
# ---------------------------------------------------------------------------


def test_whole_number_float_ids_print_as_ints():
    """A float link_id column holding only whole numbers reads as ints in messages and extra; NaN reads as None."""
    engine = _engine()
    nodes = _node(
        engine,
        [
            {"node_id": 1.0, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2.0, "x_coord": 1e-6, "y_coord": 0.0},
            {"node_id": float("nan"), "x_coord": 0.0, "y_coord": 1e-6},  # NaN id: not a node, like a null one
        ],
    )
    links = _link(
        engine,
        [
            {"link_id": 1.0, "facility_type": "residential", "free_speed": 50.5, "geometry": "LINESTRING (0 0, 2 0)"},
            {
                "link_id": float("nan"),
                "facility_type": "local",
                "free_speed": 60.5,
                "geometry": "LINESTRING (1 0, 3 0)",
            },
        ],
    )
    report = run_quality(_network({"link": links, "node": nodes}))
    speed = _issues_with_code(report, "quality.high_speed_residential")
    assert [i.message.split(":")[0] for i in speed] == ["link row 0 (link_id=1)", "link row 1 (link_id=None)"]
    assert [i.extra["node_ids"] for i in _issues_with_code(report, "quality.duplicate_near_nodes")] == [[1, 2]]
    assert [i.extra["link_ids"] for i in _issues_with_code(report, "quality.overlapping_links")] == [[1, None]]


def test_fractional_float_ids_keep_their_float_form():
    """Only an all-whole-number column is shown as ints: one fractional id keeps the whole column as floats."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [
            {"link_id": 1.0, "facility_type": "residential", "free_speed": 50.5},
            {"link_id": 1.5, "facility_type": "tertiary", "free_speed": 20.5},
        ],
    )
    flagged = _issues_with_code(run_quality(_network({"link": links, "node": nodes})), "quality.high_speed_residential")
    assert "(link_id=1.0)" in flagged[0].message


@pytest.mark.parametrize(
    "ftype", ["\vresidential\v", "\xa0Local\xa0", "residential" + chr(0x3000), "\x1cresidential\x85"]
)
def test_high_speed_residential_strips_whitespace_like_python(ftype):
    """facility_type is compared after str.strip(), so vertical tabs, NBSP and other Unicode spaces are ignored."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(engine, [{"link_id": 1, "facility_type": ftype, "free_speed": 50.5}])
    report = run_quality(_network({"link": links, "node": nodes}))
    assert len(_issues_with_code(report, "quality.high_speed_residential")) == 1


def test_high_speed_whitespace_class_matches_python_isspace():
    """The engine-side whitespace class is exactly the characters Python's str.strip() removes."""
    from netstead.quality.rules import _PY_WHITESPACE

    assert set(_PY_WHITESPACE) == {c for c in map(chr, range(0x110000)) if c.isspace()}


def test_overlapping_links_reports_each_pair_once_when_geometry_ids_repeat():
    """A geometry table that repeats a geometry_id must not duplicate the pair (or pair a link with itself)."""
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(
        engine,
        [{"link_id": 10, "geometry_id": 100}, {"link_id": 20, "geometry_id": 200}],
    )
    geometry = _table(
        engine,
        "geometry",
        [
            {"geometry_id": 100, "geometry": "LINESTRING (0 0, 2 0)"},
            {"geometry_id": 100, "geometry": "LINESTRING (0 0, 2 0)"},
            {"geometry_id": 200, "geometry": "LINESTRING (1 0, 3 0)"},
            {"geometry_id": 200, "geometry": "LINESTRING (1 0, 3 0)"},
        ],
    )
    net = _network({"link": links, "node": nodes, "geometry": geometry})
    assert _overlaps(run_quality(net)) == [([10, 20], 1.0)]


def test_non_unique_combination_defaults_include_signal_timing_phase():
    from netstead.quality import DEFAULT_UNIQUE_COMBINATIONS

    assert ("timing_plan_id", "signal_phase_num") in DEFAULT_UNIQUE_COMBINATIONS["signal_timing_phase"]


def test_non_unique_combination_defaults_are_read_only():
    from netstead.quality import DEFAULT_UNIQUE_COMBINATIONS

    with pytest.raises(TypeError):
        DEFAULT_UNIQUE_COMBINATIONS["link"] = [["link_id"]]  # type: ignore[index]
    values = DEFAULT_UNIQUE_COMBINATIONS.values()
    assert all(isinstance(v, tuple) and all(isinstance(c, tuple) for c in v) for v in values)


def test_non_unique_combination_logs_configured_combinations_it_cannot_check(caplog):
    """A user-configured table or column that is missing is logged; missing default tables stay silent.

    A log line rather than an issue: ``run_quality`` applies ``severity_override`` to every issue a rule emits,
    so a "not checked" note would turn into an ERROR alongside the real duplicates.
    """
    engine = _engine()
    net = _tod_network(engine, [{"link_tod_id": 1, "link_id": 1, "time_day": "x"}])
    custom = RuleConfig(
        thresholds={"combinations": {"link_tod": [["link_id", "no_such_column"]], "movement_tod": [["mvmt_id"]]}}
    )
    with caplog.at_level("WARNING", logger="netstead.quality.rules"):
        report = run_quality(net, config={"quality.non_unique_combination": custom})
    assert [i for i in report.issues if i.code.startswith("quality.non_unique_combination")] == []
    notes = [r.getMessage() for r in caplog.records if r.name == "netstead.quality.rules"]
    assert len(notes) == 2
    assert "no_such_column" in notes[0] and "movement_tod" in notes[1]
    # The defaults name many tables Leavenworth lacks: none of them is reported or logged.
    caplog.clear()
    with caplog.at_level("WARNING", logger="netstead.quality.rules"):
        report = run_quality(Network.from_source(leavenworth.csv_dir(), engine=_engine()))
    assert _issues_with_code(report, "quality.non_unique_combination") == []
    assert not [r for r in caplog.records if r.name == "netstead.quality.rules"]


def test_non_unique_combination_severity_override_does_not_escalate_unchecked_notes():
    """severity_override=ERROR promotes real duplicates only; a missing optional table never becomes an ERROR."""
    engine = _engine()
    net = _tod_network(
        engine,
        [
            {"link_tod_id": 1, "link_id": 1, "time_day": "x"},
            {"link_tod_id": 2, "link_id": 1, "time_day": "x"},
        ],
    )
    custom = RuleConfig(
        severity_override=Severity.ERROR,
        thresholds={"combinations": {"link_tod": [["link_id", "time_day"]], "movement_tod": [["mvmt_id", "time_day"]]}},
    )
    report = run_quality(net, config={"quality.non_unique_combination": custom})
    mine = [i for i in report.issues if i.code.startswith("quality.non_unique_combination")]
    assert [(i.severity, i.table) for i in mine] == [(Severity.ERROR, "link_tod")]


@pytest.mark.parametrize("bad", [[["link_id", "time_day"]], "link_tod", {"link_tod": "link_id"}])
def test_non_unique_combination_rejects_malformed_combinations(bad):
    engine = _engine()
    net = _tod_network(engine, [{"link_tod_id": 1, "link_id": 1, "time_day": "x"}])
    with pytest.raises(ValueError, match="combinations"):
        run_quality(net, config={"quality.non_unique_combination": RuleConfig(thresholds={"combinations": bad})})


def test_duplicate_near_nodes_rejects_negative_epsilon():
    engine = _engine()
    nodes = _node(engine, [{"node_id": 1, "x_coord": 0.0, "y_coord": 0.0}])
    links = _link(engine, [{"link_id": 1, "from_node_id": 1, "to_node_id": 1}])
    config = {"quality.duplicate_near_nodes": RuleConfig(thresholds={"epsilon_units": -1.0})}
    with pytest.raises(ValueError, match="epsilon_units"):
        run_quality(_network({"link": links, "node": nodes}), config=config)
