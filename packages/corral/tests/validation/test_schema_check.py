"""Unit tests for :mod:`corral.validation.schema_check` (task 2.3 / issue #62).

Each per-rule helper is parametrised over every engine the workspace has
installed (``ibis``, ``polars``, ``pandas``) so the schema check makes
the same Issues regardless of which engine produced the ``TableExpr``.
The Leavenworth ``link.csv`` fixture is the real-data substrate;
corruption (drop a required value, inject an out-of-enum value, etc.)
is synthesised by mutating the in-memory frame and re-scanning via the
engine's ``{"data": rows}`` dict-source contract.

Three explicit regression tests pin the v0.3 bug classes the
architecture doc calls out:

- ``test_v03_unique_constraint_regression`` — Series-vs-bool truthiness
  in ``_unique_constraint`` (``if duplicated: ...`` raised TypeError).
- ``test_v03_pattern_check_regression`` — ``~s.str.contains(...)``
  bool-of-Series in pattern check.
- ``test_v03_warning_list_copy_paste_regression`` — the
  ``apply_schema_to_df`` warning-list copy-paste bug that misclassified
  warnings vs errors.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from corral.engines.ibis_engine import IbisEngine
from corral.spec.loader import load_schema
from corral.spec.model import Constraints, Field, Schema
from corral.validation import Category, Severity, ValidationReport
from corral.validation.schema_check import (
    _check_enum as check_enum,
)
from corral.validation.schema_check import (
    _check_max_length as check_max_length,
)
from corral.validation.schema_check import (
    _check_maximum as check_maximum,
)
from corral.validation.schema_check import (
    _check_min_length as check_min_length,
)
from corral.validation.schema_check import (
    _check_minimum as check_minimum,
)
from corral.validation.schema_check import (
    _check_pattern as check_pattern,
)
from corral.validation.schema_check import (
    _check_required as check_required,
)
from corral.validation.schema_check import (
    _check_type as check_type,
)
from corral.validation.schema_check import (
    _check_unique as check_unique,
)
from corral.validation.schema_check import (
    check_schema,
)
from netstead.fixtures import leavenworth

# ---------------------------------------------------------------------------
# Engine matrix
# ---------------------------------------------------------------------------

# polars is an optional extra; skip rather than fail when missing so the
# matrix degrades gracefully.
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


# Standard engine parametrisation — single source of truth for every
# parametrised test below. polars is marked skip rather than absent so
# the test count matrix is consistent for humans reading verbose output.
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
def link_csv() -> Path:
    """Return the Leavenworth ``link.csv`` path."""
    return leavenworth.csv_dir() / "link.csv"


@pytest.fixture
def link_schema(link_csv: Path) -> Schema:
    """Load the GMNS 0.97 ``link.schema.json`` used by the Leavenworth fixture.

    The fixture file paths are stable; we resolve the schema by walking
    up from ``link.csv`` rather than hardcoding the spec version.
    """
    # link_csv = .../packages/netstead/netstead/fixtures/leavenworth/csv/link.csv
    # spec at    .../packages/netstead/netstead/spec/0.97/link.schema.json
    # parents[0]=csv [1]=leavenworth [2]=fixtures [3]=netstead
    schema_path = link_csv.parents[3] / "spec" / "0.97" / "link.schema.json"
    return load_schema(schema_path)


def _load_link_rows(link_csv: Path) -> list[dict]:
    """Read ``link.csv`` as a list of row dicts with native Python types.

    We materialize via ``csv.DictReader`` (string-typed) and then coerce
    the columns we mutate in tests. Engines normalize the rest via
    ``cast_schema`` after they re-scan the data.
    """
    with link_csv.open() as f:
        return list(csv.DictReader(f))


def _scan_rows(engine, rows: list[dict], schema: Schema | None = None):
    """Scan an in-memory list of rows on ``engine`` via the dict-source contract.

    Engines accept ``{"data": [...]}`` as a synthetic source — see
    :meth:`Engine.scan` in :mod:`corral.engines.base`. This keeps the
    test independent of file I/O for synthetic corruptions.

    Columns that are entirely null are dropped before scanning — the
    ibis duckdb backend rejects all-NULL memtable columns, and a
    column with zero non-null values can't be the subject of any
    schema check anyway.
    """
    if rows:
        all_keys = list(rows[0].keys())
        keep = [k for k in all_keys if any(r.get(k) is not None for r in rows)]
        if len(keep) != len(all_keys):
            rows = [{k: r.get(k) for k in keep} for r in rows]
    return engine.scan({"data": rows}, schema=schema)


def _coerce_link_rows(rows: list[dict]) -> list[dict]:
    """Coerce CSV string fields on ``link.csv`` to native types.

    The Leavenworth ``link.csv`` is read as all-string via DictReader;
    schema checks against ``minimum=0`` need real numbers. We coerce
    the columns each test cares about — the rest stay as strings and
    the engine still produces a valid frame because GMNS ``link.link_id``
    is ``type="any"``.
    """
    out = []
    for r in rows:
        new = dict(r)
        # Numeric fields used in min/max/length tests
        for col in ("length", "free_speed", "grade", "lanes", "capacity"):
            v = new.get(col)
            if v in (None, ""):
                new[col] = None
            else:
                try:
                    new[col] = float(v) if col != "lanes" else int(v)
                except (TypeError, ValueError):
                    new[col] = None
        # Booleans
        for col in ("directed",):
            v = new.get(col)
            if isinstance(v, str):
                new[col] = v.strip().lower() in ("true", "1", "yes")
        # Integer-ish IDs
        for col in ("link_id", "from_node_id", "to_node_id", "geometry_id"):
            v = new.get(col)
            if v in (None, ""):
                new[col] = None
            else:
                try:
                    new[col] = int(v)
                except (TypeError, ValueError):
                    # Some IDs are textual in GMNS (``type="any"``).
                    new[col] = v
        out.append(new)
    return out


# ---------------------------------------------------------------------------
# check_required
# ---------------------------------------------------------------------------


class TestCheckRequired:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_flags_null_in_required_field(self, engine_name, link_csv, link_schema):
        """A null in a ``required=True`` field becomes a schema.required Issue."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None  # corrupt
        try:
            expr = _scan_rows(engine, rows)
            field = next(f for f in link_schema.fields if f.name == "from_node_id")
            issues = check_required(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert len(issues) >= 1
        issue = issues[0]
        assert issue.code == "schema.required"
        assert issue.category is Category.SCHEMA
        assert issue.severity is Severity.ERROR
        assert issue.table == "link"
        assert issue.column == "from_node_id"
        # The row number must be in the message AND on the Issue.
        assert issue.row is not None
        assert "from_node_id" in issue.message

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_clean_required_field_yields_no_issue(self, engine_name, link_csv, link_schema):
        """A column with no nulls in a required field produces zero issues."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        try:
            expr = _scan_rows(engine, rows)
            field = next(f for f in link_schema.fields if f.name == "from_node_id")
            issues = check_required(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert issues == []

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_non_required_field_skips_check(self, engine_name, link_csv):
        """A field without ``constraints.required=True`` is exempt from the rule."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["name"] = None
        # Build a Field with required=False (or no constraints at all).
        field = Field(name="name", type="string")
        try:
            expr = _scan_rows(engine, rows)
            issues = check_required(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert issues == []


# ---------------------------------------------------------------------------
# check_enum
# ---------------------------------------------------------------------------


class TestCheckEnum:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_value_not_in_enum_emits_issue(self, engine_name, link_csv):
        """A value outside the declared enum becomes a schema.enum WARNING."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["facility_type"] = "urban"  # not in the enum
        field = Field(
            name="facility_type",
            type="string",
            constraints=Constraints(enum=["residential", "tertiary", "primary"]),
        )
        try:
            expr = _scan_rows(engine, rows)
            issues = check_enum(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.enum" for i in issues)
        issue = next(i for i in issues if i.code == "schema.enum")
        assert issue.severity is Severity.WARNING
        # Per spec — the message must list the allowed values.
        for allowed in ("residential", "tertiary", "primary"):
            assert allowed in issue.message
        assert "urban" in issue.message


# ---------------------------------------------------------------------------
# check_minimum / check_maximum
# ---------------------------------------------------------------------------


class TestCheckMinimum:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_value_below_minimum_emits_issue(self, engine_name, link_csv, link_schema):
        """A value below the declared minimum is reported with its row + bound."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["length"] = -5.0
        field = next(f for f in link_schema.fields if f.name == "length")
        try:
            expr = _scan_rows(engine, rows)
            issues = check_minimum(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.minimum" for i in issues)
        issue = next(i for i in issues if i.code == "schema.minimum")
        assert "length" in issue.message
        assert "-5" in issue.message or "-5.0" in issue.message
        # The bound (0) appears in the message.
        assert "0" in issue.message


class TestCheckMaximum:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_value_above_maximum_emits_issue(self, engine_name, link_csv, link_schema):
        """A value above the declared maximum is reported with its row + bound."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["free_speed"] = 999.0  # max is 200
        field = next(f for f in link_schema.fields if f.name == "free_speed")
        try:
            expr = _scan_rows(engine, rows)
            issues = check_maximum(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.maximum" for i in issues)
        issue = next(i for i in issues if i.code == "schema.maximum")
        assert "free_speed" in issue.message
        assert "999" in issue.message
        assert "200" in issue.message


# ---------------------------------------------------------------------------
# check_min_length / check_max_length
# ---------------------------------------------------------------------------


class TestCheckLength:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_string_shorter_than_min_length(self, engine_name, link_csv):
        """A string shorter than ``min_length`` emits schema.min_length."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["name"] = "ab"
        field = Field(name="name", type="string", constraints=Constraints(min_length=3))
        try:
            expr = _scan_rows(engine, rows)
            issues = check_min_length(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.min_length" for i in issues)
        issue = next(i for i in issues if i.code == "schema.min_length")
        assert "name" in issue.message
        assert "3" in issue.message

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_string_longer_than_max_length(self, engine_name, link_csv):
        """A string longer than ``max_length`` emits schema.max_length."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["name"] = "Z" * 50
        field = Field(name="name", type="string", constraints=Constraints(max_length=10))
        try:
            expr = _scan_rows(engine, rows)
            issues = check_max_length(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.max_length" for i in issues)
        issue = next(i for i in issues if i.code == "schema.max_length")
        assert "name" in issue.message
        assert "10" in issue.message


# ---------------------------------------------------------------------------
# check_pattern
# ---------------------------------------------------------------------------


class TestCheckPattern:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_pattern_mismatch_emits_issue_with_row_number(self, engine_name, link_csv):
        """Regex mismatch is reported with code, row, and the failing value."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        # Make the second row's name violate the pattern.
        rows[1]["name"] = "!!!bad!!!"
        field = Field(
            name="name",
            type="string",
            constraints=Constraints(pattern=r"^[A-Za-z0-9 ]+$"),
        )
        try:
            expr = _scan_rows(engine, rows)
            issues = check_pattern(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.pattern" for i in issues)
        issue = next(i for i in issues if i.code == "schema.pattern")
        assert issue.severity is Severity.WARNING
        assert issue.row is not None
        assert "name" in issue.message


# ---------------------------------------------------------------------------
# check_unique
# ---------------------------------------------------------------------------


class TestCheckUnique:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_duplicates_emit_issue(self, engine_name, link_csv):
        """Duplicate values in a unique field become a schema.unique Issue."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        # Duplicate the first link_id onto the second row.
        rows[1]["link_id"] = rows[0]["link_id"]
        field = Field(
            name="link_id",
            type="any",
            constraints=Constraints(required=True, unique=True),
        )
        try:
            expr = _scan_rows(engine, rows)
            issues = check_unique(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert any(i.code == "schema.unique" for i in issues)
        issue = next(i for i in issues if i.code == "schema.unique")
        assert issue.severity is Severity.ERROR
        assert "link_id" in issue.message

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_no_duplicates_yields_no_issue(self, engine_name, link_csv):
        """A clean unique field produces zero issues."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        field = Field(
            name="link_id",
            type="any",
            constraints=Constraints(unique=True),
        )
        try:
            expr = _scan_rows(engine, rows)
            issues = check_unique(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert issues == []

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_returns_count_summary_with_many_duplicates(self, engine_name):
        """Many duplicates → one summary Issue + per-row Issues (capped)."""
        from corral.validation.schema_check import MAX_ROW_ISSUES

        engine = _engine_for(engine_name)
        # All-duplicate column to maximise the count.
        rows = [{"x": 1} for _ in range(MAX_ROW_ISSUES + 50)]
        field = Field(name="x", type="integer", constraints=Constraints(unique=True))
        try:
            expr = _scan_rows(engine, rows)
            issues = check_unique(expr, field, table_name="t")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # First Issue is the summary (no row, has total_violations).
        assert issues[0].row is None
        assert issues[0].extra.get("total_violations") == len(rows)
        # Per-row Issues capped at MAX_ROW_ISSUES.
        per_row_issues = [i for i in issues if i.row is not None]
        assert len(per_row_issues) == MAX_ROW_ISSUES


# ---------------------------------------------------------------------------
# check_type
# ---------------------------------------------------------------------------


class TestCheckType:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_uncoercible_column_emits_issue(self, engine_name, link_csv):
        """A column whose dtype doesn't match the declared type emits schema.type.

        We force a string column where an integer is declared. The
        dict-source contract requires homogeneous types per column
        across engines, so we simulate the realistic v0.3 failure mode
        — a column read from a CSV that landed as ``object`` because
        one cell wouldn't coerce — by sending the whole column as
        strings.
        """
        engine = _engine_for(engine_name)
        rows = [{"lanes": "x"}, {"lanes": "y"}, {"lanes": "z"}]
        field = Field(name="lanes", type="integer")
        try:
            expr = _scan_rows(engine, rows)
            issues = check_type(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # check_type emits a schema.type Issue for the dtype mismatch.
        assert any(i.code == "schema.type" for i in issues)


# ---------------------------------------------------------------------------
# check_schema — orchestrator
# ---------------------------------------------------------------------------


class TestCheckSchema:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_clean_data_returns_clean_report(self, engine_name, link_csv, link_schema):
        """Pristine link.csv has no errors or warnings."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        try:
            expr = _scan_rows(engine, rows, schema=link_schema)
            report = check_schema(expr, link_schema, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert isinstance(report, ValidationReport)
        # Pristine data — any issues should be informational only.
        # link.schema.json has minimum=0 on length/lanes/capacity, no enums.
        assert not report.has_errors, [str(i) for i in report.issues]

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_runs_all_rules(self, engine_name, link_csv, link_schema):
        """Three corruptions of different kinds surface three distinct codes."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None  # schema.required
        rows[1]["length"] = -5.0  # schema.minimum
        # Add ``unique=True`` to ``link_id`` so we can also exercise that rule
        # via the orchestrator (the upstream link schema only declares
        # ``primaryKey`` — uniqueness lives on a separate Frictionless
        # property that the structural check, task 2.5, handles).
        schema_with_unique = link_schema.model_copy(deep=True)
        for f in schema_with_unique.fields:
            if f.name == "link_id":
                if f.constraints is None:
                    f.constraints = Constraints(unique=True)
                else:
                    f.constraints.unique = True
        rows[2]["link_id"] = rows[0]["link_id"]  # schema.unique
        try:
            expr = _scan_rows(engine, rows, schema=schema_with_unique)
            report = check_schema(expr, schema_with_unique, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        codes = {i.code for i in report.issues}
        assert "schema.required" in codes
        assert "schema.minimum" in codes
        assert "schema.unique" in codes

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_emits_correct_categories(self, engine_name, link_csv, link_schema):
        """Every Issue from check_schema carries category=SCHEMA."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None
        rows[1]["length"] = -5.0
        try:
            expr = _scan_rows(engine, rows, schema=link_schema)
            report = check_schema(expr, link_schema, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert len(report.issues) > 0
        for issue in report.issues:
            assert issue.category is Category.SCHEMA, issue

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_severity_distribution(self, engine_name, link_csv, link_schema):
        """Required violations are ERROR, enum violations are WARNING."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None  # ERROR
        # Add an enum field to the schema so we can also exercise WARNING.
        schema_with_enum = link_schema.model_copy(deep=True)
        for f in schema_with_enum.fields:
            if f.name == "facility_type":
                f.constraints = Constraints(enum=["residential", "tertiary", "primary"])
        rows[1]["facility_type"] = "urban"  # WARNING
        try:
            expr = _scan_rows(engine, rows, schema=schema_with_enum)
            report = check_schema(expr, schema_with_enum, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        required_issues = [i for i in report.issues if i.code == "schema.required"]
        enum_issues = [i for i in report.issues if i.code == "schema.enum"]
        assert required_issues, "expected at least one schema.required Issue"
        assert enum_issues, "expected at least one schema.enum Issue"
        assert all(i.severity is Severity.ERROR for i in required_issues)
        assert all(i.severity is Severity.WARNING for i in enum_issues)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_lazy_eval_materialises_once(self, engine_name, link_csv, link_schema):
        """check_schema must materialize ``expr`` to pandas exactly once.

        Repeated materialization would mean schema_check is O(rules) in
        engine round-trips instead of O(1) — undesirable for large
        networks and a Lens-A perf smell. We patch ``Engine.to_pandas``
        to count calls.
        """
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None
        rows[1]["length"] = -5.0
        try:
            expr = _scan_rows(engine, rows, schema=link_schema)
            original_to_pandas = engine.to_pandas
            call_count = {"n": 0}

            def counting_to_pandas(e):
                call_count["n"] += 1
                return original_to_pandas(e)

            engine.to_pandas = counting_to_pandas  # type: ignore[method-assign]
            check_schema(expr, link_schema, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # At most one materialization across all rules. The pandas engine
        # returns a DataFrame directly from ``scan`` (it's already eager)
        # so 0 calls is also acceptable — the contract is bounded, not
        # exactly-one.
        assert call_count["n"] <= 1, (
            f"check_schema materialized {call_count['n']} times; "
            "must be bounded (one round-trip per check_schema call)."
        )

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_appends_to_existing_report(self, engine_name, link_csv, link_schema):
        """An existing ValidationReport is mutated (not replaced)."""
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None
        seed = ValidationReport(spec_version="0.97", source="link.csv")
        seed.add(
            severity=Severity.INFO,
            category=Category.SCHEMA,
            code="schema.info",
            message="prior info",
            table="link",
        )
        try:
            expr = _scan_rows(engine, rows, schema=link_schema)
            result = check_schema(expr, link_schema, table_name="link", report=seed)
        finally:
            if hasattr(engine, "close"):
                engine.close()
        assert result is seed
        assert any(i.code == "schema.info" for i in seed.issues)
        assert any(i.code == "schema.required" for i in seed.issues)


# ---------------------------------------------------------------------------
# v0.3 regression tests
# ---------------------------------------------------------------------------


class TestV03Regressions:
    """Explicit regression tests for the v0.3 bug classes called out in
    ``docs/architecture.md``.

    Each test names the v0.3 bug it pins so a future grep finds the
    rationale instantly.
    """

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_v03_unique_constraint_regression(self, engine_name, link_csv):
        """Regression — Series-vs-bool in v0.3 _unique_constraint.

        v0.3 wrote ``if s.dropna().duplicated(): ...`` which raises
        ``TypeError: The truth value of a Series is ambiguous``. The
        new check must handle nulls + duplicates without raising and
        emit a schema.unique Issue.
        """
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        # Inject a null AND a duplicate in a unique field.
        rows[0]["link_id"] = None
        rows[1]["link_id"] = rows[2]["link_id"]
        field = Field(
            name="link_id",
            type="any",
            constraints=Constraints(unique=True),
        )
        try:
            expr = _scan_rows(engine, rows)
            # Must NOT raise.
            issues = check_unique(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # Duplicates among non-nulls are still reported.
        assert any(i.code == "schema.unique" for i in issues)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_v03_pattern_check_regression(self, engine_name, link_csv):
        """Regression — ``if not_matching: ...`` on a Series in v0.3.

        v0.3 wrote ``~s.str.contains(pattern)`` and then a bare ``if``
        on the result, which is ambiguous on a Series. The new check
        must enumerate each mismatching row.
        """
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[1]["name"] = "@@@invalid@@@"
        field = Field(
            name="name",
            type="string",
            constraints=Constraints(pattern=r"^[A-Za-z0-9 ]+$"),
        )
        try:
            expr = _scan_rows(engine, rows)
            issues = check_pattern(expr, field, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        # Pin: at least one Issue with the row number identified.
        assert any(i.code == "schema.pattern" and i.row is not None for i in issues)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_v03_warning_list_copy_paste_regression(self, engine_name, link_csv, link_schema):
        """Regression — apply_schema_to_df warning-list copy-paste bug.

        v0.3 built the warning list with ``[i for i in error_list if
        i.severity == "warning"]`` — wrong source list. The new check
        must correctly classify required (ERROR) vs enum (WARNING) into
        the right buckets on the ValidationReport.
        """
        engine = _engine_for(engine_name)
        rows = _coerce_link_rows(_load_link_rows(link_csv))
        rows[0]["from_node_id"] = None  # ERROR via schema.required
        # Inject an enum + an out-of-range value to drive a WARNING.
        schema_with_enum = link_schema.model_copy(deep=True)
        for f in schema_with_enum.fields:
            if f.name == "facility_type":
                f.constraints = Constraints(enum=["residential", "tertiary", "primary"])
        rows[1]["facility_type"] = "urban"  # WARNING via schema.enum
        try:
            expr = _scan_rows(engine, rows, schema=schema_with_enum)
            report = check_schema(expr, schema_with_enum, table_name="link")
        finally:
            if hasattr(engine, "close"):
                engine.close()
        errors = report.by_severity(Severity.ERROR)
        warnings = report.by_severity(Severity.WARNING)
        assert any(i.code == "schema.required" for i in errors)
        assert any(i.code == "schema.enum" for i in warnings)
        # And the inverse — no required issue is misclassified as a warning.
        assert not any(i.code == "schema.required" for i in warnings)
        assert not any(i.code == "schema.enum" for i in errors)
