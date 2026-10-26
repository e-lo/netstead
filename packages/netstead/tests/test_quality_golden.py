"""Golden findings for the GMNS quality rule pack (issue #181).

Pins every field of every finding (code, severity, table, column, row, message, fix hint, extra) that the seven
original rules emit on the committed fixtures and on a synthetic network built to hit each rule's edge cases
(nulls, threshold equality, case and whitespace in ``facility_type``, zero capacity, 3-D and multi-part
geometries, a lane row with no ``link_id``). The rule implementations can change how they read the tables
(predicate pushdown through ibis instead of materializing them) but not what they report.

Regenerate after an intended behavior change with::

    NETSTEAD_REGEN_QUALITY_GOLDEN=1 uv run --frozen pytest packages/netstead/tests/test_quality_golden.py

and review the diff of ``fixtures/quality/golden.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from corral.quality import RuleConfig, run_quality
from corral.spec import DataPackage, Resource
from netstead import Network
from netstead.fixtures import leavenworth

pytest.importorskip("scipy")
pytest.importorskip("shapely")

GOLDEN = Path(__file__).parent / "fixtures" / "quality" / "golden.json"
#: Real OSM interchanges with inline link geometry (Leavenworth keeps geometry in a separate table).
RDU_I40_CSV = Path(leavenworth.__file__).parent.parent / "rdu_i40" / "csv"

#: The rules the golden file pins. Rules added later get their own tests, so a new rule never moves this file.
PINNED_CODES = (
    "quality.high_speed_residential",
    "quality.disconnected_components",
    "quality.lane_count_mismatch",
    "quality.duplicate_near_nodes",
    "quality.sharp_angle_bends",
    "quality.implausible_vc",
    "quality.missing_critical_fields",
)

#: Every threshold moved off its default, so the configured path is pinned too.
_TUNED = {
    "quality.high_speed_residential": RuleConfig(thresholds={"speed_limit_mph": 30.0}),
    "quality.duplicate_near_nodes": RuleConfig(thresholds={"epsilon_units": 0.0003}),
    "quality.sharp_angle_bends": RuleConfig(thresholds={"min_angle_degrees": 170.0}),
    "quality.implausible_vc": RuleConfig(thresholds={"vc_max": 1.0}),
    "quality.missing_critical_fields": RuleConfig(thresholds={"coverage_min": 0.5}),
}


def _table(engine: IbisEngine, name: str, rows: list[dict[str, Any]]) -> Table:
    return Table(name=name, expr=engine.from_records(rows), engine=engine)


def _edge_case_network() -> Network:
    """A small network with at least one hit, one near miss and one skipped row per rule.

    Numeric values are deliberately non-integral: the pre-pushdown rules read tables through
    ``Table.to_pandas()``, whose ``convert_dtypes`` turns an all-integral float column into ``Int64``, so a
    message quoted ``1000`` where the stored value is ``1000.0``. Non-integral values keep the pinned messages
    about the data rather than about that conversion.
    """
    engine = IbisEngine()
    link_rows = [
        # 1: hit for high-speed (whitespace + case), lane mismatch, v/c, sharp bend.
        {
            "link_id": 1,
            "from_node_id": 1,
            "to_node_id": 2,
            "length": 10.5,
            "directed": True,
            "facility_type": " Residential ",
            "free_speed": 50.5,
            "lanes": 2,
            "capacity": 1000.5,
            "volume": 2000.25,
            "geometry": "LINESTRING (0 0, 1 0, 0.1 0.1)",
        },
        # 2: at the speed threshold (not flagged); lanes match; zero capacity (skipped); smooth geometry.
        {
            "link_id": 2,
            "from_node_id": 2,
            "to_node_id": 3,
            "length": 11.5,
            "directed": True,
            "facility_type": "LOCAL",
            "free_speed": 35.0,
            "lanes": 1,
            "capacity": 0.0,
            "volume": 100.5,
            "geometry": "LINESTRING (0 0, 1 0, 2 0.05)",
        },
        # 3: null speed and lanes and capacity and geometry (all skipped).
        {
            "link_id": 3,
            "from_node_id": 3,
            "to_node_id": 4,
            "length": 12.5,
            "directed": True,
            "facility_type": "residential",
            "free_speed": None,
            "lanes": None,
            "capacity": None,
            "volume": 10.5,
            "geometry": None,
        },
        # 4: null facility type; no lane rows; v/c exactly at the threshold; multi-part geometry (skipped).
        {
            "link_id": 4,
            "from_node_id": 4,
            "to_node_id": 5,
            "length": 13.5,
            "directed": True,
            "facility_type": None,
            "free_speed": 80.5,
            "lanes": 3,
            "capacity": 100.5,
            "volume": 150.75,
            "geometry": "MULTILINESTRING ((0 0, 1 0, 0 0.1), (5 5, 6 6))",
        },
        # 5: fast but not residential; two lane rows against lanes=1; v/c hit; sharp 3-D bend.
        {
            "link_id": 5,
            "from_node_id": 5,
            "to_node_id": 6,
            "length": 14.5,
            "directed": True,
            "facility_type": "tertiary",
            "free_speed": 60.5,
            "lanes": 1,
            "capacity": 600.5,
            "volume": 1000.5,
            "geometry": "LINESTRING Z (0 0 0, 1 0 0, 0.2 0.05 0)",
        },
        # 6: null speed and null volume (skipped); two-point geometry (skipped).
        {
            "link_id": 6,
            "from_node_id": 6,
            "to_node_id": 1,
            "length": 15.5,
            "directed": True,
            "facility_type": "residential",
            "free_speed": None,
            "lanes": 2,
            "capacity": 700.5,
            "volume": None,
            "geometry": "LINESTRING (0 0, 1 1)",
        },
        # 7: a residential link 0.5 over the threshold, in a second component (isolated nodes 7-8).
        {
            "link_id": 7,
            "from_node_id": 7,
            "to_node_id": 8,
            "length": 16.5,
            "directed": False,
            "facility_type": "residential",
            "free_speed": 35.5,
            "lanes": 1,
            "capacity": 800.5,
            "volume": 900.5,
            "geometry": "LINESTRING (0 0, 0 1, 0.05 0.2, 0.05 5)",
        },
        # 8: NaN speed and capacity: read as missing, like null (Parquet can carry NaN; CSV cannot).
        {
            "link_id": 8,
            "from_node_id": 8,
            "to_node_id": 7,
            "length": 17.5,
            "directed": True,
            "facility_type": "residential",
            "free_speed": float("nan"),
            "lanes": 1,
            "capacity": float("nan"),
            "volume": 5.5,
            "geometry": "",
        },
    ]
    node_rows = [
        {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
        {"node_id": 2, "x_coord": 1e-6, "y_coord": 0.0},  # near node 1
        {"node_id": 3, "x_coord": 0.0, "y_coord": 1e-6},  # near nodes 1 and 2
        {"node_id": 4, "x_coord": 100.0, "y_coord": 100.0},
        {"node_id": 5, "x_coord": None, "y_coord": 3.0},  # null coordinate: skipped
        {"node_id": 6, "x_coord": 100.000005, "y_coord": 100.0},  # near node 4
        {"node_id": 7, "x_coord": 50.0, "y_coord": 50.0},
        {"node_id": 8, "x_coord": 50.0002, "y_coord": 50.0},  # near 7 only at the tuned epsilon
        {"node_id": 9, "x_coord": float("nan"), "y_coord": 0.0},  # NaN coordinate: skipped, like null
    ]
    lane_rows = [
        {"lane_id": 1, "link_id": 1, "lane_num": 1},
        {"lane_id": 2, "link_id": 2, "lane_num": 1},
        {"lane_id": 3, "link_id": 5, "lane_num": 1},
        {"lane_id": 4, "link_id": 5, "lane_num": 2},
        {"lane_id": 5, "link_id": None, "lane_num": 1},
        {"lane_id": 6, "link_id": 7, "lane_num": 1},
    ]
    tables = {
        "link": _table(engine, "link", link_rows),
        "node": _table(engine, "node", node_rows),
        "lane": _table(engine, "lane", lane_rows),
    }
    spec = DataPackage(name="edge_cases", resources=[Resource(name=n) for n in tables])
    return Network(spec=spec, tables=tables, engine=engine, spec_version="0.97")


def _cases() -> dict[str, tuple[Any, dict[str, RuleConfig] | None]]:
    return {
        "leavenworth_csv": (lambda: Network.from_source(leavenworth.csv_dir(), engine=IbisEngine()), None),
        "leavenworth_parquet": (lambda: Network.from_source(leavenworth.parquet_dir(), engine=IbisEngine()), None),
        "rdu_i40_csv": (lambda: Network.from_source(RDU_I40_CSV, engine=IbisEngine()), None),
        "rdu_i40_csv_tuned": (lambda: Network.from_source(RDU_I40_CSV, engine=IbisEngine()), _TUNED),
        "edge_cases": (_edge_case_network, None),
        "edge_cases_tuned": (_edge_case_network, _TUNED),
    }


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so the comparison sees exactly what the golden file stores."""
    return json.loads(json.dumps(value, default=str))


def _findings(net: Network, config: dict[str, RuleConfig] | None) -> list[dict[str, Any]]:
    report = run_quality(net, config=config)
    rows = [
        {
            "code": i.code,
            "severity": i.severity.value,
            "category": i.category.value,
            "table": i.table,
            "column": i.column,
            "row": i.row,
            "message": i.message,
            "fix_hint": i.fix_hint,
            "extra": i.extra,
        }
        for i in report.issues
        if i.code in PINNED_CODES
    ]
    # MissingCriticalFieldsRule walks a frozenset, so its issue order varies with the hash seed: compare sorted.
    rows.sort(key=lambda r: (r["code"], r["row"] if r["row"] is not None else -1, r["column"] or "", r["message"]))
    return _jsonable(rows)


@pytest.fixture(autouse=True)
def _registered():
    from netstead.quality import register_all

    register_all()


def _load_golden() -> dict[str, Any]:
    if not GOLDEN.exists():
        return {}
    return json.loads(GOLDEN.read_text())


@pytest.mark.parametrize("case", list(_cases()))
def test_quality_findings_match_golden(case: str) -> None:
    build, config = _cases()[case]
    actual = _findings(build(), config)
    if os.environ.get("NETSTEAD_REGEN_QUALITY_GOLDEN"):
        golden = _load_golden()
        golden[case] = actual
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
        return
    golden = _load_golden()
    assert case in golden, f"no golden findings for {case!r}; regenerate (see module docstring)"
    assert actual == golden[case]


def test_edge_cases_hit_every_pinned_rule() -> None:
    """The synthetic network is only useful as a golden if each original rule fires on it."""
    codes = {f["code"] for f in _findings(_edge_case_network(), None)}
    assert codes == set(PINNED_CODES)
