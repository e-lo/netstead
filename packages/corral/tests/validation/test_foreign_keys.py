"""Unit tests for :mod:`corral.validation.foreign_keys` (task 2.4 / issue #63).

The foreign-key validator walks a multi-table package and verifies that
every non-null source value exists in the target column. Tests cover:

* Clean Leavenworth fixture (real GMNS data) round-trips clean across
  every installed engine.
* Synthetic corruption (delete a node, null an FK, point at a missing
  table, etc.) raises the expected :class:`Issue` with the
  documented code + severity.
* Composite FKs match on the full tuple of (source, ..., target, ...).
* Same-table FKs (``reference.resource == ""``) work for self-joins
  (the canonical case: ``node.parent_node_id -> node.node_id``).
* Bounded enumeration: more than ``MAX_ROW_ISSUES`` violations collapse
  into a summary Issue with ``extra["total_violations"]``.
* v0.3 regression: nulls in a source FK column no longer trigger the
  Series-vs-bool truthiness crash.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from corral.engines.ibis_engine import IbisEngine
from corral.spec.loader import load_package
from corral.spec.model import (
    Constraints,
    DataPackage,
    Field,
    ForeignKey,
    ForeignKeyReference,
    Resource,
    Schema,
)
from corral.validation import Category, Severity, ValidationReport
from corral.validation.foreign_keys import (
    MAX_ROW_ISSUES,
    check_foreign_key,
    check_foreign_keys,
)
from netstead.fixtures import leavenworth

# ---------------------------------------------------------------------------
# Engine matrix (mirrors test_schema_check.py)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard
    import polars  # noqa: F401

    _POLARS_AVAILABLE = True
except ImportError:  # pragma: no cover - polars not installed
    _POLARS_AVAILABLE = False


def _engine_for(name: str):
    """Build a fresh engine instance for the parametrised name."""
    if name == "ibis":
        return IbisEngine()
    if name == "polars":  # pragma: no cover - exercised only when polars installed
        from corral.engines.ibis_engine import IbisEngine as _PE

        return _PE()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine: {name}")  # pragma: no cover


ENGINES = [
    "ibis",
    pytest.param(
        "polars",
        marks=pytest.mark.skipif(not _POLARS_AVAILABLE, reason="polars not installed"),
    ),
    "pandas",
]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def leavenworth_package() -> DataPackage:
    """Load the bundled Leavenworth ``datapackage.json``."""
    return load_package(leavenworth.DATAPACKAGE)


def _load_rows(csv_path: Path) -> list[dict[str, Any]]:
    """Read a Leavenworth CSV as native-typed row dicts.

    All integer-looking fields are coerced to ``int`` (or ``None`` for
    blanks); everything else is left as a string. The engines' scan
    paths then normalize via ``cast_schema``.
    """
    with csv_path.open() as f:
        raw = list(csv.DictReader(f))
    int_cols = {
        "link_id",
        "from_node_id",
        "to_node_id",
        "node_id",
        "geometry_id",
        "lane_id",
        "lanes",
        "parent_link_id",
        "parent_node_id",
    }
    out: list[dict[str, Any]] = []
    for r in raw:
        new: dict[str, Any] = {}
        for k, v in r.items():
            if k in int_cols:
                if v in (None, ""):
                    new[k] = None
                else:
                    try:
                        new[k] = int(v)
                    except (TypeError, ValueError):
                        new[k] = v
            else:
                new[k] = v if v != "" else None
        out.append(new)
    return out


def _scan_rows(engine, rows: list[dict[str, Any]]):
    """Scan a list of row dicts via the engine ``{"data": rows}`` contract.

    All-null columns get dropped (ibis duckdb memtable doesn't like
    them); the validator's missing-column branch covers that case
    elsewhere.
    """
    if rows:
        keys = list(rows[0].keys())
        keep = [k for k in keys if any(r.get(k) is not None for r in rows)]
        if len(keep) != len(keys):
            rows = [{k: r.get(k) for k in keep} for r in rows]
    return engine.scan({"data": rows})


@pytest.fixture
def leavenworth_tables_pandas(leavenworth_package: DataPackage):
    """Build a {name: TableExpr} mapping on a pandas engine for whole-package tests.

    Yields (engine, tables) so the test body can pass both to
    :func:`check_foreign_keys`. Uses :func:`_load_rows` so the cross-engine
    behavior is hit indirectly via the per-engine parametrised tests.
    """
    engine = IbisEngine()
    tables: dict[str, Any] = {}
    csv_dir = leavenworth.csv_dir()
    for res in leavenworth_package.resources:
        path = csv_dir.parent / res.path  # ``path`` is "csv/link.csv"
        if not path.exists():
            continue
        rows = _load_rows(path)
        tables[res.name] = _scan_rows(engine, rows)
    yield engine, tables


# ---------------------------------------------------------------------------
# 1. Clean Leavenworth fixture (whole-package, all engines)
# ---------------------------------------------------------------------------


class TestCleanLeavenworthFixture:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_clean_leavenworth_fks_pass(self, engine_name, leavenworth_package):
        """A clean, real-data Leavenworth scan produces zero FK ERRORs."""
        engine = _engine_for(engine_name)
        tables: dict[str, Any] = {}
        csv_dir = leavenworth.csv_dir()
        try:
            for res in leavenworth_package.resources:
                path = csv_dir.parent / res.path
                if not path.exists():
                    continue
                rows = _load_rows(path)
                tables[res.name] = _scan_rows(engine, rows)
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # No FK ERROR should fire on the canonical fixture. (Unverifiable
        # WARNINGs are acceptable for optional resources we didn't load.)
        fk_errors = [i for i in report.issues if i.category is Category.FOREIGN_KEY and i.severity is Severity.ERROR]
        assert fk_errors == [], f"unexpected FK errors: {[i.message for i in fk_errors]}"


# ---------------------------------------------------------------------------
# 2. Missing target → ERROR (with row + target metadata)
# ---------------------------------------------------------------------------


class TestMissingTarget:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_missing_target_flagged(self, engine_name, leavenworth_package):
        """Corrupt link.csv so from_node_id=99999 has no matching node row."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            node_rows = _load_rows(csv_dir / "node.csv")
            link_rows[0]["from_node_id"] = 99999
            tables = {
                "link": _scan_rows(engine, link_rows),
                "node": _scan_rows(engine, node_rows),
            }
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = [i for i in report.issues if i.code == "fk.missing_target"]
        assert missing, "expected at least one fk.missing_target issue"
        issue = next(i for i in missing if i.column == "from_node_id")
        assert issue.severity is Severity.ERROR
        assert issue.table == "link"
        assert issue.row == 0
        assert "99999" in issue.message
        assert "node" in issue.message
        assert "node_id" in issue.message
        # extra carries the target metadata for the HTML renderer.
        assert issue.extra.get("target_table") == "node"
        assert issue.extra.get("target_field") == "node_id"
        assert issue.extra.get("value") == 99999


# ---------------------------------------------------------------------------
# 3. Null in a required FK column → ERROR
# ---------------------------------------------------------------------------


class TestNullInRequiredFK:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_null_in_required_fk_flagged(self, engine_name, leavenworth_package):
        """A null in link.from_node_id (required) emits fk.null_in_required_fk."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            node_rows = _load_rows(csv_dir / "node.csv")
            link_rows[1]["from_node_id"] = None
            tables = {
                "link": _scan_rows(engine, link_rows),
                "node": _scan_rows(engine, node_rows),
            }
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        nulls = [i for i in report.issues if i.code == "fk.null_in_required_fk"]
        assert nulls, "expected fk.null_in_required_fk issue"
        issue = next(i for i in nulls if i.column == "from_node_id")
        assert issue.severity is Severity.ERROR
        assert issue.row == 1


# ---------------------------------------------------------------------------
# 4 + 5. Unverifiable target table
# ---------------------------------------------------------------------------


class TestUnverifiable:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_unverifiable_warning(self, engine_name, leavenworth_package):
        """Missing target table downgrades to WARNING by default."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            tables = {"link": _scan_rows(engine, link_rows)}  # no "node"
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        unverifiable = [i for i in report.issues if i.code == "fk.unverifiable"]
        assert unverifiable, "expected fk.unverifiable WARNING when node is absent"
        assert all(i.severity is Severity.WARNING for i in unverifiable)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_unverifiable_strict_is_error(self, engine_name, leavenworth_package):
        """Under strict=True the missing-target downgrade becomes ERROR."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            tables = {"link": _scan_rows(engine, link_rows)}
            report = check_foreign_keys(leavenworth_package, tables, strict=True)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        unverifiable = [i for i in report.issues if i.code == "fk.unverifiable"]
        assert unverifiable
        assert all(i.severity is Severity.ERROR for i in unverifiable)


# ---------------------------------------------------------------------------
# 6. Target field missing — spec bug
# ---------------------------------------------------------------------------


class TestTargetFieldMissing:
    def test_target_field_missing(self):
        """An FK pointing at a non-existent target field becomes fk.target_field_missing."""
        engine = IbisEngine()
        try:
            src = engine.scan({"data": [{"id": 1, "ref": 1}]})
            tgt = engine.scan({"data": [{"id": 1}]})  # has "id", NOT "missing_col"
            fk = ForeignKey(
                fields="ref",
                reference=ForeignKeyReference(resource="tgt", fields="missing_col"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "fk.target_field_missing" for i in issues)
        issue = next(i for i in issues if i.code == "fk.target_field_missing")
        assert issue.severity is Severity.ERROR
        assert "missing_col" in issue.message
        assert "tgt" in issue.message


# ---------------------------------------------------------------------------
# 7 + 8. Composite FKs
# ---------------------------------------------------------------------------


class TestCompositeFK:
    def test_composite_fk_passes(self):
        """A 2-column FK with every tuple present in the target is clean."""
        engine = IbisEngine()
        try:
            src = engine.scan({"data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]})
            tgt = engine.scan({"data": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}]})
            fk = ForeignKey(
                fields=["a", "b"],
                reference=ForeignKeyReference(resource="tgt", fields=["x", "y"]),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert [i for i in issues if i.code == "fk.missing_target"] == []

    def test_composite_fk_fails_on_mismatched_tuple(self):
        """A row whose (a, b) doesn't match any target (x, y) raises one issue."""
        engine = IbisEngine()
        try:
            src = engine.scan({"data": [{"a": 1, "b": 2}, {"a": 9, "b": 9}]})
            tgt = engine.scan({"data": [{"x": 1, "y": 2}]})
            fk = ForeignKey(
                fields=["a", "b"],
                reference=ForeignKeyReference(resource="tgt", fields=["x", "y"]),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = [i for i in issues if i.code == "fk.missing_target"]
        assert len(missing) == 1
        assert missing[0].row == 1
        # Composite tuple value is surfaced.
        assert "9" in missing[0].message


# ---------------------------------------------------------------------------
# 9 + 10. Same-table (self-referential) FK
# ---------------------------------------------------------------------------


class TestSameTableFK:
    def test_same_table_fk_passes(self):
        """parent_id -> id, every parent points to an existing row → clean."""
        engine = IbisEngine()
        try:
            tbl = engine.scan(
                {
                    "data": [
                        {"id": 1, "parent_id": None},
                        {"id": 2, "parent_id": 1},
                        {"id": 3, "parent_id": 1},
                    ]
                }
            )
            fk = ForeignKey(
                fields="parent_id",
                reference=ForeignKeyReference(resource="", fields="id"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="tree",
                source_expr=tbl,
                target_table_name="tree",
                target_expr=tbl,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert [i for i in issues if i.code == "fk.missing_target"] == []

    def test_same_table_fk_fails_on_missing_parent(self):
        """An orphan parent_id is reported even when source == target."""
        engine = IbisEngine()
        try:
            tbl = engine.scan(
                {
                    "data": [
                        {"id": 1, "parent_id": None},
                        {"id": 2, "parent_id": 999},  # orphan
                    ]
                }
            )
            fk = ForeignKey(
                fields="parent_id",
                reference=ForeignKeyReference(resource="", fields="id"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="tree",
                source_expr=tbl,
                target_table_name="tree",
                target_expr=tbl,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = [i for i in issues if i.code == "fk.missing_target"]
        assert len(missing) == 1
        assert missing[0].row == 1
        assert "999" in missing[0].message


# ---------------------------------------------------------------------------
# 11. Bounded enumeration
# ---------------------------------------------------------------------------


class TestBoundedEnumeration:
    def test_bounded_enumeration(self):
        """500 violations collapse into MAX_ROW_ISSUES row issues + 1 summary."""
        engine = IbisEngine()
        try:
            # 500 source rows, each pointing at a missing target value.
            src_rows = [{"id": i, "ref": 1_000_000 + i} for i in range(500)]
            tgt_rows = [{"id": 0}]  # nothing matches
            src = engine.scan({"data": src_rows})
            tgt = engine.scan({"data": tgt_rows})
            fk = ForeignKey(
                fields="ref",
                reference=ForeignKeyReference(resource="tgt", fields="id"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = [i for i in issues if i.code == "fk.missing_target"]
        # MAX_ROW_ISSUES enumerated row-level + 1 summary.
        assert len(missing) == MAX_ROW_ISSUES + 1
        summary = [i for i in missing if "total_violations" in i.extra]
        assert len(summary) == 1
        assert summary[0].extra["total_violations"] == 500


# ---------------------------------------------------------------------------
# 12 + 13 + 14. Issue-shape contracts
# ---------------------------------------------------------------------------


class TestIssueContracts:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_issues_have_correct_category(self, engine_name, leavenworth_package):
        """Every issue emitted by the FK validator is Category.FOREIGN_KEY."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            node_rows = _load_rows(csv_dir / "node.csv")
            link_rows[0]["from_node_id"] = 99999
            tables = {
                "link": _scan_rows(engine, link_rows),
                "node": _scan_rows(engine, node_rows),
            }
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        fk_issues = [i for i in report.issues if i.code.startswith("fk.")]
        assert fk_issues
        assert all(i.category is Category.FOREIGN_KEY for i in fk_issues)

    def test_messages_include_value_and_target(self):
        """missing_target message names the source value AND target table.field."""
        engine = IbisEngine()
        try:
            src = engine.scan({"data": [{"id": 1, "ref": 42}]})
            tgt = engine.scan({"data": [{"key": 1}]})
            fk = ForeignKey(
                fields="ref",
                reference=ForeignKeyReference(resource="tgt", fields="key"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = next(i for i in issues if i.code == "fk.missing_target")
        assert "42" in missing.message
        assert "tgt" in missing.message
        assert "key" in missing.message

    def test_fix_hint_present_for_missing_target(self):
        """ERROR-level fk.missing_target issues include a fix_hint."""
        engine = IbisEngine()
        try:
            src = engine.scan({"data": [{"id": 1, "ref": 42}]})
            tgt = engine.scan({"data": [{"key": 1}]})
            fk = ForeignKey(
                fields="ref",
                reference=ForeignKeyReference(resource="tgt", fields="key"),
            )
            issues = check_foreign_key(
                fk,
                source_table_name="src",
                source_expr=src,
                target_table_name="tgt",
                target_expr=tgt,
            )
        finally:
            if hasattr(engine, "close"):
                engine.close()
        missing = next(i for i in issues if i.code == "fk.missing_target")
        assert missing.fix_hint
        assert "42" in missing.fix_hint or "tgt" in missing.fix_hint


# ---------------------------------------------------------------------------
# 15. v0.3 regression: null in source FK column no longer crashes
# ---------------------------------------------------------------------------


class TestV03Regression:
    def test_v03_fk_validator_regression(self):
        """v0.3 raised TypeError on ``if s.isna()`` when source held nulls.

        We pin the regression by feeding a source with both nulls AND a
        real missing-target row. The validator must (a) not raise, (b)
        emit ``fk.null_in_required_fk`` for the null, and (c) still
        catch the genuine missing-target separately. The structural
        guard is "never coerce a Series to a bool".
        """
        engine = IbisEngine()
        try:
            src = engine.scan(
                {
                    "data": [
                        {"id": 1, "ref": None},
                        {"id": 2, "ref": 99999},
                        {"id": 3, "ref": 1},
                    ]
                }
            )
            tgt = engine.scan({"data": [{"key": 1}]})
            fk = ForeignKey(
                fields="ref",
                reference=ForeignKeyReference(resource="tgt", fields="key"),
            )
            # Build a synthetic package so the null-vs-required check works.
            pkg = DataPackage(
                name="syn",
                resources=[
                    Resource(
                        name="src",
                        path="src.csv",
                        schema=Schema(
                            fields=[
                                Field(name="id", type="integer"),
                                Field(
                                    name="ref",
                                    type="integer",
                                    constraints=Constraints(required=True),
                                ),
                            ],
                            foreign_keys=[fk],
                        ),
                    ),
                    Resource(
                        name="tgt",
                        path="tgt.csv",
                        schema=Schema(fields=[Field(name="key", type="integer")]),
                    ),
                ],
            )
            report = check_foreign_keys(pkg, {"src": src, "tgt": tgt})
        finally:
            if hasattr(engine, "close"):
                engine.close()
        codes = [i.code for i in report.issues]
        assert "fk.null_in_required_fk" in codes
        assert "fk.missing_target" in codes


# ---------------------------------------------------------------------------
# 16. Report accumulation
# ---------------------------------------------------------------------------


class TestReportAccumulation:
    def test_check_foreign_keys_appends_to_existing_report(self, leavenworth_package):
        """An incoming report is mutated and returned (same identity)."""
        engine = IbisEngine()
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            node_rows = _load_rows(csv_dir / "node.csv")
            link_rows[0]["from_node_id"] = 99999
            tables = {
                "link": _scan_rows(engine, link_rows),
                "node": _scan_rows(engine, node_rows),
            }
            seed = ValidationReport(source="pre-existing")
            seed.add(
                severity=Severity.INFO,
                category=Category.SCHEMA,
                code="seed.note",
                message="pre-existing finding",
            )
            returned = check_foreign_keys(leavenworth_package, tables, report=seed)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert returned is seed
        codes = [i.code for i in seed.issues]
        assert "seed.note" in codes  # preserved
        assert any(c.startswith("fk.") for c in codes)


# ---------------------------------------------------------------------------
# 17. Cross-engine parametrize: identical issue counts / codes
# ---------------------------------------------------------------------------


class TestCrossEngineParity:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_cross_engine_identical_counts_and_codes(self, engine_name, leavenworth_package):
        """Same corruption → same FK issue codes across all engines."""
        engine = _engine_for(engine_name)
        csv_dir = leavenworth.csv_dir()
        try:
            link_rows = _load_rows(csv_dir / "link.csv")
            node_rows = _load_rows(csv_dir / "node.csv")
            # Two distinct violations: missing target + null in required.
            link_rows[0]["from_node_id"] = 99999
            link_rows[1]["to_node_id"] = None
            tables = {
                "link": _scan_rows(engine, link_rows),
                "node": _scan_rows(engine, node_rows),
            }
            report = check_foreign_keys(leavenworth_package, tables)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        codes = {i.code for i in report.issues if i.code.startswith("fk.")}
        assert "fk.missing_target" in codes
        assert "fk.null_in_required_fk" in codes
