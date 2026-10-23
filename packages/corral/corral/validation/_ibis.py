"""Internal: ibis wrap + shared helpers for the validators.

The validators in this package use ibis predicates throughout
(``expr[col].is_null()``, ``expr.filter(...).count().execute()``,
``expr.left_join(...)``) so that violation counts get pushed down to
the duckdb backend instead of materializing entire tables into
pandas. At Bay-Area scale (millions of rows) this is the difference
between "count nulls in a column" being a millisecond SQL aggregate
and being a gigabyte pandas materialization.

To keep one codepath across the three engines, every validator entry
point calls :func:`to_ibis` once at the top, then operates on the
resulting :class:`ibis.expr.types.Table` for the rest of the call.

- ``IbisEngine`` (default) — pass-through. The expression already IS
  an ibis Table; we don't round-trip.
- ``IbisEngine`` — collect the LazyFrame to a polars DataFrame, hand
  it the pyarrow Table view, wrap with ``ibis.memtable``. The data
  was going to be materialized anyway (polars is the in-memory path).
- ``IbisEngine`` — convert the DataFrame to pyarrow then
  ``ibis.memtable``. Same reasoning — pandas is already eager.

The ibis duckdb backend then runs all rule predicates as SQL against
the registered memtable. For pandas/polars sources this is a single
pyarrow round-trip per validator orchestrator call (not per rule), so
the overhead is bounded.

Sample materialization for Issue enumeration goes via ``.to_pyarrow()``
+ ``.to_pylist()`` — never pandas — so this module is pandas-free.

This module is *also* the single home for the small ibis-shaped
helpers every validator needs (row-index attach, push-down count,
sample-as-pylist, source-row recovery, summary-issue builder). The
``schema_check`` and ``foreign_keys`` modules used to keep their own
copies; consolidating them here removes a ~70-LOC duplication and
gives the validator authors one obvious place to look.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import ibis

from corral.reports import Category, Issue, Severity

if TYPE_CHECKING:  # pragma: no cover - typing only
    import ibis.expr.types as ir


__all__ = [
    "MAX_ROW_ISSUES",
    "ROW_COL",
    "count",
    "emit_summary",
    "row_of",
    "sample",
    "to_ibis",
    "with_row_index",
]


# Per-rule row-Issue enumeration cap. Beyond this every validator
# emits one summary Issue plus the first ``MAX_ROW_ISSUES`` row-specific
# Issues. The cap protects callers (rich-console, HTML renderer) from
# million-issue reports while still letting a reviewer see concrete
# examples.
MAX_ROW_ISSUES: int = 100


# Internal column name used to surface the original row position into
# the materialized sample. We attach ``ibis.row_number()`` as this
# column inside :func:`with_row_index`; per-rule enumerators read it
# back from the pyarrow sample so ``Issue.row`` is the source-of-truth
# row index, not the sample's 0-based offset.
ROW_COL: str = "__dg_row__"


def to_ibis(expr: Any) -> ir.Table:
    """Return ``expr`` as an ibis :class:`Table`, converting if necessary.

    Pass-through for ibis tables (the default IbisEngine path).
    Polars LazyFrames and pandas DataFrames go via pyarrow into
    ``ibis.memtable``.

    Args:
        expr: Engine-native table expression — ibis Table, polars
            LazyFrame, or pandas DataFrame.

    Returns:
        An ibis Table backed by either the source backend (ibis
        pass-through) or the default in-memory ibis backend (memtable
        wrap).
    """
    # Ibis Table — pass-through. Cheapest path on the default engine.
    if isinstance(expr, ibis.expr.types.Table):
        return expr

    # Polars LazyFrame — collect to a polars DataFrame, hand its
    # pyarrow view to ibis.memtable. Polars frames don't expose
    # ``to_pyarrow`` directly on LazyFrame; collect first.
    try:
        import polars as pl

        if isinstance(expr, pl.LazyFrame):
            return ibis.memtable(expr.collect().to_arrow())
        if isinstance(expr, pl.DataFrame):
            return ibis.memtable(expr.to_arrow())
    except ImportError:  # pragma: no cover - polars not installed
        pass

    # Pandas DataFrame — duck-typed (no ``import pandas`` here, so the
    # validation package stays pandas-free per architecture §8). We
    # pyarrow-round-trip rather than calling ``ibis.memtable(df)``
    # directly because ibis's pandas-path inference drops integer
    # nullability (Int64 → float64), which would silently change
    # ``schema.type`` outcomes.
    if _looks_like_pandas_frame(expr):
        import pyarrow as pa

        return ibis.memtable(pa.Table.from_pandas(expr, preserve_index=False))

    # Last resort — let ibis try whatever it can with ``memtable``.
    # Any non-tabular object hits a clear ibis error here, not a
    # confusing AttributeError downstream.
    return ibis.memtable(expr)


def _looks_like_pandas_frame(obj: Any) -> bool:
    """Duck-type pandas DataFrame without importing pandas.

    Returns True for objects whose class name is ``DataFrame`` and
    whose module starts with ``pandas`` — the structural signature of
    a pandas DataFrame. This keeps this module free of any ``import
    pandas`` (architecture §8 — corral core paths are pandas-free;
    pandas is allowed only at user-facing converters).
    """
    cls = type(obj)
    return cls.__name__ == "DataFrame" and cls.__module__.startswith("pandas")


def with_row_index(table: ir.Table) -> ir.Table:
    """Attach a 0-based row-number column so enumerators carry positions.

    ibis ``row_number()`` is the SQL window function; the cast to
    int64 is defensive (ibis returns int64 universally but downstream
    code consumes the value as a plain ``int``).
    """
    if ROW_COL in table.columns:
        return table
    return table.mutate(**{ROW_COL: ibis.row_number().cast("int64")})


def count(predicate_table: ir.Table) -> int:
    """Push a ``COUNT(*)`` to the backend and return a plain int.

    Uses ``.to_pyarrow().as_py()`` rather than ``.execute()`` so the
    return type is statically inferrable as ``Any`` (which pyright
    accepts as an int) — ``.execute()`` is typed as
    ``DataFrame | Series | Scalar`` even though the scalar branch
    is the only one our usage hits.
    """
    return int(predicate_table.count().to_pyarrow().as_py())


def sample(predicate_table: ir.Table, *, limit: int = MAX_ROW_ISSUES) -> list[dict[str, Any]]:
    """Materialize up to ``limit`` rows of ``predicate_table`` as pylist dicts.

    The pyarrow path keeps us pandas-free; ``to_pylist`` is the
    cheapest stable way to iterate a small sample row-by-row.
    """
    arrow = predicate_table.limit(limit).to_pyarrow()
    return arrow.to_pylist()


def row_of(sample_row: dict[str, Any]) -> int:
    """Pull the source row index out of a sampled dict.

    Returns ``-1`` when the row dict carries no ``ROW_COL`` entry —
    the validators emit that as the literal row index in the Issue,
    making the failure mode obvious in renderers.
    """
    raw = sample_row.get(ROW_COL)
    # Defensive cast: arrow may yield numpy ints depending on backend.
    return int(raw) if raw is not None else -1


def emit_summary(
    *,
    severity: Severity,
    category: Category,
    code: str,
    table: str,
    column: str | None,
    total: int,
    message: str,
    sample_size: int = MAX_ROW_ISSUES,
    extra: dict[str, Any] | None = None,
) -> Issue:
    """Build the "showing first N of total" summary Issue.

    Unified across validators: ``extra["total_violations"]`` carries
    the full count; ``extra["sample_shown"]`` carries the cap.
    Renderers already understand both keys (HTML + JSON exercises pin
    the contract).
    """
    payload: dict[str, Any] = {"total_violations": total, "sample_shown": sample_size}
    if extra:
        payload.update(extra)
    return Issue(
        severity=severity,
        category=category,
        code=code,
        message=message,
        table=table,
        column=column,
        extra=payload,
    )
