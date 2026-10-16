"""Core table editing: turn a request (set cells, delete rows, add rows) into corral edits, checked first.

Nothing here mutates. Each ``plan_*`` reads the network and returns an :class:`EditPlan` whose ``edits``
:meth:`~netstead.workbench.session.Session.mutate` applies all-or-nothing. A caller that knows the network's
version passes it (``version=``), so ``mutate(..., expected_version=plan.version)`` can refuse a stale plan.
A plan refuses (raises :class:`EditRefused`) only what the network cannot hold:

* a value its column's storage type cannot hold: ``"fast"`` in a float column, or ``1.5`` in an integer
  column, which corral would otherwise silently widen to float;
* a column the table does not have (corral would add it), a key column, or a column whose type P2 cannot
  edit (geometry, binary, dates, times);
* a missing row, and deleting a node that a link still starts or ends at.

What the *spec* says about a value (required, enum, type, foreign keys, duplicate keys) is a warning, never
a refusal: see :mod:`netstead.workbench.editcheck`. Two kinds of callable appear below: a corral ``Edit``
predicate maps a table to a boolean column (:func:`_in`); a corral ``Table.filter`` argument maps a table
expression to a filtered one (:func:`_where`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from corral.editing import Edit

from netstead.viz.styling import json_scalar

from .related import ForeignKey, foreign_keys, primary_keys

__all__ = [
    "EditPlan",
    "EditRefused",
    "coerce_value",
    "editable",
    "plan_add",
    "plan_delete",
    "plan_update",
    "table_keys",
]

#: A deletion never cascades into these: a link that still uses a deleted node is refused instead.
_NETWORK_TABLES = ("link", "node")


class EditRefused(ValueError):
    """The edit cannot be applied as asked; the message says why (nothing was changed)."""


@dataclass
class EditPlan:
    """The corral edits for one request, rows per table it adds, changes or removes, its keys, and a summary.

    ``recheck`` lists keys the live checks should look at besides the rows the edits touch: the rows whose
    self-reference pointed at a deleted row. ``version`` is the network version the plan read (``None``: the
    caller did not say); pass it to ``mutate(..., expected_version=plan.version)``, which refuses the plan if
    the network has changed since.
    """

    edits: list[Edit]
    summary: str
    rows: dict[str, int] = field(default_factory=dict)
    keys: list[Any] = field(default_factory=list)  # the target table's keys, coerced to the key's type
    recheck: dict[str, list[Any]] = field(default_factory=dict)
    version: int | None = None


def table_keys(net: Any) -> dict[str, str | None]:
    """Each table's single-column primary key, from the spec (``None`` when it has none)."""
    return primary_keys(net.spec, _columns(net))


def editable(dtype: Any) -> bool:
    """Whether P2 can edit a column of this ibis storage type (numbers, text, booleans).

    >>> import ibis.expr.datatypes as dt
    >>> editable(dt.float64), editable(dt.binary)
    (True, False)
    """
    return bool(
        dtype.is_boolean() or dtype.is_integer() or dtype.is_floating() or dtype.is_decimal() or dtype.is_string()
    )


def coerce_value(value: Any, dtype: Any, column: str) -> Any:
    """``value`` as ``column``'s ibis storage ``dtype``, or :class:`EditRefused`.

    ``None``, and empty text in a non-text column, clear the cell. Integers take integral numbers or text
    within the column's range; floats take finite numbers or numeric text; text takes any scalar; booleans
    take ``true/false/1/0/yes/no``. A boolean is not a number: ``True`` in a number column is refused.

    >>> import ibis.expr.datatypes as dt
    >>> coerce_value("45", dt.float64, "free_speed"), coerce_value(2.0, dt.int64, "lanes")
    (45.0, 2)
    >>> coerce_value("", dt.int64, "lanes") is None
    True
    """
    if value is None or (isinstance(value, str) and not value.strip() and not dtype.is_string()):
        return None
    if not editable(dtype):
        raise EditRefused(f"{column} ({dtype}) cannot be edited yet")
    try:
        if dtype.is_boolean():
            text = str(value).strip().lower()
            if text in ("true", "1", "yes"):
                return True
            if text in ("false", "0", "no"):
                return False
            raise ValueError(value)
        if isinstance(value, bool) and not dtype.is_string():
            raise ValueError(value)  # bool is an int subclass: True would land as 1
        if dtype.is_integer():
            if isinstance(value, int):
                number = int(value)
            else:
                real = float(value)
                if not real.is_integer():  # also refuses inf and nan
                    raise ValueError(value)
                number = int(real)
            low, high = dtype.bounds
            if not low <= number <= high:
                raise ValueError(value)
            return number
        if dtype.is_floating() or dtype.is_decimal():
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(value)
            return number
        return str(value)
    except (TypeError, ValueError):
        raise EditRefused(f"{column} holds {dtype}; {value!r} does not fit it") from None


def plan_update(
    net: Any, table: str, ids: Sequence[Any], values: Mapping[str, Any], *, version: int | None = None
) -> EditPlan:
    """Set ``values`` (``{column: value}``; ``None`` clears) on the rows of ``table`` whose key is in ``ids``."""
    src, pk = _table(net, table)
    if not ids:
        raise EditRefused("give at least one row to edit")
    if not values:
        raise EditRefused("give at least one column to set")
    schema = src.expr.schema()
    sets: dict[str, Any] = {}
    for column, value in values.items():
        if column == pk:
            raise EditRefused(f"{column} is the {table} key; keys cannot be edited here (add a row with the new key)")
        if column not in schema:
            raise EditRefused(f"{table} has no column {column!r}")
        if not editable(schema[column]):  # before coerce_value lets None through: clearing a geometry is an edit too
            raise EditRefused(f"{column} ({schema[column]}) cannot be edited yet")
        sets[column] = coerce_value(value, schema[column], column)
    keys = _keys(src, pk, ids)
    _require_present(src, pk, keys, table)
    edit = Edit(op="update_rows", table=table, payload={"predicate": _in(pk, keys), "set": sets})
    what = ", ".join(f"{c} = {_show(v)}" for c, v in sets.items())
    return EditPlan([edit], f"{table} {_few(keys)}: {what}", {table: len(keys)}, keys, version=version)


def plan_delete(net: Any, table: str, ids: Sequence[Any], *, version: int | None = None) -> EditPlan:
    """Delete rows of ``table`` and, first, every row that depends on them through the spec's foreign keys.

    Dependents are found recursively (a link's lanes, then each lane's ``lane_tod`` rows), deepest first. A
    link or a node is never deleted as a dependent: deleting a node a link still uses is refused, naming the
    links. Self-references and cycles through the spec's keys are not followed; the rows that pointed at a
    deleted row there go in ``recheck``. A row reached through two foreign keys is counted once.
    """
    src, pk = _table(net, table)
    if not ids:
        raise EditRefused("give at least one row to delete")
    keys = _keys(src, pk, ids)
    _require_present(src, pk, keys, table)
    plan = EditPlan([], "", keys=keys, version=version)
    fks = foreign_keys(net.spec, _columns(net), self_refs=True)
    gone: dict[str, set[Any]] = {table: set(keys)}
    keyless: dict[str, int] = {}
    _cascade(net, fks, table_keys(net), table, pk, keys, plan, frozenset({table}), gone, keyless)
    plan.edits.append(Edit(op="delete_rows", table=table, payload={"predicate": _in(pk, keys)}))
    for edit in plan.edits:  # deepest first, the target last
        plan.rows.setdefault(edit.table, len(gone[edit.table]) if edit.table in gone else keyless[edit.table])
    plan.recheck = {t: list(dict.fromkeys(k for k in ks if k not in gone.get(t, ()))) for t, ks in plan.recheck.items()}
    deps = ", ".join(f"{n} {t}" for t, n in plan.rows.items() if t != table)
    plan.summary = f"delete {table} {_few(keys)}" + (f" (and {deps} rows that depend on it)" if deps else "")
    return plan


def plan_add(net: Any, table: str, rows: Sequence[Mapping[str, Any]], *, version: int | None = None) -> EditPlan:
    """Append ``rows`` to ``table``: every row needs its key; columns left out are empty (null).

    ``None`` is accepted in any column, even one P2 cannot edit (a geometry): a new row may leave it empty.

    A key that is already taken is added anyway; the live checks report it as ``edit.duplicate_key``.
    """
    src, pk = _table(net, table)
    if not rows:
        raise EditRefused("give at least one row to add")
    schema = src.expr.schema()
    out = []
    for i, row in enumerate(rows, start=1):
        unknown = sorted(set(row) - set(schema))
        if unknown:
            raise EditRefused(f"row {i}: {table} has no column(s) {', '.join(unknown)}")
        coerced = {c: coerce_value(v, schema[c], c) for c, v in row.items()}
        key = coerced.get(pk)  # coerced first: blank text in a number key column comes back as None
        if key is None or (isinstance(key, str) and not key.strip()):
            raise EditRefused(f"row {i}: every added {table} row needs its key, {pk}")
        out.append(coerced)
    keys = [r[pk] for r in out]
    edit = Edit(op="add_rows", table=table, payload={"rows": out})
    return EditPlan([edit], f"add {table} {_few(keys)}", {table: len(out)}, keys, version=version)


# ---------------------------------------------------------------- helpers


def _columns(net: Any) -> dict[str, list[str]]:
    return {name: t.columns() for name, t in net.tables.items()}


def _few(ids: Sequence[Any], n: int = 5) -> str:
    items = list(ids)
    shown = ", ".join(str(i) for i in items[:n])
    return shown + (f" (+{len(items) - n} more)" if len(items) > n else "")


def _show(value: Any) -> str:
    return "empty" if value is None else repr(value)


def _in(column: str, values: Sequence[Any]) -> Callable[[Any], Any]:
    """A corral ``Edit`` predicate: the rows whose ``column`` is in ``values``."""
    frozen = list(values)
    return lambda t: t[column].isin(frozen)


def _where(column: str, values: Sequence[Any]) -> Callable[[Any], Any]:
    """A corral ``Table.filter`` transform: the same rows, as a filtered expression."""
    frozen = list(values)
    return lambda expr: expr.filter(expr[column].isin(frozen))


def _table(net: Any, name: str) -> tuple[Any, str]:
    src = net.tables.get(name)
    if src is None:
        raise EditRefused(f"the network has no {name} table")
    pk = table_keys(net).get(name)
    if pk is None:
        raise EditRefused(f"{name} has no single-column primary key, so its rows cannot be edited here")
    return src, pk


def _keys(src: Any, pk: str, ids: Sequence[Any]) -> list[Any]:
    dtype = src.expr.schema()[pk]
    return list(dict.fromkeys(coerce_value(i, dtype, pk) for i in ids))


def _values(src: Any, column: str, values: Sequence[Any], out: str) -> list[Any]:
    """``out`` of every row of ``src`` whose ``column`` is in ``values`` (one bounded DuckDB read)."""
    frame = src.filter(_where(column, values)).select(out).to_pandas()
    return [json_scalar(v) for v in frame[out]]


def _require_present(src: Any, pk: str, keys: list[Any], name: str) -> None:
    found = set(_values(src, pk, keys, pk))
    missing = [k for k in keys if k not in found]
    if missing:
        raise EditRefused(f"{name} {_few(missing)} not found")


def _cascade(
    net: Any,
    fks: list[ForeignKey],
    pks: Mapping[str, str | None],
    table: str,
    column: str,
    values: list[Any],
    plan: EditPlan,
    seen: frozenset[str],
    gone: dict[str, set[Any]],
    keyless: dict[str, int],
) -> None:
    """Queue deletes (deepest first) for rows of other tables whose key points at ``table.column`` in ``values``.

    ``seen`` is the path from the target (a table on it again is a cycle: not followed); ``gone`` collects
    the keys each keyed table loses (so a row reached twice counts once), ``keyless`` the rows of tables
    without a key.
    """
    for fk in fks:
        if fk.ref_table != table or fk.ref_column != column:
            continue
        own_pk = pks.get(fk.table)
        hits = _values(net.tables[fk.table], fk.column, values, own_pk or fk.column)
        if not hits:
            continue
        if fk.table == table or fk.table in seen:
            # A self-reference or a cycle: not followed. The rows that pointed here are re-checked, so a
            # dangling reference left behind surfaces as edit.fk_missing.
            if own_pk:
                plan.recheck.setdefault(fk.table, []).extend(hits)
            continue
        if fk.table in _NETWORK_TABLES:
            raise EditRefused(
                f"{table} {_few(values)} is still used by {fk.table}.{fk.column} ({fk.table} {_few(hits)}); "
                f"delete those {fk.table}s first"
            )
        if own_pk:
            taken = gone.setdefault(fk.table, set())
            new = [h for h in hits if h not in taken]
            if not new:  # every row is already deleted by an earlier edit in this plan
                continue
            taken.update(new)
            _cascade(net, fks, pks, fk.table, own_pk, new, plan, seen | {fk.table}, gone, keyless)
        else:
            keyless[fk.table] = keyless.get(fk.table, 0) + len(hits)
        plan.edits.append(Edit(op="delete_rows", table=fk.table, payload={"predicate": _in(fk.column, values)}))
