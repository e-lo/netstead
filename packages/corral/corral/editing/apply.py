"""Internal: dispatch one :class:`Edit` against a :class:`Package`.

Ibis-first per architecture §6.1: op handlers take the table's own
engine expression and route the payload through ibis (``filter`` /
``ifelse`` / ``anti_join``), so the work runs on the engine's connection
(under its lock) rather than on an ``ibis.memtable`` copy of the whole
table, one per op, registered with ibis's process-wide default backend
(#145). Results materialise via pyarrow so the module stays
pandas-free. Sample capture is bounded at :data:`SAMPLE_CAP` rows.

The dispatch tables :data:`APPLY_OPS` + :data:`REVERSE_OPS` are the
sole extension seam — a new op only adds to those two dicts.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import ibis
import pyarrow as pa

from corral.validation._ibis import to_ibis

from .errors import InvalidPayload, UnknownTable, UnsupportedEditOp
from .types import SAMPLE_CAP, Diff, Edit, EditResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.dataset.package import Package
    from corral.engines.base import Engine, TableExpr


# ---------------------------------------------------------------------------
# Helpers — summary-only docstrings (internal per §9)
# ---------------------------------------------------------------------------


def _sample(arrow: pa.Table, *, cap: int = SAMPLE_CAP) -> list[dict]:
    """Materialise up to ``cap`` rows of ``arrow`` as a list of row dicts."""
    return arrow.slice(0, cap).to_pylist()


def _sample_expr(expr: ibis.Table, *, cap: int = SAMPLE_CAP) -> list[dict]:
    """Up to ``cap`` rows of ``expr`` as row dicts, fetching only those rows."""
    return expr.head(cap).to_pyarrow().to_pylist()


def _arrow_of(expr: TableExpr) -> pa.Table:
    """Normalise an engine-native expression to a pyarrow Table via ibis."""
    return to_ibis(expr).to_pyarrow()


def _engine_table(engine: Engine, arrow: pa.Table) -> TableExpr:
    """Wrap a pyarrow Table back into an engine-native expression without dtype loss.

    Goes straight through :meth:`Engine.from_arrow` so binary / decimal /
    timestamp / large-string columns survive the round-trip. The old
    ``from_records(arrow.to_pylist())`` path coerced binary to ``bytes``,
    decimals to ``Decimal``, and dropped tz from timestamps — which
    corrupted hash equality after edit + rollback.
    """
    return engine.from_arrow(arrow)


def _require(payload: dict, key: str, op: str) -> Any:
    """Pull ``key`` from ``payload`` or raise :class:`InvalidPayload`."""
    if key not in payload:
        raise InvalidPayload(f"Edit op {op!r} requires payload key {key!r}; got keys={sorted(payload)}")
    return payload[key]


def _concat_arrow(left: pa.Table, right: pa.Table) -> pa.Table:
    """Vertically concatenate two pyarrow Tables, promoting missing columns to typed nulls.

    pyarrow's :func:`concat_tables` insists schemas match; we build the
    column-name union, pick a non-null type per column from whichever
    side has it, then align + concat.
    """
    if left.schema == right.schema:
        return pa.concat_tables([left, right])

    all_names: list[str] = list(left.column_names)
    for name in right.column_names:
        if name not in left.column_names:
            all_names.append(name)

    type_for: dict[str, pa.DataType] = {}
    for name in all_names:
        ltype = left.schema.field(name).type if name in left.column_names else None
        rtype = right.schema.field(name).type if name in right.column_names else None
        if ltype is not None and not pa.types.is_null(ltype):
            type_for[name] = ltype
        elif rtype is not None and not pa.types.is_null(rtype):
            type_for[name] = rtype
        else:
            type_for[name] = ltype or rtype or pa.null()

    def _aligned(t: pa.Table) -> pa.Table:
        cols: list[Any] = []
        for name in all_names:
            target = type_for[name]
            if name in t.column_names:
                arr = t.column(name)
                if arr.type != target:
                    arr = arr.cast(target)
                cols.append(arr)
            else:
                cols.append(pa.nulls(t.num_rows, type=target))
        return pa.table(dict(zip(all_names, cols, strict=True)))

    return pa.concat_tables([_aligned(left), _aligned(right)])


def _null_safe(left: ibis.Table, right: ibis.Table, columns: list[str]) -> list[Any]:
    """Join predicates that also match NULL to NULL (``=`` never does, so a row holding a null would never match).

    ``right`` is built with ``left``'s types (:func:`_rows_like`), so no cast is needed; one is added only for a
    column whose recorded rows could not take the current type.
    """
    out = []
    for c in columns:
        rhs = right[c] if right[c].type() == left[c].type() else right[c].cast(left[c].type())
        out.append(left[c].identical_to(rhs))
    return out


def _rows_like(rows: list[dict], current: pa.Table, engine: Engine) -> pa.Table:
    """Recorded rows as arrow, typed like ``current``'s columns (a NULL-only column keeps its real type).

    Types are inferred from the rows, then each column ``current`` has is cast to its type with
    ``safe=True``, so ``2.7`` in an integer column raises instead of landing as ``2``. A NULL-only
    column (inferred ``null``) becomes typed nulls: inferring alone made an all-NULL timestamp,
    decimal, binary or list column ``null`` (then ``string``), which no cast turns back into a list.
    When a cast raises, the rows are read as the engine infers them, and a value the column's type
    cannot take fails when the rows meet the table (a safe cast there too).
    """
    fields = {f.name: f for f in current.schema}
    try:
        inferred = pa.Table.from_pylist(rows)
    except (pa.ArrowException, TypeError, ValueError):
        return _arrow_of(engine.from_records(rows))
    columns: list[pa.ChunkedArray | pa.Array] = []
    try:
        for name, column in zip(inferred.column_names, inferred.columns, strict=True):
            field = fields.get(name)
            if field is None or column.type == field.type:
                columns.append(column)
            elif pa.types.is_null(column.type):
                columns.append(pa.nulls(len(column), field.type))
            else:
                columns.append(column.cast(field.type, safe=True))
    except pa.ArrowException:
        return _arrow_of(engine.from_records(rows))
    return pa.table(dict(zip(inferred.column_names, columns, strict=True)))


# ---------------------------------------------------------------------------
# Op handlers — each returns (new_arrow, rollback_data, diff_counts)
# ---------------------------------------------------------------------------


def _apply_add_rows(current_expr: ibis.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Append ``payload['rows']`` to the table; rollback blob records the verbatim added rows."""
    rows = _require(edit.payload, "rows", edit.op)
    if not isinstance(rows, list):
        raise InvalidPayload(f"Edit op 'add_rows' payload['rows'] must be a list; got {type(rows).__name__}")
    current = current_expr.to_pyarrow()
    if not rows:
        return current, {"added_rows": []}, {"rows_added": 0, "rows_removed": 0, "rows_changed": 0}

    new_arrow = _rows_like(rows, current, engine)  # typed like the table: a NULL-only list column stays a list
    combined = _concat_arrow(current, new_arrow)
    return (
        combined,
        {"added_rows": new_arrow.to_pylist()},
        {"rows_added": len(rows), "rows_removed": 0, "rows_changed": 0},
    )


def _apply_delete_rows(ibis_current: ibis.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Remove rows matching ``payload['predicate']``; rollback blob carries the removed rows."""
    predicate = _require(edit.payload, "predicate", edit.op)
    if not callable(predicate):
        raise InvalidPayload("Edit op 'delete_rows' payload['predicate'] must be callable(ibis.Table) -> BooleanColumn")

    cond: Any = predicate(ibis_current)  # an ibis BooleanColumn, per the payload contract
    deleted_arrow = ibis_current.filter(cond).to_pyarrow()
    kept_arrow = ibis_current.filter(~cond).to_pyarrow()
    return (
        kept_arrow,
        {"deleted_rows": deleted_arrow.to_pylist()},
        {"rows_added": 0, "rows_removed": deleted_arrow.num_rows, "rows_changed": 0},
    )


def _apply_update_rows(ibis_current: ibis.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Update matched rows via ``payload['predicate']`` + ``set``; blob stores prior values."""
    predicate = _require(edit.payload, "predicate", edit.op)
    changes: dict = _require(edit.payload, "set", edit.op)
    if not callable(predicate):
        raise InvalidPayload("Edit op 'update_rows' payload['predicate'] must be callable(ibis.Table) -> BooleanColumn")
    if not isinstance(changes, dict) or not changes:
        raise InvalidPayload("Edit op 'update_rows' payload['set'] must be a non-empty dict")

    cond: Any = predicate(ibis_current)  # an ibis BooleanColumn, per the payload contract
    matched_arrow = ibis_current.filter(cond).to_pyarrow()
    mutated = ibis_current.mutate(
        **{col: cond.ifelse(ibis.literal(val), ibis_current[col]) for col, val in changes.items()}
    )
    new_arrow = mutated.to_pyarrow()
    return (
        new_arrow,
        {"original_rows": matched_arrow.to_pylist(), "predicate_fields": list(changes)},
        {"rows_added": 0, "rows_removed": 0, "rows_changed": matched_arrow.num_rows},
    )


def _apply_replace_table(current_expr: ibis.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Replace the whole table with ``payload['expr']``; blob carries the prior rows."""
    new_arrow = _arrow_of(_require(edit.payload, "expr", edit.op))
    current = current_expr.to_pyarrow()
    return (
        new_arrow,
        {"prior_rows": current.to_pylist()},
        {
            "rows_added": max(0, new_arrow.num_rows - current.num_rows),
            "rows_removed": max(0, current.num_rows - new_arrow.num_rows),
            "rows_changed": min(current.num_rows, new_arrow.num_rows),
        },
    )


#: Dispatch table — add a new op by registering ``(apply, reverse)`` here.
APPLY_OPS = {
    "add_rows": _apply_add_rows,
    "delete_rows": _apply_delete_rows,
    "update_rows": _apply_update_rows,
    "replace_table": _apply_replace_table,
}


# ---------------------------------------------------------------------------
# Reverse handlers — feed rollback_data back through the apply pipeline
# ---------------------------------------------------------------------------


def _reverse_add_rows(current: pa.Table, rollback_data: dict, engine: Engine) -> pa.Table:
    """Anti-join ``current`` against the recorded added rows on every shared column.

    Columns are compared null-safely (NULL matches NULL).
    """
    added_rows = rollback_data.get("added_rows") or []
    if not added_rows:
        return current
    added_arrow = _rows_like(added_rows, current, engine)
    join_cols = [c for c in current.column_names if c in added_arrow.column_names]
    if not join_cols:
        return current
    left, right = ibis.memtable(current), ibis.memtable(added_arrow)
    return left.anti_join(right, predicates=_null_safe(left, right, join_cols)).to_pyarrow()


def _reverse_delete_rows(current: pa.Table, rollback_data: dict, engine: Engine) -> pa.Table:
    """Re-append the rows the original delete removed."""
    deleted_rows = rollback_data.get("deleted_rows") or []
    if not deleted_rows:
        return current
    return _concat_arrow(current, _rows_like(deleted_rows, current, engine))


def _reverse_update_rows(current: pa.Table, rollback_data: dict, engine: Engine) -> pa.Table:
    """Restore pre-update values by anti-joining on identity (non-touched) columns + concat originals.

    Best-effort when no identity columns survive — restores the original snapshot wholesale. Columns are compared
    null-safely (NULL matches NULL).
    """
    original_rows = rollback_data.get("original_rows") or []
    touched = rollback_data.get("predicate_fields") or []
    if not original_rows:
        return current
    original_arrow = _rows_like(original_rows, current, engine)
    if not touched:
        return current
    identity_cols = [c for c in current.column_names if c not in touched]
    if not identity_cols:
        return original_arrow
    left, right = ibis.memtable(current), ibis.memtable(original_arrow)
    kept = left.anti_join(right, predicates=_null_safe(left, right, identity_cols)).to_pyarrow()
    return _concat_arrow(kept, original_arrow)


def _reverse_replace_table(current: pa.Table, rollback_data: dict, engine: Engine) -> pa.Table:
    """Restore the prior arrow snapshot from ``_apply_replace_table``."""
    prior_rows = rollback_data.get("prior_rows") or []
    if not prior_rows:
        return current.slice(0, 0)
    return _arrow_of(engine.from_records(prior_rows))


REVERSE_OPS = {
    "add_rows": _reverse_add_rows,
    "delete_rows": _reverse_delete_rows,
    "update_rows": _reverse_update_rows,
    "replace_table": _reverse_replace_table,
}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_edit(package: Package, edit: Edit, *, session_id: str | None = None) -> EditResult:
    """Apply one :class:`Edit` to ``package``; return the :class:`EditResult`.

    Mutates ``package`` in place — the target :class:`Table`'s ``expr``
    is replaced, its ``dirty`` flag is set, and the package's
    :class:`DirtyTracker` (when attached) is notified via
    ``mark_dirty``. The returned :class:`EditResult` carries the
    rollback blob needed by :func:`reverse_edit`.

    Args:
        package: Target package; must contain ``edit.table``.
        edit: The :class:`Edit` to apply.
        session_id: Optional owning session id stamped on the result.

    Returns:
        :class:`EditResult` with diff + rollback blob.

    Raises:
        UnknownTable: ``edit.table`` not in ``package``.
        UnsupportedEditOp: ``edit.op`` not in :data:`APPLY_OPS`.
        InvalidPayload: Payload missing required keys for its op.

    Examples:
        >>> from corral.dataset import Package, Table
        >>> from corral.editing import Edit
        >>> from corral.editing.apply import apply_edit
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}]), engine=e)})
        >>> r = apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"a": 2}]}))
        >>> r.diff.rows_added, pkg["x"].count()
        (1, 2)
    """
    if edit.table not in package.tables:
        raise UnknownTable(f"Edit targets table {edit.table!r}, not in package {sorted(package.tables)}")
    handler = APPLY_OPS.get(edit.op)
    if handler is None:
        raise UnsupportedEditOp(f"Edit op {edit.op!r} not in dispatch table; supported: {sorted(APPLY_OPS)}")

    table = package.tables[edit.table]
    engine = table.engine
    current = to_ibis(table.expr)  # the engine's own expression, not a memtable copy per op (#145)
    before_sample = _sample_expr(current)
    pre_edit_dirty = table.dirty

    new_arrow, rollback_data, counts = handler(current, edit, engine)
    # Stash the pre-edit dirty flag inside the rollback blob so
    # reverse_edit can restore it (else a rolled-back edit on a
    # previously-clean table leaves it spuriously dirty).
    rollback_data = {**rollback_data, "_pre_edit_dirty": pre_edit_dirty}
    after_sample = _sample(new_arrow)

    table.expr = _engine_table(engine, new_arrow)
    table.dirty = True
    if package.dirty_tracker is not None:
        package.dirty_tracker.mark_dirty(edit.table)

    diff = Diff(
        edit=edit,
        rows_added=counts["rows_added"],
        rows_removed=counts["rows_removed"],
        rows_changed=counts["rows_changed"],
        before_sample=before_sample,
        after_sample=after_sample,
    )
    return EditResult(
        edit=edit,
        diff=diff,
        rollback_data=rollback_data,
        applied_at=datetime.now(),
        session_id=session_id,
    )


def reverse_edit(package: Package, result: EditResult) -> None:
    """Reverse a previously applied :class:`EditResult` against ``package`` (in-place).

    Inverts the op recorded on ``result.edit`` using :data:`REVERSE_OPS`.
    Raises :class:`UnknownTable` / :class:`UnsupportedEditOp` on bad input.
    """
    if result.edit.table not in package.tables:
        raise UnknownTable(
            f"Cannot reverse edit on table {result.edit.table!r}: not in package {sorted(package.tables)}"
        )
    reverser = REVERSE_OPS.get(result.edit.op)
    if reverser is None:
        raise UnsupportedEditOp(f"Op {result.edit.op!r} has no reverse handler; supported: {sorted(REVERSE_OPS)}")
    table = package.tables[result.edit.table]
    engine = table.engine
    current_arrow = _arrow_of(table.expr)
    rolled_back = reverser(current_arrow, result.rollback_data, engine)
    table.expr = _engine_table(engine, rolled_back)
    # Restore the dirty flag the table carried *before* the apply. A
    # rollback of an edit applied to a clean table should leave it
    # clean; a rollback on an already-dirty table preserves that.
    # Default to True for legacy rollback blobs missing the stamp.
    table.dirty = bool(result.rollback_data.get("_pre_edit_dirty", True))
    if package.dirty_tracker is not None:
        package.dirty_tracker.mark_dirty(result.edit.table)


def affected_rows(result: EditResult) -> list[dict]:
    """The rows an applied edit added, changed (as they were *before* the edit) or removed.

    Read from the edit's rollback data, so callers never interpret that blob themselves. Empty for
    ``replace_table`` (every row) and for an edit that matched nothing.

    Examples:
        >>> from corral.dataset import Package, Table
        >>> from corral.editing import Edit
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}]), engine=e)})
        >>> affected_rows(apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"a": 2}]})))
        [{'a': 2}]
    """
    data = result.rollback_data or {}
    for key in ("added_rows", "original_rows", "deleted_rows"):
        if key in data:
            return list(data[key] or [])
    return []
