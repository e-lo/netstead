"""Live checks for edited rows: what the spec says about the values an edit left behind (warn, never block).

After every mutation the session re-checks the rows its pending edits have touched, by primary key:

* ``edit.required_empty``: a required field is empty;
* ``edit.type``: a value the column stores but the spec's type cannot hold (``2.5`` for an ``integer``
  field whose column was widened to float; text in a ``number`` field stored as text);
* ``edit.enum``: a value outside the field's ``enum``;
* ``edit.fk_missing``: a foreign-key value the referenced table does not have (self-references included);
* ``edit.duplicate_key``: another row has the same primary key.

Each check reads only the touched rows (an ibis ``IN`` filter) and the referenced keys it needs, so it is
bounded by the edit, not by the network; at most :data:`MAX_CHECKED_KEYS` keys per table are checked, the
newest edit's keys first. A whole row is checked, not just the edited cells: a row is what the spec
constrains. A deletion touches the rows that still point at what it deleted (:func:`referencing_keys`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd
from corral.editing import EditResult, affected_rows

from netstead.viz.styling import json_scalar

from .editing import table_keys
from .related import _schemas, foreign_keys

__all__ = [
    "MAX_CHECKED_KEYS",
    "CheckResult",
    "EditWarning",
    "check_rows",
    "fits_type",
    "referencing_keys",
    "touched_keys",
]

#: Keys checked per table after one mutation; beyond this the result says ``truncated``.
MAX_CHECKED_KEYS = 10_000


@dataclass(frozen=True)
class EditWarning:
    """One thing the spec says is wrong with an edited row (shown on its cell, its row and the Issues panel)."""

    code: str
    table: str
    key: Any
    column: str | None
    value: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy."""
        return asdict(self)


@dataclass(frozen=True)
class CheckResult:
    """The warnings for a set of touched rows: whether :data:`MAX_CHECKED_KEYS` cut it short, or it crashed."""

    warnings: tuple[EditWarning, ...] = ()
    truncated: bool = False
    failed: bool = False  # the check raised (logged); its warnings are unknown, not absent

    def for_rows(self, touched: Mapping[str, Iterable[Any]]) -> list[EditWarning]:
        """The warnings on the rows in ``touched`` (``{table: keys}``)."""
        wanted = {(t, k) for t, keys in touched.items() for k in keys}
        return [w for w in self.warnings if (w.table, w.key) in wanted]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy."""
        return {
            "warnings": [w.to_dict() for w in self.warnings],
            "truncated": self.truncated,
            "failed": self.failed,
        }


def fits_type(value: Any, spec_type: str | None) -> bool:
    """Whether a non-null ``value`` is what the spec's Frictionless ``type`` says (``any`` and others: yes).

    >>> fits_type(2.0, "integer"), fits_type(2.5, "integer"), fits_type(True, "number")
    (True, False, False)
    """
    if spec_type == "integer":
        integral = isinstance(value, float) and value.is_integer()
        return (isinstance(value, int) and not isinstance(value, bool)) or integral
    if spec_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if spec_type == "boolean":
        return isinstance(value, bool) or value in (0, 1)
    if spec_type == "string":
        return isinstance(value, str)
    return True


def touched_keys(results: Sequence[EditResult], pks: Mapping[str, str | None]) -> dict[str, set[Any]]:
    """``{table: keys}`` of the rows ``results`` changed or added. Deleted rows are gone, so they are not listed."""
    out: dict[str, set[Any]] = {}
    for result in results:
        pk = pks.get(result.edit.table)
        if pk is None or result.edit.op == "delete_rows":
            continue
        for row in affected_rows(result):
            if row.get(pk) is not None:
                out.setdefault(result.edit.table, set()).add(json_scalar(row[pk]))
    return out


def referencing_keys(net: Any, results: Sequence[EditResult]) -> dict[str, set[Any]]:
    """``{table: keys}`` of the rows that still point, through the spec's foreign keys, at rows ``results`` deleted.

    Read after the edits are applied: one ``IN`` read per foreign key into a table that lost rows. A plugin's
    raw ``delete_rows`` on a link leaves its lanes behind; this is how their dangling ``link_id`` gets checked.
    """
    pks = table_keys(net)
    fks = foreign_keys(net.spec, {n: t.columns() for n, t in net.tables.items()}, self_refs=True)
    deleted: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.edit.op == "delete_rows":
            deleted.setdefault(result.edit.table, []).extend(affected_rows(result))
    out: dict[str, set[Any]] = {}
    for fk in fks:
        rows, pk = deleted.get(fk.ref_table), pks.get(fk.table)
        src = net.tables.get(fk.table)
        if not rows or pk is None or src is None:
            continue
        values = sorted({json_scalar(r[fk.ref_column]) for r in rows if r.get(fk.ref_column) is not None}, key=str)
        if not values:
            continue
        frame = src.filter(_where(fk.column, values)).select(pk).to_pandas()
        out.setdefault(fk.table, set()).update(json_scalar(v) for v in frame[pk] if v is not None)
    return {t: keys for t, keys in out.items() if keys}


def check_rows(
    net: Any, touched: Mapping[str, Iterable[Any]], *, first: Mapping[str, Iterable[Any]] | None = None
) -> CheckResult:
    """Check the rows in ``touched`` (``{table: keys}``) that still exist; tables without a key are skipped.

    ``first`` (a subset of ``touched``: the newest edit's keys) is checked before the rest, so
    :data:`MAX_CHECKED_KEYS` never cuts the newest edit's rows in favour of older ones.
    """
    pks = table_keys(net)
    schemas = _schemas(net.spec)
    fks = foreign_keys(net.spec, {n: t.columns() for n, t in net.tables.items()}, self_refs=True)
    found: list[EditWarning] = []
    truncated = False
    first = first or {}
    for table, wanted in touched.items():
        src, pk = net.tables.get(table), pks.get(table)
        everything = set(wanted)
        newest = sorted(everything & set(first.get(table, ())), key=str)
        keys = newest + sorted(everything - set(newest), key=str)
        if src is None or pk is None or not keys:
            continue
        if len(keys) > MAX_CHECKED_KEYS:  # the newest first, then the oldest; Run validation covers the rest
            keys, truncated = keys[:MAX_CHECKED_KEYS], True
        frame = src.filter(_where(pk, keys)).to_pandas()
        if frame.empty:
            continue
        rows = [{c: _plain(v) for c, v in r.items()} for r in frame.to_dict("records")]
        fields = {f.name: f for f in schemas[table].fields} if table in schemas else {}
        found += _field_checks(table, pk, rows, fields)
        found += _duplicate_keys(table, pk, src, keys)
        found += _fk_checks(net, table, pk, rows, [fk for fk in fks if fk.table == table])
    return CheckResult(tuple(found), truncated)


# ---------------------------------------------------------------- helpers


def _where(column: str, values: Sequence[Any]) -> Any:
    frozen = list(values)
    return lambda expr: expr.filter(expr[column].isin(frozen))


def _plain(value: Any) -> Any:
    """A pandas cell as a plain Python value (NA -> None)."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):  # a list or array cell: not a scalar NA
        pass
    return json_scalar(value)


def _few(values: Sequence[Any], n: int = 5) -> str:
    shown = ", ".join(str(v) for v in values[:n])
    return shown + (f" (+{len(values) - n} more)" if len(values) > n else "")


def _field_checks(table: str, pk: str, rows: list[dict[str, Any]], fields: Mapping[str, Any]) -> list[EditWarning]:
    out = []
    for row in rows:
        key = row[pk]
        for name, spec_field in fields.items():
            if name not in row:
                continue
            value, c = row[name], spec_field.constraints
            if value is None:
                if c is not None and c.required:
                    msg = f"{table} {key}: {name} is required but empty"
                    out.append(EditWarning("edit.required_empty", table, key, name, None, msg))
                continue
            if not fits_type(value, spec_field.type):
                msg = f"{table} {key}: {name} is {spec_field.type} in the spec; {value!r} is not"
                out.append(EditWarning("edit.type", table, key, name, value, msg))
            elif c is not None and c.enum and value not in c.enum:
                msg = f"{table} {key}: {name} {value!r} is not one of {_few(c.enum)}"
                out.append(EditWarning("edit.enum", table, key, name, value, msg))
    return out


def _duplicate_keys(table: str, pk: str, src: Any, keys: list[Any]) -> list[EditWarning]:
    counts = src.filter(_where(pk, keys)).select(pk).to_pandas()[pk].map(json_scalar).value_counts()
    return [
        EditWarning("edit.duplicate_key", table, k, pk, k, f"{table} {k}: {n} rows have this {pk}")
        for k, n in counts.items()
        if n > 1
    ]


def _fk_checks(net: Any, table: str, pk: str, rows: list[dict[str, Any]], fks: list[Any]) -> list[EditWarning]:
    out = []
    for fk in fks:
        values = sorted({r[fk.column] for r in rows if r.get(fk.column) is not None}, key=str)
        if not values:
            continue
        ref = net.tables[fk.ref_table].filter(_where(fk.ref_column, values)).select(fk.ref_column).to_pandas()
        present = {json_scalar(v) for v in ref[fk.ref_column]}
        for r in rows:
            value = r.get(fk.column)
            if value is not None and value not in present:
                msg = f"{table} {r[pk]}: {fk.column} {value!r} is not a {fk.ref_table}.{fk.ref_column}"
                out.append(EditWarning("edit.fk_missing", table, r[pk], fk.column, value, msg))
    return out
