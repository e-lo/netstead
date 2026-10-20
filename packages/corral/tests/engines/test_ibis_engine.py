"""Behavioural tests for the IbisEngine (task 1.3).

Covers: default backend construction, scan() across csv/parquet/duckdb,
schema-driven type coercion, format override, materialize stability,
to_pandas / to_polars converters, write() round-trips, error messages
that point at the deferred-adapter tasks, and registry/protocol wiring.
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import Any

import ibis
import pytest
from corral.engines import Engine, get_engine
from corral.engines.base import EngineNotAvailableError
from corral.engines.ibis_engine import IbisEngine, backend_lock, serialize_backend
from corral.spec.loader import load_schema
from netstead.fixtures import leavenworth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> IbisEngine:
    """Fresh engine with a private in-memory duckdb connection."""
    e = IbisEngine()
    yield e
    e.close()


@pytest.fixture
def link_csv() -> Path:
    return leavenworth.csv_dir() / "link.csv"


@pytest.fixture
def link_parquet() -> Path:
    return leavenworth.parquet_dir() / "link.parquet"


@pytest.fixture
def duckdb_path(tmp_path: Path) -> Path:
    # A private copy: the engine opens .duckdb read-write, which takes an exclusive
    # cross-process lock that would block other xdist workers reading the committed file.
    return Path(shutil.copyfile(leavenworth.duckdb_path(), tmp_path / "leavenworth.duckdb"))


@pytest.fixture
def link_schema():
    """Frictionless schema for the GMNS link table.

    We load it from the vendored 0.97 spec so the test honours the same
    schema the production code does. ``shared_categories`` is mandatory
    because several link fields reference enum payloads via ``$ref``.
    """
    spec_root = Path(leavenworth.__file__).parent.parent.parent / "spec" / "0.97"
    import json

    shared = json.loads((spec_root / "shared_categories.json").read_text())
    return load_schema(spec_root / "link.schema.json", shared_categories=shared)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_creates_default_in_memory_connection():
    e = IbisEngine()
    try:
        assert e.con is not None
        assert e.name == "ibis"
    finally:
        e.close()


def test_accepts_existing_backend():
    """A caller can pass in their own backend; engine doesn't disconnect it."""
    con = ibis.duckdb.connect(":memory:")
    e = IbisEngine(con=con)
    try:
        assert e.con is con
    finally:
        e.close()
    # The engine should not have disconnected a backend it didn't own.
    # We can still list_tables() on it (empty, but the call succeeds).
    assert con.list_tables() == []
    con.disconnect()


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_scan_csv_returns_ibis_table(engine: IbisEngine, link_csv: Path):
    t = engine.scan(link_csv)
    assert isinstance(t, ibis.expr.types.Table)
    assert t.count().to_pyarrow().as_py() == 339


def test_scan_parquet_returns_ibis_table(engine: IbisEngine, link_parquet: Path):
    t = engine.scan(link_parquet)
    assert isinstance(t, ibis.expr.types.Table)
    assert t.count().to_pyarrow().as_py() == 339


def test_scan_duckdb_with_explicit_table(engine: IbisEngine, duckdb_path: Path):
    t = engine.scan(duckdb_path, table="link")
    assert isinstance(t, ibis.expr.types.Table)
    assert t.count().to_pyarrow().as_py() == 339


def test_scan_duckdb_dict_handle(engine: IbisEngine, duckdb_path: Path):
    t = engine.scan({"path": str(duckdb_path), "table": "node"})
    assert isinstance(t, ibis.expr.types.Table)
    assert "node_id" in t.columns


def test_scan_duckdb_without_table_kwarg_raises(engine: IbisEngine, duckdb_path: Path):
    # Post-#134: the duckdb adapter owns this validation. The message
    # still mentions the 'table=' kwarg so the caller can fix the call;
    # the exception is still a ValueError subclass (InvalidEngineCallError).
    with pytest.raises(ValueError, match=re.escape("table=")):
        engine.scan(duckdb_path)


def test_scan_with_format_override_csv(engine: IbisEngine, link_csv: Path):
    """An explicit format= still works when the extension already matches."""
    t = engine.scan(link_csv, format="csv")
    assert t.count().to_pyarrow().as_py() == 339


def test_scan_with_format_override_parquet(engine: IbisEngine, link_parquet: Path):
    t = engine.scan(link_parquet, format="parquet")
    assert t.count().to_pyarrow().as_py() == 339


def test_scan_with_schema_casts_types(engine: IbisEngine, link_csv: Path, link_schema):
    t = engine.scan(link_csv, schema=link_schema)
    s = t.schema()
    # GMNS link.from_node_id / to_node_id are Frictionless "integer".
    assert "int" in str(s["from_node_id"]).lower()
    assert "int" in str(s["to_node_id"]).lower()
    # length is "number" → float
    assert "float" in str(s["length"]).lower()
    # directed is "boolean"
    assert str(s["directed"]).lower() == "boolean"


def test_unsupported_format_raises_helpful_error(engine: IbisEngine):
    """Post-#134: dispatch via the FormatAdapter registry raises FormatNotDetected.

    Before the inversion the engine's own ``_resolve_kind`` raised
    ``NotImplementedError`` for unknown extensions. After the
    inversion the engine delegates to ``corral.io.dispatch``, which
    raises :class:`~corral.io.FormatNotDetected` (the registry's
    own structured exception) with the list of registered adapters
    so the caller can see what *is* available.
    """
    from corral.io import FormatNotDetected

    with pytest.raises(FormatNotDetected) as exc:
        engine.scan("foo.xlsx")
    msg = str(exc.value)
    # Lists what the registry knows about so the caller can correct
    # the call.
    assert "Registered adapters" in msg or "registered" in msg.lower()


def test_explicit_unsupported_format_raises(engine: IbisEngine):
    """Post-#134: an unknown explicit format= raises AdapterNotAvailableError."""
    from corral.io import AdapterNotAvailableError

    with pytest.raises(AdapterNotAvailableError) as exc:
        engine.scan("anything", format="excel")
    assert "excel" in str(exc.value)


# ---------------------------------------------------------------------------
# from_records / from_arrow — all-null column regression
# ---------------------------------------------------------------------------


def test_from_records_with_all_null_column_does_not_raise(engine: IbisEngine):
    """Regression: duckdb rejects ``CREATE TABLE`` with NULL-typed columns.

    Repro of the issue surfacing via ``netstead.scope.from_node(...).apply()``
    on the bundled Leavenworth fixture where ``link.name`` is entirely
    null. pyarrow infers ``pa.null()`` for such columns; duckdb raises
    ``IbisTypeError: DuckDB does not support creating tables with NULL
    typed columns``. ``IbisEngine.from_records`` now coerces all-null
    columns to ``string`` before the duckdb write — see
    ``_coerce_all_null_columns_to_string``.
    """
    expr = engine.from_records(
        [
            {"id": 1, "name": None},
            {"id": 2, "name": None},
        ]
    )
    df = engine.to_pandas(expr)
    assert len(df) == 2
    assert set(df.columns) == {"id", "name"}
    # The ``name`` column is castable to string after coercion — every
    # value still reads back as None / NA, just with a usable dtype.
    assert df["name"].isna().all()


def test_from_arrow_with_all_null_column_does_not_raise(engine: IbisEngine):
    """``from_arrow`` shares the same coercion — verify in parity with from_records."""
    import pyarrow as pa

    arrow = pa.Table.from_pylist([{"id": 1, "name": None}, {"id": 2, "name": None}])
    # Sanity-check the precondition the coercion fixes.
    assert pa.types.is_null(arrow.schema.field("name").type)
    expr = engine.from_arrow(arrow)
    df = engine.to_pandas(expr)
    assert len(df) == 2
    assert df["name"].isna().all()


# ---------------------------------------------------------------------------
# materialize / converters
# ---------------------------------------------------------------------------


def test_to_pandas_returns_pandas_dataframe(engine: IbisEngine, link_csv: Path):
    import pandas as pd

    df = engine.to_pandas(engine.scan(link_csv))
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 339


def test_to_polars_returns_polars_dataframe(engine: IbisEngine, link_csv: Path):
    pl = pytest.importorskip("polars")
    df = engine.to_polars(engine.scan(link_csv))
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 339


@pytest.mark.parametrize("table", ["link", "node", "geometry", "lane"])
def test_to_arrow_round_trips_leavenworth_parquet(engine: IbisEngine, table: str):
    """#116: ``to_arrow`` hands back the same Arrow table that was read, and ``from_arrow`` takes it back."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    def narrow(t: pa.DataType) -> pa.DataType:  # the fixture files store large_string; the engine reads string
        return {pa.large_string(): pa.string(), pa.large_binary(): pa.binary()}.get(t, t)

    source = leavenworth.parquet_dir() / f"{table}.parquet"
    on_disk = pq.read_table(source)
    expected = on_disk.cast(pa.schema([(f.name, narrow(f.type)) for f in on_disk.schema]))
    arrow = engine.to_arrow(engine.scan(source))
    assert isinstance(arrow, pa.Table)
    assert arrow.equals(expected)
    assert engine.to_arrow(engine.from_arrow(arrow)).equals(expected)


def test_to_arrow_keeps_awkward_types(engine: IbisEngine):
    """Arrow-native types survive ``from_arrow`` -> ``to_arrow`` without a pandas or records hop."""
    import datetime as dt
    from decimal import Decimal

    import pyarrow as pa

    arrow = pa.table(
        {
            "ts": pa.array([dt.datetime(2026, 1, 1, tzinfo=dt.UTC), None], pa.timestamp("us", tz="UTC")),
            "dec": pa.array([Decimal("1.25"), None], pa.decimal128(10, 2)),
            "bin": pa.array([b"a", None], pa.binary()),
            "lst": pa.array([[1, 2], None], pa.list_(pa.int64())),
            "n": pa.array([1, None], pa.int32()),
        }
    )
    assert engine.to_arrow(engine.from_arrow(arrow)).equals(arrow)


def test_to_polars_without_polars_raises_engine_not_available(engine: IbisEngine, link_csv: Path, monkeypatch):
    """Parity with IbisEngine: missing polars must raise EngineNotAvailableError.

    Per ``Engine`` protocol §9 ("structured exceptions"), the right
    failure mode for "an optional engine extra isn't installed" is the
    structured ``EngineNotAvailableError`` — not bare ``ImportError``.
    """
    scan = engine.scan(link_csv)

    def _raise(*_a, **_kw):
        raise ImportError("simulated polars missing")

    monkeypatch.setattr(type(scan), "to_polars", _raise)
    with pytest.raises(EngineNotAvailableError, match="polars"):
        engine.to_polars(scan)


def test_materialize_makes_values_stable(engine: IbisEngine, link_csv: Path):
    """Materialize twice and confirm both materializations agree on rows."""
    scan = engine.scan(link_csv)
    mat1 = engine.materialize(scan)
    mat2 = engine.materialize(scan)
    df1 = engine.to_pandas(mat1).sort_values("link_id").reset_index(drop=True)
    df2 = engine.to_pandas(mat2).sort_values("link_id").reset_index(drop=True)
    assert df1.equals(df2)
    assert len(df1) == 339


def test_materialize_returns_ibis_table(engine: IbisEngine, link_csv: Path):
    mat = engine.materialize(engine.scan(link_csv))
    assert isinstance(mat, ibis.expr.types.Table)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_write_csv_roundtrip(engine: IbisEngine, link_csv: Path, tmp_path: Path):
    out = tmp_path / "link_out.csv"
    engine.write(engine.scan(link_csv), out, "csv")
    assert out.exists()
    again = engine.scan(out)
    assert again.count().to_pyarrow().as_py() == 339


def test_write_parquet_roundtrip(engine: IbisEngine, link_csv: Path, tmp_path: Path):
    out = tmp_path / "link_out.parquet"
    engine.write(engine.scan(link_csv), out, "parquet")
    assert out.exists()
    again = engine.scan(out)
    assert again.count().to_pyarrow().as_py() == 339


def test_write_duckdb_roundtrip(engine: IbisEngine, link_csv: Path, tmp_path: Path):
    out = tmp_path / "out.duckdb"
    engine.write(engine.scan(link_csv), out, "duckdb", table="link")
    assert out.exists()
    again = engine.scan(out, table="link")
    assert again.count().to_pyarrow().as_py() == 339


def test_write_duckdb_stays_inside_duckdb(engine: IbisEngine, link_parquet: Path, tmp_path: Path, monkeypatch):
    """#128: the duckdb write never materialises the table in Python (no Arrow hop, no memtable)."""
    import ibis.expr.types as ir

    expected = engine.to_arrow(engine.scan(link_parquet))

    def _refuse(*_a, **_kw):
        raise AssertionError("write_duckdb_table materialised the table through Arrow")

    out = tmp_path / "out.duckdb"
    with monkeypatch.context() as m:
        m.setattr(ir.Table, "to_pyarrow", _refuse)
        m.setattr(ir.Table, "to_pyarrow_batches", _refuse)
        m.setattr(ibis, "memtable", _refuse)
        engine.write_duckdb_table(engine.scan(link_parquet), out, table="link")
    assert engine.to_arrow(engine.read_duckdb_table(out, table="link")).equals(expected)


def test_write_duckdb_replaces_an_existing_table_and_keeps_the_others(engine: IbisEngine, tmp_path: Path):
    out = tmp_path / "out.duckdb"
    engine.write_duckdb_table(engine.from_records([{"a": 1}]), out, table="t")
    engine.write_duckdb_table(engine.from_records([{"b": "x"}]), out, table="keep")
    engine.write_duckdb_table(engine.from_records([{"a": 2}, {"a": 3}]), out, table="t")
    assert engine.to_arrow(engine.read_duckdb_table(out, table="t")).to_pylist() == [{"a": 2}, {"a": 3}]
    assert engine.to_arrow(engine.read_duckdb_table(out, table="keep")).to_pylist() == [{"b": "x"}]


def test_write_duckdb_releases_the_destination(engine: IbisEngine, tmp_path: Path):
    """The destination is detached afterwards: the engine's catalogs are unchanged and the file can be moved."""
    con: Any = engine.con
    catalogs = set(con.list_catalogs())
    out = tmp_path / "out.duckdb"
    engine.write_duckdb_table(engine.from_records([{"a": 1}]), out, table="t")
    assert set(con.list_catalogs()) == catalogs
    moved = out.rename(tmp_path / "moved.duckdb")
    assert engine.to_arrow(engine.read_duckdb_table(moved, table="t")).to_pylist() == [{"a": 1}]


def test_write_duckdb_to_a_path_with_a_quote(engine: IbisEngine, tmp_path: Path):
    out = tmp_path / "o'brien" / "out.duckdb"
    out.parent.mkdir()
    engine.write_duckdb_table(engine.from_records([{"a": 1}]), out, table="t")
    assert engine.to_arrow(engine.read_duckdb_table(out, table="t")).to_pylist() == [{"a": 1}]


def test_write_duckdb_into_the_engines_own_database_file(tmp_path: Path):
    """An engine opened on a duckdb file can write a table back into that same file."""
    db = tmp_path / "own.duckdb"
    e = IbisEngine(con=ibis.duckdb.connect(str(db)))
    try:
        e.write_duckdb_table(e.from_records([{"a": 1}, {"a": 2}]), db, table="t")
        assert e.to_arrow(e.con.table("t")).to_pylist() == [{"a": 1}, {"a": 2}]
    finally:
        e.con.disconnect()


def test_write_unsupported_format_raises(engine: IbisEngine, link_csv: Path, tmp_path: Path):
    """Post-#134: unknown write fmt raises AdapterNotAvailableError from the registry."""
    from corral.io import AdapterNotAvailableError

    with pytest.raises(AdapterNotAvailableError) as exc:
        engine.write(engine.scan(link_csv), tmp_path / "x.xlsx", "xlsx")
    assert "xlsx" in str(exc.value)


# ---------------------------------------------------------------------------
# Registry / protocol
# ---------------------------------------------------------------------------


def test_engine_is_default_in_registry():
    # Compare by class name + module rather than isinstance: pytest's
    # `--import-mode=importlib` can load this module under two paths when
    # the engine source file is collected for doctests AND the test file
    # imports IbisEngine — producing two distinct class objects that both
    # satisfy the user's mental model of "is an ibis engine".
    e = get_engine()
    assert e.name == "ibis"
    assert type(e).__name__ == "IbisEngine"
    assert type(e).__module__ == "corral.engines.ibis_engine"


def test_protocol_conformance():
    e = IbisEngine()
    try:
        assert isinstance(e, Engine)
    finally:
        e.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_close_is_idempotent():
    e = IbisEngine()
    e.close()
    e.close()  # second call must not raise


# ---------------------------------------------------------------------------
# Thread safety: one shared connection, many threads
# ---------------------------------------------------------------------------


def _hammer(engine: IbisEngine, link_parquet: Path, link_csv: Path, rounds: int = 15) -> list[BaseException]:
    """Scan, materialize and execute on ``engine`` from several threads at once; return any errors."""
    errors: list[BaseException] = []
    start = threading.Barrier(4)

    def work(i: int) -> None:
        start.wait()
        try:
            for _ in range(rounds):
                if i % 2:
                    expr = engine.scan(link_parquet)
                    assert engine.count(expr) > 0
                    engine.to_pandas(expr.head(50))
                else:
                    expr = engine.materialize(engine.scan(link_csv))
                    expr.count().execute()  # direct ibis execution, bypassing the engine's methods
                    engine.from_records({"a": [1, 2, 3]}).to_pyarrow()
        except BaseException as exc:  # collected and asserted on by the caller
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_shared_engine_is_safe_across_threads(engine: IbisEngine, link_parquet: Path, link_csv: Path):
    assert _hammer(engine, link_parquet, link_csv) == []


def test_adopted_backend_is_serialized_too(link_parquet: Path, link_csv: Path):
    con = ibis.duckdb.connect()
    engine = IbisEngine(con=con)
    try:
        assert _hammer(engine, link_parquet, link_csv) == []
    finally:
        con.disconnect()


def test_serialize_backend_is_idempotent_and_exposes_its_lock():
    con = serialize_backend(ibis.duckdb.connect())
    try:
        lock = con._corral_lock
        wrapped = con.execute
        assert serialize_backend(con) is con and con._corral_lock is lock and con.execute is wrapped
        with backend_lock(con):  # re-entrant: the holder can still call the backend
            assert con.list_tables() == []
        assert backend_lock(object()).__enter__() is None  # no lock: a no-op context
    finally:
        con.disconnect()


def test_memtable_finalizer_skips_its_drop_while_a_query_holds_the_lock(monkeypatch):
    import corral.engines.ibis_engine as ie

    monkeypatch.setattr(ie, "_FINALIZER_WAIT_S", 0.05)
    con = ibis.duckdb.connect()
    dropped: list[str] = []
    # duckdb's own backend returns no finalizer; SQL backends that do return a "DROP" callable.
    con._make_memtable_finalizer = lambda name: lambda: dropped.append(name)
    serialize_backend(con)
    try:
        finalizer = con._make_memtable_finalizer("t")
        held, release = threading.Event(), threading.Event()

        def busy():  # stands in for a job thread mid-query when the interpreter exits
            with backend_lock(con):
                held.set()
                release.wait(5)

        worker = threading.Thread(target=busy)
        worker.start()
        assert held.wait(5)
        finalizer()  # gives up after the short wait instead of hanging
        release.set()
        worker.join()
        assert dropped == []
        finalizer()  # lock free: the drop runs
        assert dropped == ["t"]
    finally:
        con.disconnect()


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_to_pandas_matches_ibis_execute_for_every_leavenworth_table(engine: IbisEngine, fmt: str):
    import pandas as pd

    folder = leavenworth.csv_dir() if fmt == "csv" else leavenworth.parquet_dir()
    for path in sorted(folder.glob(f"*.{fmt}")):
        expr = engine.scan(path)
        pd.testing.assert_frame_equal(engine.to_pandas(expr), expr.execute().convert_dtypes(), obj=path.name)


def test_to_pandas_matches_ibis_execute_for_awkward_types(engine: IbisEngine):
    """Decimals, tz-aware timestamps, uuid / json values and nullable ints convert as ``execute`` does."""
    import datetime as dt
    import decimal

    import pandas as pd
    import pyarrow as pa

    utc = dt.UTC
    uid = "00000000-0000-0000-0000-000000000001"
    arrow = pa.table(
        {
            "dec": pa.array([decimal.Decimal("1.25"), None, decimal.Decimal("-3.50")], pa.decimal128(9, 2)),
            "ts": pa.array(
                [dt.datetime(2026, 1, 1, tzinfo=utc), None, dt.datetime(2026, 6, 1, 12, tzinfo=utc)],
                pa.timestamp("us", tz="UTC"),
            ),
            "n": pa.array([1, None, 3], pa.int64()),
            "n_full": pa.array([1, 2, 3], pa.int32()),
            "uid_text": pa.array([uid, None, uid]),
            "doc_text": pa.array(['{"a": 1}', None, "[1, 2]"]),
            "flag": pa.array([True, None, False]),
        }
    )
    expr = engine.from_arrow(arrow)
    expr = expr.mutate(uid=expr.uid_text.cast("uuid"), doc=expr.doc_text.cast("json"))
    pd.testing.assert_frame_equal(engine.to_pandas(expr), expr.execute().convert_dtypes())


def test_to_pandas_converts_after_releasing_the_backend_lock(engine: IbisEngine, link_parquet: Path, monkeypatch):
    from corral.engines import ibis_engine

    lock = backend_lock(engine.con)
    seen: list[bool] = []
    real = ibis_engine._arrow_to_pandas

    def probe(*args, **kwargs):
        def try_lock() -> None:  # another thread: can it take the lock right now?
            got = lock.acquire(blocking=False)
            if got:
                lock.release()
            seen.append(got)

        t = threading.Thread(target=try_lock)
        t.start()
        t.join()
        return real(*args, **kwargs)

    monkeypatch.setattr(ibis_engine, "_arrow_to_pandas", probe)
    engine.to_pandas(engine.scan(link_parquet))
    assert seen == [True]
