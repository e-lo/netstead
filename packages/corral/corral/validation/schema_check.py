"""Schema validator — per-field Frictionless constraint checks (task 2.3 / issue #62).

This module is the **schema** half of the corral validation layer.
It takes a table expression (from any engine — ibis, polars, pandas)
plus a parsed Frictionless :class:`~corral.spec.model.Schema` and
emits :class:`~corral.reports.Issue` records — one per field-level
violation — into a :class:`~corral.reports.ValidationReport`.

Cross-engine strategy — ibis-first (architecture §6.1)
------------------------------------------------------

Every rule pushes its violation count down as an ibis predicate
(``expr[col].is_null()``, ``~expr[col].isin(allowed)``,
``expr[col] < bound``, …) — the count round-trips as a single SQL
aggregate, not a pandas materialization. At Bay-Area scale (millions
of rows) this turns "count nulls in a required column" from a
gigabyte materialization into a millisecond SQL ``COUNT(*) WHERE col
IS NULL``.

Only the per-rule sample used to populate row-context Issues is
materialized — capped at
:data:`corral.validation._ibis.MAX_ROW_ISSUES` rows — and goes
through pyarrow (``.to_pyarrow().to_pylist()``), never pandas.
Pandas-backed and polars-backed sources route through
:func:`corral.validation._ibis.to_ibis`, which wraps them once per
orchestrator call as an ``ibis.memtable`` against the default
duckdb backend.

Per-rule code mapping (the dotted codes downstream tools grep for)
------------------------------------------------------------------

================  ================================================
Rule              Issue code (downstream-stable)
================  ================================================
required          ``schema.required``
type              ``schema.type``
enum              ``schema.enum``
minimum           ``schema.minimum``
maximum           ``schema.maximum``
min_length        ``schema.min_length``
max_length        ``schema.max_length``
pattern           ``schema.pattern``
unique            ``schema.unique``
================  ================================================

Severity defaults
-----------------

``required`` / ``type`` / ``unique`` → :class:`Severity.ERROR`.
``enum`` / ``pattern`` / ``minimum`` / ``maximum`` / ``min_length`` /
``max_length`` → :class:`Severity.WARNING`. If a field is both
``required=True`` AND has a min/max/length bound, the bound check
elevates to :class:`Severity.ERROR` (the field's contract is strict
enough that an out-of-range value is treated as broken).

Module shape
------------

:func:`check_schema` is the public orchestrator and the only symbol
in ``__all__``. Per-rule helpers (``_check_required``, ``_check_type``,
…) are decomposed for legibility and pinned by direct tests, but they
are *internal* — callers should not import them. See
``validation/__init__.py`` for the validator-shape convention.

v0.3 bug-class regressions
--------------------------

Three explicit regression tests live in
``packages/corral/tests/validation/test_schema_check.py`` and pin
the three v0.3 bug classes the architecture doc calls out:
``_unique_constraint`` (Series-vs-bool), ``apply_schema_to_df``
warning-list copy/paste, and the ``~s.str.contains(...)`` bool-of-Series
bug. The ibis-first refactor makes each of those structurally
impossible — predicates are ibis expressions (no Python truthiness),
the report classifies issues by their own ``severity`` (not by
re-filtering a single mixed list), and pattern checks enumerate
failing rows via a filtered sample.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import ibis

from corral.reports import Category, Issue, Severity, ValidationReport

from ._ibis import (
    MAX_ROW_ISSUES,
    count,
    emit_summary,
    row_of,
    sample,
    to_ibis,
    with_row_index,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import ibis.expr.types as ir

    from corral.engines.base import TableExpr
    from corral.spec.model import Field, Schema

__all__ = ["check_schema"]


# Frictionless type → ibis dtype family. A column whose ibis dtype is
# not in the declared family fires ``schema.type``. Frictionless types
# not listed here (``date``, ``time``, ``object``, …) are skipped —
# the cross-engine type-map parity test gates the keyset.
#
# We compare by the ibis dtype's ``str()`` representation. Ibis exposes
# canonical lowercase names (``int64``, ``float64``, ``string``,
# ``boolean``); we accept the family substrings since duckdb may
# surface narrower variants (``int32``) for memtables of typed pandas.
_FRICTIONLESS_TO_IBIS_FAMILY: dict[str, tuple[str, ...]] = {
    "integer": ("int",),
    "number": ("int", "float", "decimal"),
    "boolean": ("bool",),
    "string": ("string",),
    # ``any`` is the GMNS escape hatch — every dtype is acceptable.
    "any": (),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_required(field: Field) -> bool:
    """Return ``True`` when ``field.constraints.required`` is ``True``."""
    return bool(field.constraints and field.constraints.required)


def _bound_severity(field: Field) -> Severity:
    """Pick the severity for a bound-style rule (min/max/length).

    Required fields elevate bound violations to ERROR. A required
    field's contract is already strict; an out-of-range value isn't a
    "maybe-legal-in-a-future-spec" gray area, it's a fully-broken row.
    """
    return Severity.ERROR if _is_required(field) else Severity.WARNING


def _has_column(table: ir.Table, name: str) -> bool:
    """Return ``True`` iff ``name`` is a column on ``table``.

    Missing columns are a *structural* problem, not a *schema* problem
    (task 2.5 owns that). Schema rules silently skip absent fields so
    a partial dataset doesn't produce dozens of misleading findings.
    """
    return name in table.columns


def _schema_summary(
    *,
    severity: Severity,
    code: str,
    table_name: str,
    field: Field,
    total: int,
    summary_message: str,
    extra: dict[str, Any] | None = None,
) -> Issue:
    """Wrap :func:`corral.validation._ibis.emit_summary` with SCHEMA defaults."""
    return emit_summary(
        severity=severity,
        category=Category.SCHEMA,
        code=code,
        table=table_name,
        column=field.name,
        total=total,
        message=summary_message,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Per-rule helpers — each takes an ibis Table, pushes a count, samples
# only when needed. Underscore prefix marks them as internal: tests
# import them directly but they are not in ``__all__``.
# ---------------------------------------------------------------------------


def _check_required(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag null values in a required field."""
    if not _is_required(field):
        return []
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    bad = table.filter(table[field.name].isnull())
    total = count(bad)
    if total == 0:
        return []
    issues: list[Issue] = []
    for sample_row in sample(bad, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        issues.append(
            Issue(
                severity=Severity.ERROR,
                category=Category.SCHEMA,
                code="schema.required",
                message=f"{table_name}.{field.name} row {row_idx}: required field is null",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Set {table_name}.{field.name} row {row_idx} to a non-null value.",
            )
        )
    if total > MAX_ROW_ISSUES:
        issues.append(
            _schema_summary(
                severity=Severity.ERROR,
                code="schema.required",
                table_name=table_name,
                field=field,
                total=total,
                summary_message=(
                    f"{table_name}.{field.name}: {total} required values are null (showing first {MAX_ROW_ISSUES})"
                ),
            )
        )
    return issues


def _check_type(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag a column whose ibis dtype doesn't match ``field.type``."""
    if not field.type:
        return []
    allowed = _FRICTIONLESS_TO_IBIS_FAMILY.get(field.type)
    if allowed is None or not allowed:
        # Unknown type OR "any" — nothing to check at the dtype level.
        return []
    table = to_ibis(expr)
    if not _has_column(table, field.name):
        return []
    actual = str(table[field.name].type()).lower()
    if any(fam in actual for fam in allowed):
        return []
    return [
        Issue(
            severity=Severity.ERROR,
            category=Category.SCHEMA,
            code="schema.type",
            message=(
                f"{table_name}.{field.name}: column dtype {actual!r} does not match "
                f"declared Frictionless type {field.type!r}"
            ),
            table=table_name,
            column=field.name,
            fix_hint=f"Coerce {table_name}.{field.name} to {field.type!r}.",
            extra={"actual_dtype": actual, "expected_type": field.type},
        )
    ]


def _check_enum(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag values not in ``constraints.enum`` (nulls skipped)."""
    if not field.constraints or not field.constraints.enum:
        return []
    allowed = list(field.constraints.enum)
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    col = table[field.name]
    bad = table.filter(col.notnull() & ~col.isin(allowed))
    total = count(bad)
    if total == 0:
        return []
    issues: list[Issue] = []
    allowed_str = ", ".join(repr(v) for v in allowed)
    for sample_row in sample(bad, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        value = sample_row.get(field.name)
        issues.append(
            Issue(
                severity=Severity.WARNING,
                category=Category.SCHEMA,
                code="schema.enum",
                message=f"{table_name}.{field.name} row {row_idx}: value {value!r} not in enum [{allowed_str}]",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Use one of: {', '.join(str(v) for v in allowed)}.",
            )
        )
    if total > MAX_ROW_ISSUES:
        issues.append(
            _schema_summary(
                severity=Severity.WARNING,
                code="schema.enum",
                table_name=table_name,
                field=field,
                total=total,
                summary_message=(
                    f"{table_name}.{field.name}: {total} values outside enum (showing first {MAX_ROW_ISSUES})"
                ),
                extra={"allowed": allowed},
            )
        )
    return issues


def _check_numeric_bound(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
    bound: Any,
    code: str,
    direction: str,
) -> list[Issue]:
    """Shared body for the minimum / maximum checks."""
    if bound is None:
        return []
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    col = table[field.name]
    if direction == "below":
        bad = table.filter(col.notnull() & (col < bound))
        rel = "below minimum"
        fix_word = "above"
    else:
        bad = table.filter(col.notnull() & (col > bound))
        rel = "above maximum"
        fix_word = "below"
    total = count(bad)
    if total == 0:
        return []
    severity = _bound_severity(field)
    issues: list[Issue] = []
    for sample_row in sample(bad, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        value = sample_row.get(field.name)
        issues.append(
            Issue(
                severity=severity,
                category=Category.SCHEMA,
                code=code,
                message=f"{table_name}.{field.name} row {row_idx}: value {value} {rel} {bound}",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Set {table_name}.{field.name} to a value {fix_word} {bound}.",
            )
        )
    if total > MAX_ROW_ISSUES:
        issues.append(
            _schema_summary(
                severity=severity,
                code=code,
                table_name=table_name,
                field=field,
                total=total,
                summary_message=(
                    f"{table_name}.{field.name}: {total} values {rel} {bound} (showing first {MAX_ROW_ISSUES})"
                ),
                extra={"bound": bound},
            )
        )
    return issues


def _check_minimum(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag values strictly below ``field.constraints.minimum``."""
    if not field.constraints:
        return []
    return _check_numeric_bound(
        expr,
        field,
        table_name=table_name,
        bound=field.constraints.minimum,
        code="schema.minimum",
        direction="below",
    )


def _check_maximum(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag values strictly above ``field.constraints.maximum``."""
    if not field.constraints:
        return []
    return _check_numeric_bound(
        expr,
        field,
        table_name=table_name,
        bound=field.constraints.maximum,
        code="schema.maximum",
        direction="above",
    )


def _check_length_bound(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
    bound: int | None,
    code: str,
    direction: str,
) -> list[Issue]:
    """Shared body for the min_length / max_length checks."""
    if bound is None:
        return []
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    col = table[field.name]
    # Cast to string before measuring length so int / mixed columns
    # don't blow up on the .length() call. Nulls are dropped first.
    # The bound goes through ``ibis.literal`` so pyright can model
    # the comparison as ``IntegerValue`` vs ``IntegerValue``.
    length = col.cast("string").length()
    bound_lit = ibis.literal(bound)
    if direction == "below":
        bad = table.filter(col.notnull() & (length < bound_lit))
        rel = f"shorter than min_length {bound}"
        fix_word = "at least"
    else:
        bad = table.filter(col.notnull() & (length > bound_lit))
        rel = f"longer than max_length {bound}"
        fix_word = "no more than"
    total = count(bad)
    if total == 0:
        return []
    severity = _bound_severity(field)
    issues: list[Issue] = []
    for sample_row in sample(bad, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        value = sample_row.get(field.name)
        char_len = len(str(value)) if value is not None else 0
        issues.append(
            Issue(
                severity=severity,
                category=Category.SCHEMA,
                code=code,
                message=f"{table_name}.{field.name} row {row_idx}: value {value!r} ({char_len} chars) {rel}",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Use a value {fix_word} {bound} characters in {table_name}.{field.name}.",
            )
        )
    if total > MAX_ROW_ISSUES:
        issues.append(
            _schema_summary(
                severity=severity,
                code=code,
                table_name=table_name,
                field=field,
                total=total,
                summary_message=(f"{table_name}.{field.name}: {total} values {rel} (showing first {MAX_ROW_ISSUES})"),
                extra={"bound": bound},
            )
        )
    return issues


def _check_min_length(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag strings shorter than ``field.constraints.min_length``."""
    if not field.constraints:
        return []
    return _check_length_bound(
        expr,
        field,
        table_name=table_name,
        bound=field.constraints.min_length,
        code="schema.min_length",
        direction="below",
    )


def _check_max_length(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag strings longer than ``field.constraints.max_length``."""
    if not field.constraints:
        return []
    return _check_length_bound(
        expr,
        field,
        table_name=table_name,
        bound=field.constraints.max_length,
        code="schema.max_length",
        direction="above",
    )


def _check_pattern(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag strings that don't match ``field.constraints.pattern``.

    Uses ``re_search`` with explicit ``^``/``$`` anchors so the regex
    must match the entire value (Frictionless full-match semantics).
    Null values are skipped.

    This is the regression site for the v0.3
    ``~s.str.contains(...)`` bool-of-Series bug — the predicate lives
    in ibis SQL, so Python truthiness on a Series can't recur.
    """
    if not field.constraints or not field.constraints.pattern:
        return []
    pattern = field.constraints.pattern
    # Validate the regex up front; a bad pattern is a SPEC bug, not a
    # data bug, and we emit the same Issue shape so renderers don't
    # special-case it.
    import re

    try:
        re.compile(pattern)
    except re.error as e:
        return [
            Issue(
                severity=Severity.ERROR,
                category=Category.SCHEMA,
                code="schema.pattern",
                message=f"{table_name}.{field.name}: invalid regex {pattern!r} in schema ({e})",
                table=table_name,
                column=field.name,
                fix_hint=f"Fix the regex declared on {table_name}.{field.name}.",
            )
        ]
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    col = table[field.name].cast("string")
    # Anchor for full-match semantics — Frictionless ``pattern`` is a
    # full-string regex, not a search.
    full_pattern = pattern if pattern.startswith("^") else f"^{pattern}"
    if not full_pattern.endswith("$"):
        full_pattern = f"{full_pattern}$"
    bad = table.filter(table[field.name].notnull() & ~col.re_search(full_pattern))
    total = count(bad)
    if total == 0:
        return []
    issues: list[Issue] = []
    for sample_row in sample(bad, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        value = sample_row.get(field.name)
        issues.append(
            Issue(
                severity=Severity.WARNING,
                category=Category.SCHEMA,
                code="schema.pattern",
                message=f"{table_name}.{field.name} row {row_idx}: value {value!r} does not match pattern {pattern!r}",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Make {table_name}.{field.name} match the regex {pattern!r}.",
            )
        )
    if total > MAX_ROW_ISSUES:
        issues.append(
            _schema_summary(
                severity=Severity.WARNING,
                code="schema.pattern",
                table_name=table_name,
                field=field,
                total=total,
                summary_message=(
                    f"{table_name}.{field.name}: {total} values fail pattern "
                    f"{pattern!r} (showing first {MAX_ROW_ISSUES})"
                ),
                extra={"pattern": pattern},
            )
        )
    return issues


def _check_unique(
    expr: TableExpr,
    field: Field,
    *,
    table_name: str,
) -> list[Issue]:
    """Flag duplicate values in a field declared ``unique=True``.

    Nulls are excluded from the uniqueness check (Frictionless
    convention — duplicates of null don't violate uniqueness; the
    ``required`` constraint handles nulls). Built as
    ``group_by(col).having(count > 1)`` so duplicate counting happens
    in SQL.

    This is the regression site for the v0.3 ``_unique_constraint``
    bug (``if s.dropna().duplicated(): ...`` on a Series). The ibis
    predicate can't trigger Series-bool ambiguity.
    """
    if not field.constraints or not field.constraints.unique:
        return []
    table = with_row_index(to_ibis(expr))
    if not _has_column(table, field.name):
        return []
    col_name = field.name
    col = table[col_name]
    # Group non-null values; the values appearing in >1 row are the
    # duplicate set. Then anti-join with the original table to locate
    # the offending rows + row indices.
    non_null = table.filter(col.notnull())
    dup_values = non_null.group_by(col_name).aggregate(_n=non_null.count()).filter(ibis._["_n"] > 1)
    duplicate_count = count(table.filter(col.isin(dup_values[col_name])))
    if duplicate_count == 0:
        return []
    issues: list[Issue] = [
        Issue(
            severity=Severity.ERROR,
            category=Category.SCHEMA,
            code="schema.unique",
            message=f"{table_name}.{field.name}: {duplicate_count} duplicate values",
            table=table_name,
            column=field.name,
            fix_hint=f"Remove or de-duplicate values in {table_name}.{field.name}.",
            extra={"total_violations": duplicate_count, "sample_shown": MAX_ROW_ISSUES},
        )
    ]
    dup_rows = table.filter(col.isin(dup_values[col_name]))
    for sample_row in sample(dup_rows, limit=MAX_ROW_ISSUES):
        row_idx = row_of(sample_row)
        value = sample_row.get(col_name)
        issues.append(
            Issue(
                severity=Severity.ERROR,
                category=Category.SCHEMA,
                code="schema.unique",
                message=f"{table_name}.{field.name} row {row_idx}: duplicate value {value!r}",
                table=table_name,
                column=field.name,
                row=row_idx,
                fix_hint=f"Make {table_name}.{field.name} unique at row {row_idx}.",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# Deterministic per-rule call order. Required runs first so a null value
# isn't double-reported by every downstream rule; type runs second so a
# column-wide dtype mismatch surfaces before the per-row enum/min/max
# noise it would generate.
_RULE_ORDER: tuple = (
    _check_required,
    _check_type,
    _check_enum,
    _check_minimum,
    _check_maximum,
    _check_min_length,
    _check_max_length,
    _check_pattern,
    _check_unique,
)


def check_schema(
    expr: TableExpr,
    schema: Schema,
    *,
    table_name: str,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Validate ``expr`` against ``schema`` and populate a ValidationReport.

    Runs every per-field check declared in the Frictionless schema
    (required / type / enum / minimum / maximum / min_length /
    max_length / pattern / unique) and appends one or more
    :class:`Issue` records per violation to ``report``.

    The expression is **normalized to ibis once** at the top of the
    call — pandas / polars sources are wrapped as ``ibis.memtable``
    via :func:`corral.validation._ibis.to_ibis`. Each rule then
    pushes its violation count down as an ibis predicate (a SQL
    aggregate, not a pandas materialization) and samples only the
    rows it needs for row-context Issues. At Bay-Area scale this is
    the difference between a millisecond ``COUNT(*)`` and a gigabyte
    materialization.

    Args:
        expr: Engine-native lazy table expression to validate.
        schema: Parsed Frictionless :class:`~corral.spec.model.Schema`.
        table_name: Logical name of the table (populates ``Issue.table``).
        report: Optional existing :class:`ValidationReport` to append
            into. When ``None`` (the default), a fresh report is
            created. The same object is returned either way.

    Returns:
        The :class:`ValidationReport` — the same instance as ``report``
        if one was passed.

    Examples:
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> from corral.spec.model import Schema, Field, Constraints
        >>> e = IbisEngine()
        >>> expr = e.from_records([{"id": 1}, {"id": 2}])
        >>> s = Schema(fields=[Field(name="id", type="any",
        ...     constraints=Constraints(required=True, unique=True))])
        >>> report = check_schema(expr, s, table_name="t")
        >>> report.is_clean
        True
    """
    if report is None:
        report = ValidationReport()
    # Normalize to ibis once so every rule shares the same wrapped
    # table; the per-rule helpers also call ``to_ibis`` but it's a
    # pass-through on an ibis Table so we don't double-wrap.
    table = to_ibis(expr)
    for field in schema.fields:
        for rule in _RULE_ORDER:
            for issue in rule(table, field, table_name=table_name):
                report.add_issue(issue)
    return report
