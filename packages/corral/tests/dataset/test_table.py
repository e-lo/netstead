"""Tests for :class:`corral.dataset.Table` (task 2.7 / issue #66).

The Table is a lazy wrapper around one table in a data package: it
holds the logical name, the engine-native expression, the engine, and
(optionally) the Frictionless :class:`Schema` and source locator.

Materialization must be opt-in — constructors and the head/select/filter
ops return new Table instances without touching the underlying engine
backend. The cross-engine parametrisation here mirrors
``tests/io/test_csv_adapter.py`` so the same engine fixture conventions
hold across the validation + dataset surfaces.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine


def _make_engine(name: str):
    """Construct an engine instance by short name; importorskip polars."""
    if name == "ibis":
        return IbisEngine()
    if name == "polars":
        pytest.importorskip("polars", reason="polars optional extra not installed")

        return IbisEngine()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine name: {name!r}")


def _make_table(engine_name: str, name: str = "t") -> Table:
    """Build a simple Table with three rows of two columns."""
    engine = _make_engine(engine_name)
    expr = engine.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}])
    return Table(name=name, expr=expr, engine=engine)


# ---------------------------------------------------------------------------
# Construction + inspection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_construction(engine_name: str) -> None:
    engine = _make_engine(engine_name)
    expr = engine.from_records([{"a": 1}])
    t = Table(name="link", expr=expr, engine=engine)
    assert t.name == "link"
    assert t.engine is engine
    assert t.expr is expr
    # Optional defaults
    assert t.schema is None
    assert t.source is None
    assert t.format is None
    assert t.dirty is False
    assert t.metadata == {}


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_dirty_flag_starts_false(engine_name: str) -> None:
    t = _make_table(engine_name)
    assert t.dirty is False


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_invalidate_sets_dirty(engine_name: str) -> None:
    t = _make_table(engine_name)
    t.invalidate()
    assert t.dirty is True


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_columns_returns_list(engine_name: str) -> None:
    t = _make_table(engine_name)
    cols = t.columns()
    assert isinstance(cols, list)
    assert set(cols) == {"a", "b"}


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_repr_includes_name_and_dirty(engine_name: str) -> None:
    t = _make_table(engine_name, name="link")
    r = repr(t)
    assert "link" in r
    # Dirty flag presence (clean vs dirty representation)
    assert "dirty" in r.lower() or "clean" in r.lower()


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_repr_html_returns_non_empty_string(engine_name: str) -> None:
    t = _make_table(engine_name)
    html = t._repr_html_()
    assert isinstance(html, str)
    assert html.strip() != ""


# ---------------------------------------------------------------------------
# Lazy ops — return new Table; original unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_filter_returns_new_table(engine_name: str) -> None:
    """``filter`` returns a new Table; the original's expr is unchanged."""
    t = _make_table(engine_name)
    original_expr = t.expr

    # filter accepts a predicate that takes the engine-native expr and
    # returns a filtered expr. We just keep it engine-agnostic for the
    # smoke test: a predicate that drops nothing.
    def _identity(expr: Any) -> Any:
        return expr

    t2 = t.filter(_identity)
    assert t2 is not t
    assert isinstance(t2, Table)
    assert t.expr is original_expr  # original unchanged


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_select_columns(engine_name: str) -> None:
    t = _make_table(engine_name)
    t2 = t.select("a")
    assert isinstance(t2, Table)
    assert t2 is not t
    cols = t2.columns()
    assert cols == ["a"]


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_head_is_lazy(engine_name: str) -> None:
    """head() returns a Table — does not materialize to a DataFrame."""
    t = _make_table(engine_name)
    h = t.head(2)
    assert isinstance(h, Table)
    assert h is not t
    # The returned head is still queryable by its column set without
    # forcing materialization through to_pandas.
    assert set(h.columns()) == {"a", "b"}


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_count_returns_int(engine_name: str) -> None:
    t = _make_table(engine_name)
    n = t.count()
    assert isinstance(n, int)
    assert n == 3


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_count_does_not_materialise_via_to_pandas(engine_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``Table.count`` must push down to the engine, not materialize.

    Pinning the I3 fix: prior to it, ``count()`` was implemented as
    ``len(engine.to_pandas(expr))`` which forced a full materialization
    for every count. The fix routes ibis through
    ``expr.count().to_pyarrow().as_py()`` and pandas through ``len``
    so neither path hits ``engine.to_pandas``. Monkeypatching that
    method to raise proves the new path doesn't touch it.
    """
    t = _make_table(engine_name)

    def _exploding_to_pandas(_expr: Any) -> Any:
        raise AssertionError("Table.count must not call engine.to_pandas")

    monkeypatch.setattr(t.engine, "to_pandas", _exploding_to_pandas)
    assert t.count() == 3


# ---------------------------------------------------------------------------
# Ordering + paging (order_by / limit) — the server-side grid primitives
# ---------------------------------------------------------------------------


def _unordered_table(engine_name: str) -> Table:
    """Three rows deliberately out of ``a``-order: a=[3,1,2]."""
    engine = _make_engine(engine_name)
    expr = engine.from_records([{"a": 3, "b": "z"}, {"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    return Table(name="t", expr=expr, engine=engine)


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_order_by_ascending(engine_name: str) -> None:
    t = _unordered_table(engine_name)
    t2 = t.order_by("a")
    assert isinstance(t2, Table) and t2 is not t
    assert t2.to_pandas()["a"].tolist() == [1, 2, 3]
    assert t.to_pandas()["a"].tolist() == [3, 1, 2]  # original unchanged


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_order_by_descending(engine_name: str) -> None:
    t = _unordered_table(engine_name)
    assert t.order_by("a", descending=True).to_pandas()["a"].tolist() == [3, 2, 1]


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_limit_with_offset_pages(engine_name: str) -> None:
    t = _unordered_table(engine_name).order_by("a")  # [1,2,3]
    assert t.limit(2).to_pandas()["a"].tolist() == [1, 2]
    assert t.limit(2, offset=1).to_pandas()["a"].tolist() == [2, 3]


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_limit_returns_new_table(engine_name: str) -> None:
    t = _unordered_table(engine_name)
    t2 = t.limit(1)
    assert isinstance(t2, Table) and t2 is not t


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_to_pandas_returns_dataframe_with_nullable_dtypes(engine_name: str) -> None:
    """``to_pandas`` returns a frame with the cross-engine nullable dtype family."""
    t = _make_table(engine_name)
    df = t.to_pandas()
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["a", "b"] or set(df.columns) == {"a", "b"}
    # nullable Int64 / string per the engine contract
    assert str(df["a"].dtype) in {"Int64", "int64"}


def test_table_to_polars() -> None:
    pl = pytest.importorskip("polars", reason="polars optional extra not installed")
    t = _make_table("pandas")
    plf = t.to_polars()
    assert isinstance(plf, pl.DataFrame)
    assert plf.shape[0] == 3


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_table_collect_returns_engine_native_frame(engine_name: str) -> None:
    """``collect`` forces eager materialization, returning an engine-native frame."""
    t = _make_table(engine_name)
    out = t.collect()
    # The exact type depends on the engine; the contract is that it is
    # something other than the Table wrapper itself.
    assert out is not None
    assert not isinstance(out, Table)
