"""Tests for the DuckDB FormatAdapter (Phase 1 task 1.9, issue #56).

DuckDB is the default API-download format per docs/architecture.md §6.1, so
this adapter's contract is high-stakes: it gates the URL→local-file path
for every API consumer. These tests pin:

* Identity (name / extensions / schemes / self-registration on import).
* probe() — extension + URL-scheme + never-raises.
* scan() — multi-table enumeration (a duckdb file holds many tables, unlike
  csv/parquet which hold one).
* read() — requires ``table=`` kwarg, delegates to the engine (which has
  the no-SQL relation path) rather than embedding SQL here.
* write() — same shape; round-trips through every engine.
* Dispatcher integration — both ``.duckdb`` extension and ``duckdb://``
  URL scheme route to this adapter.
* No raw SQL in the module itself (defensive cross-check on
  ``scripts/lint_no_sql.py``).
"""

from __future__ import annotations

import re
import shutil

import pytest
from corral.engines.ibis_engine import IbisEngine

# The module reference is used by test_no_raw_sql_in_module and the reload test.
from corral.io import dispatch
from corral.io import (
    duckdb_adapter as _duckdb_adapter_module,
)
from corral.io.base import FormatAdapter, ResourceRef
from corral.io.duckdb_adapter import DuckdbAdapter
from netstead.fixtures import leavenworth

LEAVENWORTH_DUCKDB = leavenworth.duckdb_path()


@pytest.fixture
def private_db(tmp_path) -> str:
    """A private copy of the Leavenworth .duckdb for tests that read through an engine.

    The engine opens the file read-write, which takes an exclusive cross-process lock; on the
    committed file that would block other xdist workers. ``scan()`` opens read-only and is fine.
    """
    return str(shutil.copyfile(LEAVENWORTH_DUCKDB, tmp_path / "leavenworth.duckdb"))


# All nine tables the Leavenworth fixture is documented to carry (see
# packages/netstead/netstead/fixtures/leavenworth/README.md). Set semantics so
# test_scan_returns_all_tables doesn't depend on alphabetic order if the
# adapter ever sorts differently.
EXPECTED_TABLES = {
    "node",
    "link",
    "geometry",
    "lane",
    "link_tod",
    "signal_controller",
    "time_set_definitions",
    "use_definition",
    "use_group",
}


# ---------------------------------------------------------------------------
# Engines available for the cross-engine matrix. Each entry is (name, factory).
# Missing optional deps (polars) are filtered out at collection time so the
# test still runs on a minimal install.
# ---------------------------------------------------------------------------


def _engine_factories():
    # DuckDB is the one compute engine; pandas/polars/arrow are I/O formats.
    return [("ibis", IbisEngine)]


ENGINE_PARAMS = _engine_factories()


# ---------------------------------------------------------------------------
# Identity + probe + registration
# ---------------------------------------------------------------------------


def test_self_registers_on_import() -> None:
    """Importing the module registers the adapter under name="duckdb"."""
    from corral.io import get_adapter

    adapter = get_adapter("duckdb")
    assert isinstance(adapter, DuckdbAdapter)


def test_identity_fields() -> None:
    """Name / extensions / schemes match the dispatcher's expectations."""
    a = DuckdbAdapter()
    assert a.name == "duckdb"
    assert a.extensions == ("duckdb",)
    assert a.schemes == ("duckdb",)


def test_probe_duckdb_extension(tmp_path) -> None:
    """probe() returns True for any .duckdb path (no file needs to exist)."""
    a = DuckdbAdapter()
    assert a.probe(str(tmp_path / "doesnotexist.duckdb")) is True
    assert a.probe("relative/path.duckdb") is True


def test_probe_duckdb_url_scheme() -> None:
    """probe() returns True for a duckdb:// URL even with no extension."""
    a = DuckdbAdapter()
    assert a.probe("duckdb://host/database") is True
    assert a.probe("duckdb:///abs/path/file.duckdb") is True


def test_probe_non_match_returns_false() -> None:
    """Other extensions / schemes / random strings yield False."""
    a = DuckdbAdapter()
    assert a.probe("foo.csv") is False
    assert a.probe("foo.parquet") is False
    assert a.probe("http://example.com/x") is False
    assert a.probe("") is False


def test_probe_never_raises() -> None:
    """probe() is required to be total; weird inputs must not crash dispatch."""
    a = DuckdbAdapter()
    # Each of these would be a legitimate failure mode if probe sniffed too
    # aggressively. The contract is "return False, never raise".
    for src in [None, 42, object(), b"bytes", {"weird": "dict"}, [1, 2, 3]]:
        try:
            result = a.probe(src)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - contract failure
            pytest.fail(f"probe({src!r}) raised {type(exc).__name__}: {exc}")
        assert result is False, f"probe({src!r}) returned True; expected False"


def test_protocol_conformance() -> None:
    """The adapter satisfies the runtime_checkable FormatAdapter protocol."""
    assert isinstance(DuckdbAdapter(), FormatAdapter)


# ---------------------------------------------------------------------------
# scan() — multi-table enumeration
# ---------------------------------------------------------------------------


def test_scan_returns_all_tables() -> None:
    """scan() enumerates every table in the duckdb file (Leavenworth = 9)."""
    a = DuckdbAdapter()
    # The engine arg is unused by scan() because table enumeration is a
    # metadata-only operation that talks to duckdb directly.
    listing = a.scan(str(LEAVENWORTH_DUCKDB), engine=None)  # type: ignore[arg-type]
    names = {ref.name for ref in listing}
    assert names == EXPECTED_TABLES, f"Got {names}, expected {EXPECTED_TABLES}"
    # Every ref must be a ResourceRef with the file::table sub-locator and the
    # adapter-name format tag, per io/base.ResourceRef docstring.
    for ref in listing:
        assert isinstance(ref, ResourceRef)
        assert ref.format == "duckdb"
        assert ref.path.startswith(str(LEAVENWORTH_DUCKDB))
        assert ref.path.endswith(f"::{ref.name}")


def test_scan_accepts_pathlib_path() -> None:
    """scan() accepts a pathlib.Path source as well as a string."""
    a = DuckdbAdapter()
    listing = a.scan(LEAVENWORTH_DUCKDB, engine=None)  # type: ignore[arg-type]
    assert {ref.name for ref in listing} == EXPECTED_TABLES


def test_scan_strips_duckdb_url_scheme() -> None:
    """scan('duckdb:///abs/path.duckdb') resolves to the file."""
    a = DuckdbAdapter()
    url = f"duckdb://{LEAVENWORTH_DUCKDB}"
    listing = a.scan(url, engine=None)  # type: ignore[arg-type]
    assert {ref.name for ref in listing} == EXPECTED_TABLES


# ---------------------------------------------------------------------------
# read() — table= kwarg required, delegates to engine
# ---------------------------------------------------------------------------


def test_read_requires_table_kwarg() -> None:
    """Calling read() without table= raises InvalidEngineCallError with a hint."""
    from corral.engines.errors import InvalidEngineCallError

    a = DuckdbAdapter()
    with pytest.raises(InvalidEngineCallError) as excinfo:
        a.read(str(LEAVENWORTH_DUCKDB), engine=IbisEngine())
    msg = str(excinfo.value)
    # The remediation hint must mention scan() so a confused caller (or AI
    # agent) knows how to discover the available tables.
    assert "table" in msg.lower()
    assert "scan" in msg.lower()


@pytest.mark.parametrize("engine_name,engine_cls", ENGINE_PARAMS)
def test_read_specific_table_delegates_to_engine(engine_name, engine_cls, private_db) -> None:
    """read(..., table='node') returns the engine's native expression type."""
    a = DuckdbAdapter()
    engine = engine_cls()
    try:
        expr = a.read(private_db, engine=engine, table="node")
        # Common interface: every engine implements to_pandas. Use that to
        # verify we got a real, usable expression for the right table.
        df = engine.to_pandas(expr)
        assert "node_id" in df.columns, (
            f"{engine_name}: expected 'node_id' column in node table; got {list(df.columns)}"
        )
        assert len(df) > 0, f"{engine_name}: node table came back empty"
    finally:
        if hasattr(engine, "close"):
            engine.close()


@pytest.mark.parametrize("engine_name,engine_cls", ENGINE_PARAMS)
def test_read_strips_table_kwarg_before_forwarding(engine_name, engine_cls, private_db) -> None:
    """Adapter must consume ``table=`` so engines don't see it as a double arg.

    Regression: a buggy adapter that forgot to ``kwargs.pop('table')`` would
    cause the engine's scan() to receive ``table=`` twice (once in the dict
    handle, once as a kwarg) — duckdb's relation API would either pick one
    or raise. Either way, we'd lose the contract guarantee.
    """
    a = DuckdbAdapter()
    engine = engine_cls()
    try:
        # If the kwarg leaked through, an engine using **kwargs would either
        # raise TypeError or fail to find the right table. The fact that
        # this works is the assertion.
        expr = a.read(private_db, engine=engine, table="link")
        df = engine.to_pandas(expr)
        assert "link_id" in df.columns
    finally:
        if hasattr(engine, "close"):
            engine.close()


# ---------------------------------------------------------------------------
# write() — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name,engine_cls", ENGINE_PARAMS)
def test_write_specific_table_roundtrip(engine_name, engine_cls, tmp_path, private_db) -> None:
    """scan → read(table) → write(table) → scan → equal table content.

    The polars engine intentionally does NOT support duckdb writes
    (its module docstring + ``write()`` body explicitly defer to
    IbisEngine — writing a duckdb table requires a CREATE TABLE
    statement which is banned from that module by the no-raw-SQL rule).
    We expect a NotImplementedError on the polars path and skip the
    roundtrip — the *adapter's* write contract still covers ibis +
    pandas, which is the broad cross-engine matrix. If polars ever
    grows a no-SQL duckdb write path, this skip becomes a real test
    automatically.
    """
    a = DuckdbAdapter()
    engine = engine_cls()
    out_path = tmp_path / f"out_{engine_name}.duckdb"
    try:
        # Source: read 'node' from leavenworth via the adapter.
        expr = a.read(private_db, engine=engine, table="node")
        df_in = engine.to_pandas(expr)

        if engine_name == "polars":
            # Confirm the documented deferral is in force; the adapter
            # correctly forwards the failure rather than masking it.
            # Post-#134: IbisEngine.write_duckdb_table raises
            # EngineNotAvailableError (the engine literally cannot do
            # this without raw SQL — structurally not-supported, not
            # not-yet-implemented).
            from corral.engines.errors import EngineNotAvailableError

            with pytest.raises(EngineNotAvailableError, match="duckdb"):
                a.write(expr, str(out_path), engine=engine, table="node")
            pytest.skip("polars engine defers duckdb writes to IbisEngine; see polars_engine.write_duckdb_table()")

        # Write to a new duckdb file under a chosen table name.
        a.write(expr, str(out_path), engine=engine, table="node")
        assert out_path.exists(), f"{engine_name}: write() did not create file"

        # Re-read via the adapter and confirm row count + column set.
        re_expr = a.read(str(out_path), engine=engine, table="node")
        df_out = engine.to_pandas(re_expr)

        assert len(df_in) == len(df_out), (
            f"{engine_name}: row count drift after roundtrip ({len(df_in)} -> {len(df_out)})"
        )
        assert set(df_in.columns) == set(df_out.columns), f"{engine_name}: column set drift after roundtrip"

        # Sanity: scan() finds the table we wrote.
        listing = a.scan(str(out_path), engine=None)  # type: ignore[arg-type]
        assert {ref.name for ref in listing} == {"node"}
    finally:
        if hasattr(engine, "close"):
            engine.close()


def test_write_requires_table_kwarg(tmp_path) -> None:
    """write() must also require table=, with a parallel error to read()."""
    from corral.engines.errors import InvalidEngineCallError

    a = DuckdbAdapter()
    engine = IbisEngine()
    # Build a trivial expression to write.
    expr = engine.scan({"data": [{"x": 1}, {"x": 2}]})
    out_path = tmp_path / "out.duckdb"
    with pytest.raises(InvalidEngineCallError) as excinfo:
        a.write(expr, str(out_path), engine=engine)
    assert "table" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


def test_dispatch_routes_duckdb_extension() -> None:
    """dispatch('x.duckdb') returns a DuckdbAdapter instance."""
    # Importing the module registers the adapter; clearing the registry would
    # blow that away, so we DON'T clear here. We rely on the side-effect of
    # importing duckdb_adapter at module top.
    assert _duckdb_adapter_module is not None  # silence unused-import linter
    adapter = dispatch("foo.duckdb")
    assert isinstance(adapter, DuckdbAdapter)


def test_dispatch_routes_duckdb_scheme() -> None:
    """dispatch('duckdb://foo.duckdb') routes by scheme, not extension."""
    adapter = dispatch("duckdb://foo")  # no extension at all
    assert isinstance(adapter, DuckdbAdapter)


# ---------------------------------------------------------------------------
# Defensive no-SQL check (cross-checks lint_no_sql.py from a test angle)
# ---------------------------------------------------------------------------


def test_no_raw_sql_in_module() -> None:
    """Module source must not embed raw SQL keywords (defensive sweep).

    The architecture-wide lint (``scripts/lint_no_sql.py``) is the
    authoritative check, but reproducing a narrow version here means a
    regression is caught at unit-test time too — and documents the rule
    for anyone reading the test file. Patterns mirror ``lint_no_sql.py``
    so a violation here implies a violation there.
    """
    import inspect

    source = inspect.getsource(_duckdb_adapter_module)
    sql_patterns = [
        r"\bSELECT\b[\s\S]{0,200}?\bFROM\b",
        r"\bINSERT\s+INTO\s+\w",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\bDELETE\s+FROM\s+\w",
        r"\bCREATE\s+TABLE\b",
        r"\bCREATE\s+OR\s+REPLACE\b",
        r"\bDROP\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
    ]
    for pat in sql_patterns:
        matches = re.findall(pat, source)
        # The pragma escape hatch is allowed for very narrow cases. If the
        # implementation needs it, the line will carry `# pragma: allow-sql`
        # — but our test still wants to see no MATCH outside such a line.
        unescaped = []
        for m in matches:
            # Find the offending line and check for the pragma.
            for line in source.splitlines():
                if m in line and "pragma: allow-sql" not in line:
                    unescaped.append((m, line.strip()))
                    break
        assert not unescaped, (
            f"Raw SQL detected in duckdb_adapter.py (pattern={pat!r}). "
            f"Offending lines: {unescaped}. "
            f"Use the duckdb Python relation API (con.table_function(...), "
            f"con.table(...).create(...), etc.) or mark a deliberate "
            f"exception with '# pragma: allow-sql' and a justifying comment."
        )


def test_module_registers_idempotently() -> None:
    """Re-importing the adapter module must not double-register or crash."""
    # Force a re-import to make sure the side-effect handles the
    # already-registered case gracefully.
    import importlib

    from corral.io import _REGISTRY

    importlib.reload(_duckdb_adapter_module)
    assert _REGISTRY["duckdb"].name == "duckdb"
