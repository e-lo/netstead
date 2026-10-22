"""Server-side paged / sorted / filtered access to GMNS tables for the viewer.

The data-table exploration view browses the underlying GMNS tables (``link``,
``node``, …) beside the map. Paging, sorting and filtering happen here, on the
server, so the browser only ever holds one page — the same philosophy as the
binary network payload in :mod:`netstead.viz.buffers`.

The filter language (``[{"col", "op", "val"}, ...]``) is a small JSON predicate
spec, **never raw SQL**, compiled to column masks. It validates column and op
names (no injection surface) and maps cleanly onto the engine-agnostic corral
``Table.filter`` the day the viewer is handed a lazy ``Network`` instead of
materialized frames.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "FILTER_OPS",
    "GEOM_COLS",
    "MAX_FILTER_CONDITIONS",
    "MAX_IN_VALUES",
    "MAX_LIMIT",
    "TOKEN_OPS",
    "FilterError",
    "KeyTypeError",
    "coerce_filter_value",
    "coerce_keys",
    "column_dtype",
    "distinct_values",
    "filter_frame",
    "locate_row",
    "page_table",
    "parse_ids",
    "table_list_entry",
    "table_schema",
]

#: WKT geometry columns are huge; excluded from grid payloads (shown on the map).
GEOM_COLS = {"geometry", "geom", "wkt"}
#: Server-side cap on a single page.
MAX_LIMIT = 500


class FilterError(ValueError):
    """An invalid filter spec (unknown column or operator, or a value the operator cannot take)."""


def _as_list(v: Any) -> list:
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def _pair(v: Any) -> tuple[Any, Any]:
    """``between``'s ``[low, high]`` (:class:`FilterError` for anything else)."""
    if isinstance(v, (list, tuple)) and len(v) == 2 and None not in v:
        return v[0], v[1]
    raise FilterError("'between' takes two values: [low, high]")


def _tokens(s: pd.Series) -> pd.Series:
    """One row per comma-separated token of ``s``, stripped, indexed by the cell's *position* in ``s``.

    A null cell contributes one null token (which no value equals), so it never has a token.
    """
    return s.astype("string").str.split(",").reset_index(drop=True).explode().str.strip()


def _has_tokens(s: pd.Series, v: Any, *, every: bool) -> pd.Series:
    """Per cell of ``s``: whether its tokens include any (or, with ``every``, all) of the values in ``v``.

    Vectorised pandas string ops, never a regex and never per-row Python: the cells are split and exploded
    once, the matching tokens counted per cell.
    """
    wanted = {str(x) for x in _as_list(v)}
    tokens = _tokens(s)
    hits = tokens.where(tokens.isin(wanted))  # a token outside ``wanted`` becomes null, which nunique skips
    found = hits.groupby(level=0).nunique().reindex(range(len(s)), fill_value=0).to_numpy()
    mask = (found >= len(wanted)) & s.notna().to_numpy() if every else found > 0
    return pd.Series(mask, index=s.index)


_OPS = {
    "eq": lambda s, v: s == v,
    "ne": lambda s, v: s != v,
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
    "in": lambda s, v: s.isin(_as_list(v)),
    # A plain substring, never a regex: a bad pattern can't raise, and a catastrophic one can't hang the session.
    "contains": lambda s, v: s.astype("string").str.contains(str(v), case=False, na=False, regex=False),
    "isnull": lambda s, v: s.isna(),
    "notnull": lambda s, v: s.notna(),
    # Comma-separated categoricals (``allowed_uses = "auto,truck,walk"``): the cell's tokens against a list of
    # values (a bare value is a one-element list). A null cell matches none_of, never any_of/all_of.
    "any_of": lambda s, v: _has_tokens(s, v, every=False),
    "all_of": lambda s, v: _has_tokens(s, v, every=True),
    "none_of": lambda s, v: ~_has_tokens(s, v, every=False),
    # Inclusive numeric range, ``val = [low, high]``; a null cell never matches.
    "between": lambda s, v: s.between(*_pair(v)),
}
#: The filter operators every table filter accepts (the rows routes, and the Workbench's ``Filter``).
FILTER_OPS: tuple[str, ...] = tuple(_OPS)
#: The operators whose value is a list of tokens to look for in a comma-separated cell.
TOKEN_OPS: tuple[str, ...] = ("any_of", "all_of", "none_of")
#: Conditions accepted in one filter, and values in one list-valued (``in``, token) condition.
MAX_FILTER_CONDITIONS = 50
MAX_IN_VALUES = 10_000


def _scalar(v: Any) -> Any:
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return getattr(v, "item", lambda: v)()


def _column_kind(s: pd.Series) -> str:
    if s.name in GEOM_COLS:
        return "geom"
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_numeric_dtype(s):
        return "num"
    return "str"


def primary_key(name: str, columns: list[str]) -> str | None:
    """Best-effort primary key: ``<table>_id`` if present, else first ``*_id``."""
    cand = f"{name}_id"
    if cand in columns:
        return cand
    return next((c for c in columns if c.endswith("_id")), None)


def parse_ids(ids: str | None) -> list | None:
    """Parse a comma-separated ``ids`` querystring into ints (fallback: strings); ``None`` when empty."""
    if not ids:
        return None
    out: list = []
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            out.append(tok)
    return out or None


class KeyTypeError(ValueError):
    """An id that cannot be compared with its key column (``"abc"`` for an integer key)."""


_INT_TEXT = re.compile(r"[+-]?\d+")


def _as_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
        return int(value)
    raise KeyTypeError(f"{str(value)[:40]!r} is not an integer id")


def coerce_keys(values: Iterable[Any], dtype: Any) -> list[Any]:
    """``values`` as the key column's type, so an eager mask and a DuckDB ``IN`` agree.

    An integer key takes ints and digit strings (``"1"`` is ``1``); a string key takes text and
    ints (``1`` is ``"1"``). Any other key type (``object``, floats) passes the values through.

    Raises:
        KeyTypeError: a value that cannot be an integer id.

    >>> coerce_keys(["1", 2, " -3"], pd.Int64Dtype())
    [1, 2, -3]
    >>> coerce_keys([7, "a"], pd.StringDtype())
    ['7', 'a']
    """
    if pd.api.types.is_bool_dtype(dtype):
        return list(values)
    if pd.api.types.is_integer_dtype(dtype):
        return [_as_int(v) for v in values]
    if isinstance(dtype, pd.StringDtype):
        return [v if isinstance(v, str) else str(v) for v in values]
    return list(values)


def coerce_filter_value(value: Any, dtype: Any) -> Any:
    """``value`` as a filter compares it with a ``dtype`` column: a number for a numeric column, text for text.

    ``None`` passes through (``isnull``/``notnull`` take no value). A numeric column takes numbers and numeric text
    (``"2"`` is ``2``); a boolean column takes booleans and ``"true"``/``"false"``; a text column takes text and
    numbers (``2`` is ``"2"``). Any other column type passes the value through.

    Raises:
        FilterError: a value the column's type cannot hold.

    >>> coerce_filter_value("2", pd.Int64Dtype()), coerce_filter_value("2.5", np.dtype("float64"))
    (2, 2.5)
    >>> coerce_filter_value(3, pd.StringDtype())
    '3'
    """
    if value is None:
        return None
    if pd.api.types.is_bool_dtype(dtype):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise FilterError(f"{str(value)[:40]!r} is not true or false")
    if pd.api.types.is_numeric_dtype(dtype):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
            return int(value)
        try:
            number = float(value) if isinstance(value, str) else None
        except ValueError:
            number = None
        if number is None or not np.isfinite(number):
            raise FilterError(f"{str(value)[:40]!r} is not a number")
        return number
    if isinstance(dtype, pd.StringDtype) or pd.api.types.is_object_dtype(dtype):
        return value if isinstance(value, str) else str(value)
    return value


def column_dtype(src: Any, column: str) -> Any:
    """The pandas dtype ``column`` materialises as (one row sampled from a lazy table)."""
    return _schema_frame(src)[column].dtype


def _is_frame(src: Any) -> bool:
    """A source is pandas (eager) when it's a DataFrame; otherwise a lazy corral Table."""
    return isinstance(src, pd.DataFrame)


def columns_of(src: Any) -> list[str]:
    return list(src.columns) if _is_frame(src) else list(src.columns())


def rowcount_of(src: Any) -> int:
    return len(src) if _is_frame(src) else int(src.count())


def _schema_frame(src: Any) -> pd.DataFrame:
    """A tiny frame carrying each column's dtype for kind classification.

    For a lazy Table this materialises only one row (pushed to the engine), so
    classification never pulls a large table into memory.
    """
    return src if _is_frame(src) else src.limit(1).to_pandas()


def table_list_entry(name: str, src: Any) -> dict:
    """One ``/api/tables`` entry: name, row count, column names."""
    return {"name": name, "rows": rowcount_of(src), "columns": columns_of(src)}


def table_schema(name: str, src: Any, enums: Mapping[str, Sequence[Any]] | None = None) -> dict:
    """Column schema (name/dtype/kind/enum) + primary key + row count for a table.

    ``enums`` maps a column to the values its spec field allows (``constraints.enum``); every other
    column's ``enum`` is ``None``.
    """
    cols = columns_of(src)
    sample = _schema_frame(src)
    enums = enums or {}
    out = [
        {
            "name": c,
            "dtype": str(sample[c].dtype) if c in sample else "object",
            "kind": _column_kind(sample[c]) if c in sample else "str",
            "enum": list(enums[c]) if enums.get(c) else None,
        }
        for c in cols
    ]
    return {"name": name, "rows": rowcount_of(src), "primary_key": primary_key(name, cols), "columns": out}


def _distinct_column(src: Any, col: str) -> pd.Series:
    """The distinct values of ``col`` (nulls included), the distinct pushed to the engine for a lazy table."""
    if _is_frame(src):
        return src[col].drop_duplicates()
    import ibis

    column = src.select(col)
    if isinstance(column.expr, ibis.expr.types.Table):
        column = column.filter(lambda expr: expr.distinct())
    return column.to_pandas()[col].drop_duplicates()


def distinct_values(src: Any, col: str, limit: int = 200) -> dict:
    """The sorted distinct non-null values of ``col``, as strings, for a column-filter's value list.

    A text column whose values contain a comma is ``list_like``: the values are then its distinct
    comma-separated tokens (``"auto,truck"`` contributes ``auto`` and ``truck``), the tokens the
    ``any_of``/``all_of``/``none_of`` operators match. At most ``limit`` values (capped at
    :data:`MAX_LIMIT`) are returned, ``truncated`` saying whether more exist.

    Raises:
        FilterError: unknown column.

    >>> distinct_values(pd.DataFrame({"u": ["auto", "auto,truck", None]}), "u")
    {'values': ['auto', 'truck'], 'truncated': False, 'list_like': True}
    """
    if col not in columns_of(src):
        raise FilterError(f"unknown column {col!r}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    s = _distinct_column(src, col).dropna()
    list_like = False
    if _column_kind(s) == "str":
        text = s.astype("string")
        if text.str.contains(",", regex=False).any():
            list_like = True
            s = text.str.split(",").explode().str.strip()
            s = s[s.notna() & (s != "")]
    uniq = [_scalar(v) for v in s.unique()]
    try:
        uniq.sort()
    except TypeError:  # mixed types in an object column
        uniq.sort(key=str)
    values = [str(int(v)) if isinstance(v, float) and v.is_integer() else str(v) for v in uniq]  # 40.0 reads as 40
    return {"values": values[:limit], "truncated": len(values) > limit, "list_like": list_like}


def _apply_filter(df: pd.DataFrame, spec: list[dict]) -> pd.DataFrame:
    for cond in spec:
        col, op = cond.get("col"), cond.get("op")
        if col not in df.columns:
            raise FilterError(f"unknown column {col!r}")
        if op not in _OPS:
            raise FilterError(f"unknown operator {op!r}")
        df = df[_OPS[op](df[col], cond.get("val"))]
    return df


def filter_frame(df: pd.DataFrame, spec: list[dict]) -> pd.DataFrame:
    """The rows of ``df`` matching every condition in ``spec`` (raises :class:`FilterError`)."""
    return _apply_filter(df, spec)


def _clamp(offset: int, limit: int) -> tuple[int, int]:
    return max(0, int(offset)), max(1, min(int(limit), MAX_LIMIT))


def _rows_payload(page: pd.DataFrame, total: int, offset: int, limit: int) -> dict:
    cols = [c for c in page.columns if c not in GEOM_COLS]
    rows = [[_scalar(v) for v in rec] for rec in page[cols].itertuples(index=False, name=None)]
    return {"columns": cols, "rows": rows, "total": total, "offset": offset, "limit": limit}


def page_table(
    source: Any,
    *,
    offset: int = 0,
    limit: int = 100,
    sort: str | None = None,
    direction: str = "asc",
    filter_spec: list[dict] | None = None,
    ids: list | None = None,
    pk: str | None = None,
) -> dict:
    """Return one page of ``source`` as compact column/row arrays (geometry excluded).

    ``source`` is a pandas ``DataFrame`` (sliced in memory) or a lazy corral
    ``Table`` (filter/sort/page/count pushed to the engine — duckdb over parquet
    materialises only the one page). Same response shape either way.

    Raises:
        FilterError: unknown filter column/operator, or unknown sort column.
    """
    if not _is_frame(source):
        return _page_lazy(source, offset, limit, sort, direction, filter_spec, ids, pk)
    df = source
    if ids and pk and pk in df.columns:
        df = df[df[pk].isin(ids)]
    if filter_spec:
        df = _apply_filter(df, filter_spec)
    total = len(df)
    if sort:
        if sort not in df.columns:
            raise FilterError(f"unknown sort column {sort!r}")
        df = df.sort_values(sort, ascending=(direction != "desc"), kind="stable")
    offset, limit = _clamp(offset, limit)
    return _rows_payload(df.iloc[offset : offset + limit], total, offset, limit)


def locate_row(
    source: Any,
    key: Any,
    *,
    pk: str,
    sort: str | None = None,
    direction: str = "asc",
    filter_spec: list[dict] | None = None,
    ids: list | None = None,
) -> int | None:
    """Index of the row whose ``pk`` is ``key``, in the order :func:`page_table` pages ``source``.

    Only eager frames are located (the map-linked ``link``/``node`` tables); a lazy table, an
    unknown key column, or a row filtered out answers ``None``. Unlike ``page_table``, an empty
    ``ids`` list means "no rows".

    Raises:
        FilterError: unknown filter column/operator, or unknown sort column.

    >>> locate_row(pd.DataFrame({"id": [4, 2, 7]}), 7, pk="id", sort="id", direction="desc")
    0
    """
    if not _is_frame(source) or pk not in source.columns:
        return None
    df = source
    if ids is not None:
        df = df[df[pk].isin(ids)]
    if filter_spec:
        df = _apply_filter(df, filter_spec)
    if sort:
        if sort not in df.columns:
            raise FilterError(f"unknown sort column {sort!r}")
        df = df.sort_values(sort, ascending=(direction != "desc"), kind="stable")
    hits = np.flatnonzero((df[pk] == key).to_numpy(dtype=bool, na_value=False))
    return int(hits[0]) if len(hits) else None


def _page_lazy(
    table: Any,
    offset: int,
    limit: int,
    sort: str | None,
    direction: str,
    filter_spec: list[dict] | None,
    ids: list | None,
    pk: str | None,
) -> dict:
    """Paging over a lazy corral ``Table`` — pushes down to the engine."""
    from corral.dataset.filter import FilterSpecError, filter_rows

    cols_all = list(table.columns())
    try:
        if ids and pk and pk in cols_all:
            table = filter_rows(table, [{"col": pk, "op": "in", "val": list(ids)}])
        if filter_spec:
            table = filter_rows(table, filter_spec)
    except FilterSpecError as exc:
        raise FilterError(str(exc)) from exc
    total = int(table.count())
    if sort:
        if sort not in cols_all:
            raise FilterError(f"unknown sort column {sort!r}")
        table = table.order_by(sort, descending=(direction == "desc"))
    offset, limit = _clamp(offset, limit)
    keep = [c for c in cols_all if c not in GEOM_COLS]  # drop WKT before materialising
    page = table.select(*keep).limit(limit, offset).to_pandas()
    return _rows_payload(page, total, offset, limit)
