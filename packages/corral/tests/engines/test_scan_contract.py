"""Behavioural contract suite for :meth:`Engine.scan` (#141).

One class per invariant documented on :meth:`corral.engines.base.Engine.scan`, each parametrised over
every engine in the registry, so a new engine (or a change to the stock one) that drifts from the
docstring fails here rather than in a downstream caller:

* ``{"data": ...}`` is inline data and goes through :meth:`Engine.from_records`;
* ``{"format": "duckdb", "path": ..., "table": ...}`` (``format`` optional for a ``.duckdb`` path)
  goes through :meth:`Engine.read_duckdb_table`;
* any other dict shape raises :class:`UnsupportedSourceError` naming the supported shapes;
* for every other source, ``format`` reaches :func:`corral.io.dispatch` and ``schema`` plus
  ``**kwargs`` reach the resolved adapter's ``read`` verbatim.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from corral.engines import get_engine, list_engines
from corral.engines.base import Engine, UnsupportedSourceError
from corral.engines.errors import InvalidEngineCallError
from netstead.fixtures import leavenworth


@pytest.fixture(params=list_engines())
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    """A fresh instance of each registered engine (the registry holds shared singletons)."""
    fresh = type(get_engine(request.param))()
    yield fresh
    close = getattr(fresh, "close", None)
    if callable(close):
        close()


@pytest.fixture
def duckdb_path(tmp_path: Path) -> Path:
    # A private copy: opening a .duckdb read-write takes a cross-process lock other xdist workers would hit.
    return Path(shutil.copyfile(leavenworth.duckdb_path(), tmp_path / "leavenworth.duckdb"))


def _rows(engine: Engine, expr: Any) -> list[dict]:
    return engine.to_arrow(expr).to_pylist()


class _Spy:
    """Records the calls made to one engine method, then delegates to the real one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, engine: Engine, method: str) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        real = getattr(engine, method)

        def record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(engine, method, record)


class TestInlineDataGoesThroughFromRecords:
    def test_row_dicts(self, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = _Spy(monkeypatch, engine, "from_records")
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        assert _rows(engine, engine.scan({"data": rows})) == rows
        assert spy.calls == [((rows,), {"schema": None})]

    def test_columnar_dict(self, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = _Spy(monkeypatch, engine, "from_records")
        columns = {"a": [1, 2], "b": ["x", "y"]}
        assert _rows(engine, engine.scan({"data": columns})) == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        assert len(spy.calls) == 1

    def test_schema_is_forwarded_and_applied(self, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
        from corral.spec.model import Field, Schema

        spy = _Spy(monkeypatch, engine, "from_records")
        schema = Schema(fields=[Field(name="a", type="number")])
        expr = engine.scan({"data": [{"a": 1}]}, schema=schema)
        assert spy.calls[0][1] == {"schema": schema}
        assert isinstance(_rows(engine, expr)[0]["a"], float)


class TestDuckdbHandleGoesThroughReadDuckdbTable:
    def test_explicit_format(self, engine: Engine, duckdb_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = _Spy(monkeypatch, engine, "read_duckdb_table")
        expr = engine.scan({"format": "duckdb", "path": str(duckdb_path), "table": "node"})
        assert engine.count(expr) == engine.count(engine.read_duckdb_table(duckdb_path, table="node"))
        assert spy.calls[0][1]["table"] == "node"

    def test_format_implied_by_the_duckdb_suffix(
        self, engine: Engine, duckdb_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _Spy(monkeypatch, engine, "read_duckdb_table")
        engine.scan({"path": str(duckdb_path), "table": "node"})
        assert len(spy.calls) == 1

    def test_table_may_come_as_a_keyword(
        self, engine: Engine, duckdb_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _Spy(monkeypatch, engine, "read_duckdb_table")
        engine.scan({"format": "duckdb", "path": str(duckdb_path)}, table="link")
        assert spy.calls[0][1]["table"] == "link"

    def test_missing_table_is_an_invalid_call(self, engine: Engine, duckdb_path: Path) -> None:
        with pytest.raises(InvalidEngineCallError, match="table"):
            engine.scan({"format": "duckdb", "path": str(duckdb_path)})

    def test_missing_path_is_an_invalid_call(self, engine: Engine) -> None:
        with pytest.raises(InvalidEngineCallError, match="path"):
            engine.scan({"format": "duckdb", "table": "node"})


class TestUnknownDictShapeIsRejected:
    @pytest.mark.parametrize("source", [{}, {"rows": [{"a": 1}]}, {"format": "parquet", "path": "x.parquet"}])
    def test_raises_unsupported_source_naming_the_supported_shapes(self, engine: Engine, source: dict) -> None:
        with pytest.raises(UnsupportedSourceError) as excinfo:
            engine.scan(source)
        message = str(excinfo.value)
        assert "'data'" in message
        assert "duckdb" in message


class TestOtherSourcesDelegateToTheAdapterVerbatim:
    def test_format_schema_and_kwargs_reach_dispatch_and_read(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import corral.io

        seen: dict[str, Any] = {}
        sentinel = object()

        class _Adapter:
            def read(self, source: Any, *, engine: Engine, schema: Any = None, **kwargs: Any) -> Any:
                seen.update(source=source, engine=engine, schema=schema, kwargs=kwargs)
                return sentinel

        def _dispatch(source: Any, format: str | None = None) -> _Adapter:
            seen.update(dispatch_source=source, format=format)
            return _Adapter()

        monkeypatch.setattr(corral.io, "dispatch", _dispatch)
        schema = object()
        options = {"delim": ";", "header": True, "storage_options": {"anon": True}}
        result = engine.scan("some/where.txt", format="csv", schema=schema, **options)

        assert result is sentinel
        assert seen["dispatch_source"] == "some/where.txt"
        assert seen["format"] == "csv"
        assert seen["source"] == "some/where.txt"
        assert seen["engine"] is engine
        assert seen["schema"] is schema
        assert seen["kwargs"] == options
        assert options == {"delim": ";", "header": True, "storage_options": {"anon": True}}  # not mutated

    def test_a_real_file_round_trips(self, engine: Engine) -> None:
        source = leavenworth.csv_dir() / "node.csv"
        assert engine.count(engine.scan(source)) == engine.count(engine.read_csv(source))
