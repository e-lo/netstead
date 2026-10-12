"""Internal: dispatch one :class:`Edit` against a :class:`Package`.

Ibis-first per architecture §6.1: op handlers route the payload
through ibis (``filter`` / ``ifelse`` / ``anti_join``) and materialise
via pyarrow so the module stays pandas-free. Sample capture is bounded
at :data:`SAMPLE_CAP` rows.

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

    ``right`` is cast to ``left``'s types: a column that is all null in the recorded rows arrives typed ``null``.
    """
    return [left[c].identical_to(right[c].cast(left[c].type())) for c in columns]


# ---------------------------------------------------------------------------
# Op handlers — each returns (new_arrow, rollback_data, diff_counts)
# ---------------------------------------------------------------------------


def _apply_add_rows(current: pa.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Append ``payload['rows']`` to ``current``; rollback blob records the verbatim added rows."""
    rows = _require(edit.payload, "rows", edit.op)
    if not isinstance(rows, list):
        raise InvalidPayload(f"Edit op 'add_rows' payload['rows'] must be a list; got {type(rows).__name__}")
    if not rows:
        return current, {"added_rows": []}, {"rows_added": 0, "rows_removed": 0, "rows_changed": 0}

    new_arrow = _arrow_of(engine.from_records(rows))
    combined = _concat_arrow(current, new_arrow)
    return (
        combined,
        {"added_rows": new_arrow.to_pylist()},
        {"rows_added": len(rows), "rows_removed": 0, "rows_changed": 0},
    )


def _apply_delete_rows(current: pa.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Remove rows matching ``payload['predicate']``; rollback blob carries the removed rows."""
    predicate = _require(edit.payload, "predicate", edit.op)
    if not callable(predicate):
        raise InvalidPayload("Edit op 'delete_rows' payload['predicate'] must be callable(ibis.Table) -> BooleanColumn")

    ibis_current = ibis.memtable(current)
    deleted_arrow = ibis_current.filter(predicate(ibis_current)).to_pyarrow()
    kept_arrow = ibis_current.filter(~predicate(ibis_current)).to_pyarrow()
    return (
        kept_arrow,
        {"deleted_rows": deleted_arrow.to_pylist()},
        {"rows_added": 0, "rows_removed": deleted_arrow.num_rows, "rows_changed": 0},
    )


def _apply_update_rows(current: pa.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Update matched rows via ``payload['predicate']`` + ``set``; blob stores prior values."""
    predicate = _require(edit.payload, "predicate", edit.op)
    changes: dict = _require(edit.payload, "set", edit.op)
    if not callable(predicate):
        raise InvalidPayload("Edit op 'update_rows' payload['predicate'] must be callable(ibis.Table) -> BooleanColumn")
    if not isinstance(changes, dict) or not changes:
        raise InvalidPayload("Edit op 'update_rows' payload['set'] must be a non-empty dict")

    ibis_current = ibis.memtable(current)
    cond = predicate(ibis_current)
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


def _apply_replace_table(current: pa.Table, edit: Edit, engine: Engine) -> tuple[pa.Table, Any, dict]:
    """Replace the whole table with ``payload['expr']``; blob carries the prior rows."""
    new_arrow = _arrow_of(_require(edit.payload, "expr", edit.op))
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
    added_arrow = _arrow_of(engine.from_records(added_rows))
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
    return _concat_arrow(current, _arrow_of(engine.from_records(deleted_rows)))


def _reverse_update_rows(current: pa.Table, rollback_data: dict, engine: Engine) -> pa.Table:
    """Restore pre-update values by anti-joining on identity (non-touched) columns + concat originals.

    Best-effort when no identity columns survive — restores the original snapshot wholesale. Columns are compared
    null-safely (NULL matches NULL).
    """
    original_rows = rollback_data.get("original_rows") or []
    touched = rollback_data.get("predicate_fields") or []
    if not original_rows:
        return current
    original_arrow = _arrow_of(engine.from_records(original_rows))
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
    current_arrow = _arrow_of(table.expr)
    before_sample = _sample(current_arrow)
    pre_edit_dirty = table.dirty

    new_arrow, rollback_data, counts = handler(current_arrow, edit, engine)
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
