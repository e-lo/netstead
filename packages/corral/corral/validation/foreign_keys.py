"""Foreign-key validator — cross-table referential integrity (task 2.4 / issue #63).

This module is the **referential-integrity** half of the corral
validation layer. It walks a :class:`~corral.spec.model.DataPackage`,
follows every declared :class:`~corral.spec.model.ForeignKey`, and
verifies that every non-null source-column value appears in the target
column. Cross-table FKs (``link.from_node_id -> node.node_id``) and
same-table self-references (``node.parent_node_id -> node.node_id``) are
both supported, and so are composite FKs (``[a, b] -> [x, y]``).

Each violation becomes one :class:`~corral.reports.Issue` with
``category=Category.FOREIGN_KEY`` and a stable dotted code
(:data:`fk.missing_target`, :data:`fk.null_in_required_fk`,
:data:`fk.unverifiable`, :data:`fk.target_field_missing`). The
:class:`Issue.extra` dict carries ``target_table`` / ``target_field`` /
``value`` so the HTML renderer (task 2.2) can drive its expand-row panel
without re-parsing the message string.

Cross-engine strategy — ibis-first (architecture §6.1)
------------------------------------------------------

Source + target tables are normalized to ibis once via
:func:`corral.validation._ibis.to_ibis`. The missing-target check
is a single ``LEFT JOIN ... WHERE target IS NULL`` pushed to duckdb —
the violation count comes back as a scalar SQL aggregate and only the
sampled rows (capped at :data:`MAX_ROW_ISSUES`) are materialized via
pyarrow for Issue enumeration. At Bay-Area scale this turns a
hundred-million-row FK check from a multi-gigabyte materialization
into a sub-second SQL join.

Per-FK code mapping (downstream-stable, dotted)
-----------------------------------------------

============================  =====================================================
Code                          When emitted
============================  =====================================================
``fk.missing_target``         A non-null source row's value(s) don't exist in the
                              target column(s). Severity: ERROR.
``fk.null_in_required_fk``    The source FK column is null AND the schema marks
                              that source field ``required=True``. Severity: ERROR.
``fk.unverifiable``           Target table isn't in the ``tables`` mapping and the source
                              column holds at least one non-null value — we
                              can't check the FK. Severity: WARNING (or ERROR
                              under ``strict=True``).
``fk.target_field_missing``   Target table is present but doesn't have the
                              target field — the spec references a missing
                              column. Severity: ERROR (spec bug, not data bug).
============================  =====================================================

v0.3 bug-class regression
-------------------------

The v0.3 ``foreign_keys.py`` raised ``TypeError`` on
``if s.isna(): ...`` when the source FK column contained nulls (the
Series-vs-bool truthiness trap). The ibis-first refactor makes the
recurrence structurally impossible — null detection is an ibis
predicate (``col.isnull()``), not a Python boolean coercion. The
regression test still exercises the same corruption shape and asserts
both issue codes fire.

DirtyTracker note (task 2.6)
----------------------------

This validator does NOT stamp ``sync_state`` hashes. The DirtyTracker
integration lives in :meth:`corral.dataset.Package.validate`, which
walks the spec after a clean FK pass and calls
:meth:`DirtyTracker.stamp_fk_from_exprs` for each FK whose validator
produced no issues. This module deliberately doesn't depend on the
hash infrastructure so the dependency points one way
(dataset → validation).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from corral.reports import Category, Issue, Severity, ValidationReport

from ._ibis import MAX_ROW_ISSUES, ROW_COL, count, sample, to_ibis, with_row_index

if TYPE_CHECKING:  # pragma: no cover - typing only
    import ibis.expr.types as ir

    from corral.engines.base import TableExpr
    from corral.spec.model import DataPackage, ForeignKey

__all__ = [
    "check_foreign_key",
    "check_foreign_keys",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _as_field_list(fields: str | list[str]) -> list[str]:
    """Normalize the Frictionless ``fields`` declaration to a list."""
    if isinstance(fields, str):
        return [fields]
    return list(fields)


def _format_field_list(fields: list[str]) -> str:
    """Render a field list for use inside messages."""
    if len(fields) == 1:
        return fields[0]
    return "[" + ", ".join(fields) + "]"


def _format_value(value: Any) -> str:
    """Render a source value (scalar or tuple) for use inside messages."""
    if isinstance(value, tuple):
        return "(" + ", ".join(repr(v) for v in value) + ")"
    return repr(value)


def _source_field_is_required(
    package: DataPackage | None,
    source_table_name: str,
    source_field: str,
) -> bool:
    """Look up whether ``source_field`` is marked ``required=True``."""
    if package is None:
        return False
    for resource in package.resources:
        if resource.name != source_table_name:
            continue
        schema = resource.table_schema
        if schema is None or isinstance(schema, str):
            return False
        for field in schema.fields:
            if field.name != source_field:
                continue
            return bool(field.constraints and field.constraints.required)
        return False
    return False


def _emit_null_in_required_fk_issues(
    src_table: ir.Table,
    source_table_name: str,
    source_fields: list[str],
    *,
    package: DataPackage | None,
) -> list[Issue]:
    """Emit ``fk.null_in_required_fk`` for null source values in required columns.

    Each required field gets its own filter+count+sample pass. The
    counts push to SQL; only the sampled offending rows are
    materialized.
    """
    issues: list[Issue] = []
    for source_field in source_fields:
        if source_field not in src_table.columns:
            continue
        if not _source_field_is_required(package, source_table_name, source_field):
            continue
        bad = src_table.filter(src_table[source_field].isnull())
        total = count(bad)
        if total == 0:
            continue
        sampled = sample(bad, limit=MAX_ROW_ISSUES)
        for sample_row in sampled:
            row_idx = int(sample_row.get(ROW_COL, -1))
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    category=Category.FOREIGN_KEY,
                    code="fk.null_in_required_fk",
                    message=f"{source_table_name}.{source_field} row {row_idx}: required FK column is null",
                    table=source_table_name,
                    column=source_field,
                    row=row_idx,
                    fix_hint="Either populate the FK column or relax the required=True constraint.",
                )
            )
        if total > MAX_ROW_ISSUES:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    category=Category.FOREIGN_KEY,
                    code="fk.null_in_required_fk",
                    message=(
                        f"{source_table_name}.{source_field}: {total} required FK "
                        f"values are null (showing first {MAX_ROW_ISSUES})"
                    ),
                    table=source_table_name,
                    column=source_field,
                    extra={"total_violations": total, "sample_shown": MAX_ROW_ISSUES},
                )
            )
    return issues


def _source_has_fk_values(src: Any, source_fields: list[str]) -> bool:
    """Return whether any row holds a non-null value in a source FK column.

    Absent columns count as empty. Uses one pushed-down ``COUNT`` over
    the present columns rather than materializing the table.
    """
    present = [f for f in source_fields if f in src.columns]
    if not present:
        return False
    has_value = src[present[0]].notnull()
    for name in present[1:]:
        has_value = has_value | src[name].notnull()
    return count(src.filter(has_value)) > 0


def _missing_target_predicate(
    src: ir.Table,
    tgt: ir.Table,
    source_fields: list[str],
    target_fields: list[str],
) -> ir.Table:
    """Build the ``source LEFT JOIN target WHERE target IS NULL`` expression.

    Filters out source rows where ANY source key column is null first
    (those are either legitimately null-FK or covered by the null-in-
    required check). The join predicate ANDs each ``src[f_i] ==
    tgt[t_i]`` so composite FKs work uniformly with single-column ones.
    """
    # Drop source rows where ANY key field is null — they're either
    # legitimately null-FK (covered separately) or can't participate
    # in a referential check.
    non_null_mask = src[source_fields[0]].notnull()
    for sf in source_fields[1:]:
        non_null_mask = non_null_mask & src[sf].notnull()
    candidates = src.filter(non_null_mask)

    # Project the target to JUST the join columns + sentinel, so the
    # null-check on the right side stays unambiguous.
    tgt_keys = tgt.select(*target_fields).distinct()
    # Rename target columns to a guaranteed-unique suffix so the join
    # doesn't collide on shared field names (the same-table FK case).
    tgt_renamed = tgt_keys.rename({f"__dg_tgt_{tf}__": tf for tf in target_fields})

    join_predicate = candidates[source_fields[0]] == tgt_renamed[f"__dg_tgt_{target_fields[0]}__"]
    for sf, tf in zip(source_fields[1:], target_fields[1:], strict=True):
        join_predicate = join_predicate & (candidates[sf] == tgt_renamed[f"__dg_tgt_{tf}__"])

    joined = candidates.left_join(tgt_renamed, [join_predicate])
    # Bad rows: the join's right side (first target column) is null
    # after the LEFT JOIN — i.e. no match in target.
    return joined.filter(joined[f"__dg_tgt_{target_fields[0]}__"].isnull())


# ---------------------------------------------------------------------------
# Per-FK helper
# ---------------------------------------------------------------------------


def check_foreign_key(
    fk: ForeignKey,
    source_table_name: str,
    source_expr: TableExpr,
    target_table_name: str,
    target_expr: TableExpr | None,
    *,
    package: DataPackage | None = None,
    strict: bool = False,
) -> list[Issue]:
    """Check a single :class:`~corral.spec.model.ForeignKey`.

    Normalizes both sides to ibis, then runs the missing-target check
    as a SQL ``LEFT JOIN``. Composite FKs join on the full key tuple;
    same-table self-references work the same way (the caller passes
    the source as ``target_expr``).

    Args:
        fk: The :class:`~corral.spec.model.ForeignKey` to check.
        source_table_name: Logical name of the source table.
        source_expr: Engine-native table expression for the source.
        target_table_name: Logical name of the target table. For
            self-referential FKs (``fk.reference.resource == ""``)
            callers should pass the source's own name.
        target_expr: Engine-native target table expression, or ``None``
            when the target table isn't available — in which case the
            helper emits one ``fk.unverifiable`` issue and returns.
        package: Optional :class:`~corral.spec.model.DataPackage`,
            consulted to determine whether a source FK column is
            ``required=True`` (which upgrades a null to
            ``fk.null_in_required_fk``).
        strict: When ``True``, ``fk.unverifiable`` issues are ERROR
            instead of WARNING.

    Returns:
        A list of :class:`Issue` records. Empty when the FK is
        satisfied for every non-null source row.
    """
    source_fields = _as_field_list(fk.fields)
    target_fields = _as_field_list(fk.reference.fields)
    src_label = _format_field_list(source_fields)
    tgt_label = f"{target_table_name}.{_format_field_list(target_fields)}"

    # 1. Unverifiable — target table not in the mapping.
    if target_expr is None:
        if not _source_has_fk_values(to_ibis(source_expr), source_fields):
            # Nothing to verify: the source column is absent or all null.
            return []
        severity = Severity.ERROR if strict else Severity.WARNING
        return [
            Issue(
                severity=severity,
                category=Category.FOREIGN_KEY,
                code="fk.unverifiable",
                message=(
                    f"{source_table_name} FK {src_label}->{tgt_label}: "
                    f"target table {target_table_name!r} missing from package"
                ),
                table=source_table_name,
                fix_hint=(
                    f"Provide the {target_table_name!r} table so the FK can be verified, "
                    f"or remove the FK from the spec."
                ),
                extra={
                    "target_table": target_table_name,
                    "target_field": ",".join(target_fields),
                },
            )
        ]

    src = with_row_index(to_ibis(source_expr))
    tgt = to_ibis(target_expr)

    issues: list[Issue] = []

    # 2. Target-field-missing — pure spec bug. One issue per missing
    # target field; report all of them so the spec author sees the
    # full list in one pass.
    missing_target_fields = [tf for tf in target_fields if tf not in tgt.columns]
    if missing_target_fields:
        for tf in missing_target_fields:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    category=Category.FOREIGN_KEY,
                    code="fk.target_field_missing",
                    message=(
                        f"{source_table_name} FK {src_label}->{tgt_label}: "
                        f"target field {tf!r} does not exist in {target_table_name}"
                    ),
                    table=source_table_name,
                    fix_hint=(
                        f"Add a {tf!r} column to {target_table_name!r}, or update "
                        f"the FK reference in the spec to point at an existing field."
                    ),
                    extra={"target_table": target_table_name, "target_field": tf},
                )
            )
        # Without the target fields we can't run the per-row check —
        # everything would look "missing". Return what we have.
        return issues

    # 3. Null-in-required-FK check — independent of the target match.
    issues.extend(
        _emit_null_in_required_fk_issues(
            src,
            source_table_name,
            source_fields,
            package=package,
        )
    )

    # 4. Missing-target check — source columns may be absent
    # (optional FK that wasn't populated). If so there's nothing
    # to check.
    if any(sf not in src.columns for sf in source_fields):
        return issues

    bad = _missing_target_predicate(src, tgt, source_fields, target_fields)
    total = count(bad)
    if total == 0:
        return issues

    # Sample the offending rows for per-row Issues. The sample carries
    # ``ROW_COL`` from the source-side row index plus the source key
    # columns themselves.
    sample_cols = [ROW_COL, *source_fields]
    sampled = sample(bad.select(*sample_cols), limit=MAX_ROW_ISSUES)

    for sample_row in sampled:
        row_idx = int(sample_row.get(ROW_COL, -1))
        if len(source_fields) == 1:
            value: Any = sample_row.get(source_fields[0])
            value_repr = _format_value(value)
        else:
            value = tuple(sample_row.get(sf) for sf in source_fields)
            value_repr = _format_value(value)
        issues.append(
            Issue(
                severity=Severity.ERROR,
                category=Category.FOREIGN_KEY,
                code="fk.missing_target",
                message=f"{source_table_name}.{src_label} row {row_idx}: value {value_repr} not found in {tgt_label}",
                table=source_table_name,
                column=src_label if len(source_fields) == 1 else None,
                row=row_idx,
                fix_hint=(
                    f"Add a {target_table_name} row with "
                    f"{_format_field_list(target_fields)}={value_repr}, "
                    f"or remove the {source_table_name} row."
                ),
                extra={
                    "target_table": target_table_name,
                    "target_field": (target_fields[0] if len(target_fields) == 1 else ",".join(target_fields)),
                    "value": value,
                },
            )
        )

    if total > MAX_ROW_ISSUES:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                category=Category.FOREIGN_KEY,
                code="fk.missing_target",
                message=(
                    f"{source_table_name}.{src_label}->{tgt_label}: "
                    f"{total} missing-target violations (showing first {MAX_ROW_ISSUES})"
                ),
                table=source_table_name,
                column=src_label if len(source_fields) == 1 else None,
                fix_hint=f"Populate {tgt_label} with the missing keys, or fix the source rows.",
                extra={
                    "target_table": target_table_name,
                    "target_field": (target_fields[0] if len(target_fields) == 1 else ",".join(target_fields)),
                    "total_violations": total,
                    "sample_shown": MAX_ROW_ISSUES,
                },
            )
        )

    return issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def check_foreign_keys(
    package: DataPackage,
    tables: Mapping[str, TableExpr],
    *,
    report: ValidationReport | None = None,
    strict: bool = False,
) -> ValidationReport:
    """Validate every foreign key declared in ``package`` across ``tables``.

    Walks :attr:`package.resources`, picks up every
    :class:`~corral.spec.model.ForeignKey` declared on each
    resource's :class:`~corral.spec.model.Schema`, and routes each
    one through :func:`check_foreign_key`. Cross-table and same-table
    (``reference.resource == ""``) FKs are both handled.

    Each table in ``tables`` is normalized to ibis exactly once (via
    :func:`corral.validation._ibis.to_ibis`) and the wrapped ibis
    Table is shared across every FK that touches it — so a package
    with many FKs against the same target only pays the conversion
    cost for that target once.

    DirtyTracker stamping happens in :meth:`Package.validate` after a
    clean FK pass; this function only emits :class:`Issue` records and
    deliberately doesn't depend on the hash infrastructure, so the
    dependency points one way (dataset → validation).

    Args:
        package: :class:`~corral.spec.model.DataPackage` describing
            every table + its FKs.
        tables: Mapping ``{table_name: TableExpr}``. Tables absent
            from the mapping become :data:`fk.unverifiable` issues
            (one per FK pointing at them).
        report: Existing :class:`ValidationReport` to append into.
            Created when ``None``; returned in either case.
        strict: When ``True``, :data:`fk.unverifiable` issues are
            ERROR instead of WARNING.

    Returns:
        The :class:`ValidationReport` — the same instance as
        ``report`` if one was passed.

    Examples:
        Clean single-FK package:

        >>> from corral.engines.ibis_engine import IbisEngine
        >>> from corral.spec.model import (
        ...     DataPackage, Resource, Schema, Field, ForeignKey, ForeignKeyReference,
        ... )
        >>> e = IbisEngine()
        >>> link = e.scan({"data": [{"link_id": 1, "from_node_id": 1}]})
        >>> node = e.scan({"data": [{"node_id": 1}]})
        >>> pkg = DataPackage(name="x", resources=[
        ...     Resource(name="node", path="node.csv",
        ...         schema=Schema(fields=[Field(name="node_id", type="integer")])),
        ...     Resource(name="link", path="link.csv",
        ...         schema=Schema(
        ...             fields=[Field(name="link_id", type="integer"),
        ...                     Field(name="from_node_id", type="integer")],
        ...             foreign_keys=[ForeignKey(fields="from_node_id",
        ...                 reference=ForeignKeyReference(resource="node",
        ...                                               fields="node_id"))],
        ...         )),
        ... ])
        >>> report = check_foreign_keys(pkg, {"link": link, "node": node})
        >>> report.is_clean
        True
    """
    if report is None:
        report = ValidationReport()

    # Normalize each provided table to ibis exactly once. The per-FK
    # helper also calls ``to_ibis`` but it's a pass-through on an
    # ibis Table so we don't double-wrap.
    materialized: dict[str, Any] = {name: to_ibis(expr) for name, expr in tables.items()}

    for resource in package.resources:
        schema = resource.table_schema
        if schema is None or isinstance(schema, str):
            continue
        if not schema.foreign_keys:
            continue
        source_name = resource.name
        source_table = materialized.get(source_name)
        if source_table is None:
            # The source table itself isn't loaded — nothing we can
            # check. Structural (task 2.5) covers the "table missing"
            # case; we don't double-report it here.
            continue
        for fk in schema.foreign_keys:
            # Same-table reference: resource == "" → target is the
            # source itself. Otherwise the reference names a sibling.
            if fk.reference.resource == "":
                target_name = source_name
                target_table: Any = source_table
            else:
                target_name = fk.reference.resource
                target_table = materialized.get(target_name)
            for issue in check_foreign_key(
                fk,
                source_table_name=source_name,
                source_expr=source_table,
                target_table_name=target_name,
                target_expr=target_table,
                package=package,
                strict=strict,
            ):
                report.add_issue(issue)

    return report
