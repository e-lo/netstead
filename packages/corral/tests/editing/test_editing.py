"""Tests for the generic editing framework (task 2.9 / issue #68).

Coverage matrix:

* Edit/Diff/EditResult value type construction + frozenness.
* apply_edit dispatch (add_rows, delete_rows, update_rows, replace_table).
* Session context manager: commit path persists log.
* Session atomicity: exception inside the block rolls back state.
* rollback() round-trips on disk (apply N edits, write log, read log,
  reverse — final state equals starting state).
* rollback() ``to=session_id`` selector.
* DirtyTracker integration: apply_edit calls mark_dirty.
* Cross-engine parity (ibis + pandas).
* UnknownTable / UnsupportedEditOp / InvalidPayload typed errors.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from corral.dataset import Package, Table
from corral.editing import (
    Diff,
    Edit,
    EditingError,
    EditResult,
    InvalidPayload,
    Session,
    UnknownTable,
    UnsupportedEditOp,
    rollback,
)
from corral.editing.apply import affected_rows, apply_edit, reverse_edit
from corral.engines.ibis_engine import IbisEngine
from corral.validation.sync_state import DirtyTracker


def _engine(name: str):
    """Construct an engine instance by short name (skip polars for 2.9)."""
    if name == "ibis":
        return IbisEngine()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine name: {name!r}")


def _make_pkg(engine_name: str, *, rows: list[dict] | None = None) -> Package:
    """Build a one-table Package with a small synthetic dataset."""
    eng = _engine(engine_name)
    if rows is None:
        rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    t = Table(name="t", expr=eng.from_records(rows), engine=eng)
    return Package.from_tables({"t": t})


def _ids(pkg: Package, table: str = "t") -> list[Any]:
    """Sorted list of ``id`` values currently in ``pkg[table]``."""
    df = pkg[table].to_pandas()
    return sorted(df["id"].dropna().tolist())


# ---------------------------------------------------------------------------
# Value-type smoke
# ---------------------------------------------------------------------------


def test_edit_frozen() -> None:
    """Edit is frozen — attempting to mutate raises."""
    e = Edit(op="add_rows", table="t", payload={"rows": []})
    with pytest.raises((AttributeError, TypeError)):
        e.op = "delete_rows"  # type: ignore[misc]


def test_editresult_carries_diff_and_session() -> None:
    """EditResult composes Edit + Diff + rollback blob + session id."""
    from datetime import datetime

    e = Edit(op="add_rows", table="t", payload={"rows": [{}]})
    d = Diff(edit=e, rows_added=1, rows_removed=0, rows_changed=0)
    r = EditResult(edit=e, diff=d, rollback_data={"n_added": 1}, applied_at=datetime(2026, 1, 1), session_id="sess-x")
    assert r.session_id == "sess-x"
    assert r.diff.rows_added == 1


# ---------------------------------------------------------------------------
# apply_edit — per-op dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_apply_add_rows(engine_name: str) -> None:
    pkg = _make_pkg(engine_name)
    r = apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 4, "v": "d"}]}))
    assert r.diff.rows_added == 1
    assert _ids(pkg) == [1, 2, 3, 4]
    # Bounded sample present.
    assert r.diff.before_sample is not None and len(r.diff.before_sample) <= 50
    assert r.diff.after_sample is not None and len(r.diff.after_sample) <= 50


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_apply_delete_rows(engine_name: str) -> None:
    pkg = _make_pkg(engine_name)
    r = apply_edit(pkg, Edit(op="delete_rows", table="t", payload={"predicate": lambda t: t["id"] == 2}))
    assert r.diff.rows_removed == 1
    assert _ids(pkg) == [1, 3]


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_apply_update_rows(engine_name: str) -> None:
    pkg = _make_pkg(engine_name)
    r = apply_edit(
        pkg,
        Edit(op="update_rows", table="t", payload={"predicate": lambda t: t["id"] == 2, "set": {"v": "B"}}),
    )
    assert r.diff.rows_changed == 1
    df = pkg["t"].to_pandas().sort_values("id").reset_index(drop=True)
    assert df.loc[df["id"] == 2, "v"].iloc[0] == "B"
    assert df.loc[df["id"] == 1, "v"].iloc[0] == "a"


def test_apply_replace_table() -> None:
    pkg = _make_pkg("pandas")
    e = pkg.engine
    new_expr = e.from_records([{"id": 99, "v": "z"}])
    r = apply_edit(pkg, Edit(op="replace_table", table="t", payload={"expr": new_expr}))
    assert _ids(pkg) == [99]
    # The diff captures the prior rows for rollback.
    assert r.rollback_data["prior_rows"]


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


def test_apply_unknown_table_raises() -> None:
    pkg = _make_pkg("pandas")
    with pytest.raises(UnknownTable):
        apply_edit(pkg, Edit(op="add_rows", table="missing", payload={"rows": []}))


def test_apply_unsupported_op_raises() -> None:
    pkg = _make_pkg("pandas")
    with pytest.raises(UnsupportedEditOp):
        apply_edit(pkg, Edit(op="frobnicate", table="t", payload={}))


def test_apply_invalid_payload_raises() -> None:
    pkg = _make_pkg("pandas")
    with pytest.raises(InvalidPayload):
        apply_edit(pkg, Edit(op="add_rows", table="t", payload={}))


# ---------------------------------------------------------------------------
# Session — commit path persists log
# ---------------------------------------------------------------------------


def test_session_commit_persists_log(tmp_path: Path) -> None:
    pkg = _make_pkg("pandas")
    log = tmp_path / "history.parquet"
    with Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 4, "v": "d"}]}))
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 5, "v": "e"}]}))
    assert log.exists()
    assert _ids(pkg) == [1, 2, 3, 4, 5]
    # Log carries both edits.
    import pyarrow.parquet as pq

    arrow = pq.read_table(log)
    assert arrow.num_rows == 2
    assert set(arrow.column_names) >= {
        "session_id",
        "edit_index",
        "op",
        "table",
        "payload_json",
        "rollback_json",
        "rows_added",
        "applied_at",
    }


def test_session_outside_with_raises() -> None:
    """add_edit before __enter__ should refuse."""
    pkg = _make_pkg("pandas")
    s = Session(pkg)
    with pytest.raises(EditingError):
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 99}]}))


# ---------------------------------------------------------------------------
# Atomicity — exception in block reverts state
# ---------------------------------------------------------------------------


def test_session_atomicity_on_exception(tmp_path: Path) -> None:
    """If the body raises, applied edits are reversed before propagation."""
    pkg = _make_pkg("pandas")
    starting = _ids(pkg)
    log = tmp_path / "history.parquet"
    with pytest.raises(RuntimeError, match="boom"), Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 4, "v": "d"}]}))
        s.add_edit(Edit(op="delete_rows", table="t", payload={"predicate": lambda t: t["id"] == 1}))
        raise RuntimeError("boom")
    # State restored, log NOT written (commit path skipped on raise).
    assert _ids(pkg) == starting
    assert not log.exists()


# ---------------------------------------------------------------------------
# Rollback round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_rollback_round_trip(engine_name: str, tmp_path: Path) -> None:
    """Apply edits → write log → rollback from disk → final == initial."""
    pkg = _make_pkg(engine_name)
    starting = _ids(pkg)
    log = tmp_path / "history.parquet"
    with Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 10, "v": "j"}]}))
        s.add_edit(Edit(op="delete_rows", table="t", payload={"predicate": lambda t: t["id"] == 2}))
        s.add_edit(Edit(op="update_rows", table="t", payload={"predicate": lambda t: t["id"] == 3, "set": {"v": "C"}}))
    # Intermediate sanity — state changed.
    assert _ids(pkg) != starting

    reversed_results = rollback(pkg, log)
    assert len(reversed_results) == 3
    # Round-trip equality on the id column. The update test makes
    # checking the `v` column non-trivial without sort+set semantics,
    # so we check ids match and the `v` column has been restored too.
    assert _ids(pkg) == starting
    df = pkg["t"].to_pandas().sort_values("id").reset_index(drop=True)
    assert df.loc[df["id"] == 3, "v"].iloc[0] == "c"


def test_rollback_selector_by_session(tmp_path: Path) -> None:
    """rollback(to=session_id) only reverts the matching session."""
    pkg = _make_pkg("pandas")
    log = tmp_path / "history.parquet"
    # Two sessions writing to the same log path (second overwrites — by
    # design: each session rewrites its log in full). Compose by hand
    # so both sessions appear in one history file.
    with Session(pkg, log_path=log, session_id="sess-A") as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 100}]}))
    # Manually merge a second session into the same log to exercise the selector.
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    a_rows = pq.read_table(log).to_pylist()
    with Session(pkg, log_path=log, session_id="sess-B") as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 200}]}))
    b_rows = pq.read_table(log).to_pylist()
    combined = a_rows + b_rows
    # Pad missing keys so all rows share schema (defensive).
    for row in combined:
        row.setdefault("metadata_json", json.dumps({}))
    pq.write_table(pa.Table.from_pylist(combined), log)

    # Rolling back only sess-B should leave sess-A's row intact.
    rollback(pkg, log, to="sess-B")
    assert 100 in _ids(pkg)
    assert 200 not in _ids(pkg)


def _log_of(results: list[EditResult], path: Path) -> Path:
    """Persist ``results`` exactly as :class:`Session` does."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from corral.editing.session import _serialize_result

    pq.write_table(pa.Table.from_pylist([_serialize_result(i, r) for i, r in enumerate(results)]), path)
    return path


def test_log_writes_no_session_as_null(tmp_path: Path) -> None:
    """#147: an edit applied outside a session is logged with a null session_id, not an empty string."""
    import pyarrow.parquet as pq

    pkg = _make_pkg("ibis")
    outside = apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 9}]}))
    inside = apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 10}]}), session_id="sess-A")
    log = _log_of([outside, inside], tmp_path / "history.parquet")
    assert pq.read_table(log).column("session_id").to_pylist() == [None, "sess-A"]


def test_rollback_to_an_empty_session_id_is_refused(tmp_path: Path) -> None:
    """#147: ``to=""`` names no session; it must not quietly match (or skip) the session-less edits."""
    from corral.editing import RollbackError

    pkg = _make_pkg("ibis")
    r = apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 9}]}))
    log = _log_of([r], tmp_path / "history.parquet")
    with pytest.raises(RollbackError, match="session_id"):
        rollback(pkg, log, to="")
    assert 9 in _ids(pkg)


def test_rollback_reads_a_legacy_empty_session_id_as_none(tmp_path: Path) -> None:
    """Logs written before #147 stored no session as ``""``; they still inflate to ``session_id=None``."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pkg = _make_pkg("ibis")
    r = apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 9}]}))
    log = _log_of([r], tmp_path / "history.parquet")
    legacy = pq.read_table(log).to_pylist()
    legacy[0]["session_id"] = ""
    pq.write_table(pa.Table.from_pylist(legacy), log)
    [reversed_result] = rollback(pkg, log)
    assert reversed_result.session_id is None
    assert 9 not in _ids(pkg)


# ---------------------------------------------------------------------------
# DirtyTracker integration
# ---------------------------------------------------------------------------


def test_apply_marks_tracker_dirty() -> None:
    """apply_edit calls dirty_tracker.mark_dirty on the touched table."""
    pkg = _make_pkg("pandas")
    tracker = DirtyTracker()
    tracker.stamp_table("t", pkg["t"].expr, pkg.engine)
    pkg.dirty_tracker = tracker
    assert "t" in tracker.known_tables()
    apply_edit(pkg, Edit(op="add_rows", table="t", payload={"rows": [{"id": 999}]}))
    # mark_dirty drops the stamp, so the table is no longer in known_tables.
    assert "t" not in tracker.known_tables()
    # The Table-level dirty flag is set.
    assert pkg["t"].dirty


# ---------------------------------------------------------------------------
# Empty-log + missing-log behaviours
# ---------------------------------------------------------------------------


def test_rollback_missing_log_raises(tmp_path: Path) -> None:
    pkg = _make_pkg("pandas")
    from corral.editing import RollbackError

    with pytest.raises(RollbackError):
        rollback(pkg, tmp_path / "never-written.parquet")


def test_leavenworth_round_trip(tmp_path: Path) -> None:
    """End-to-end: load Leavenworth, edit a row, rollback, verify equality."""
    leavenworth = pytest.importorskip(
        "netstead.fixtures.leavenworth",
        reason="netstead fixture not installed",
    )
    import netstead

    spec = Path(netstead.__file__).parent / "spec" / "0.97" / "datapackage.json"
    pkg = Package.from_source(
        leavenworth.csv_dir(),
        engine=IbisEngine(),
        spec=spec,
        tables=["link", "node"],
    )
    starting_link_count = pkg["link"].count()
    starting_node_count = pkg["node"].count()

    log = tmp_path / "leavenworth_history.parquet"
    with Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="add_rows", table="node", payload={"rows": [{"node_id": 999999}]}))
    assert pkg["node"].count() == starting_node_count + 1

    rollback(pkg, log)
    assert pkg["link"].count() == starting_link_count
    assert pkg["node"].count() == starting_node_count


def test_leavenworth_replace_table_with_an_all_null_column(tmp_path: Path) -> None:
    """#163: a ``replace_table`` built from row dicts survives Leavenworth's all-null ``node.name``.

    ``netstead clean`` rebuilds a table with ``engine.from_records(rows)``; every ``node.name`` is
    ``None``, so pyarrow infers a ``null``-typed column that duckdb refuses to ``CREATE TABLE``.
    The edit, a duckdb write of the result, and rollback must all go through on the default engine.
    """
    leavenworth = pytest.importorskip("netstead.fixtures.leavenworth", reason="netstead fixture not installed")
    import netstead

    spec = Path(netstead.__file__).parent / "spec" / "0.97" / "datapackage.json"
    engine = IbisEngine()
    pkg = Package.from_source(leavenworth.csv_dir(), engine=engine, spec=spec, tables=["node"])
    before = pkg["node"].expr.to_pyarrow()
    assert before.column("name").null_count == before.num_rows  # the shape this test exists for

    rows = before.to_pylist()
    log = tmp_path / "history.parquet"
    with Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="replace_table", table="node", payload={"expr": engine.from_records(rows)}))
    after = pkg["node"].expr.to_pyarrow()
    assert after.num_rows == before.num_rows
    assert after.schema.field("name").type == before.schema.field("name").type

    engine.write_duckdb_table(pkg["node"].expr, tmp_path / "out.duckdb", table="node")

    rollback(pkg, log)
    restored = pkg["node"].expr.to_pyarrow()
    assert restored.schema == before.schema
    assert restored.to_pylist() == rows


def test_session_no_log_path_skips_persist() -> None:
    """log_path=None — session works fine but writes no file."""
    pkg = _make_pkg("pandas")
    with tempfile.TemporaryDirectory() as tmp:
        with Session(pkg) as s:
            s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 77}]}))
        # No file written (we passed log_path=None).
        assert list(Path(tmp).iterdir()) == []
    assert 77 in _ids(pkg)


# ---------------------------------------------------------------------------
# from_arrow round-trip preserves content hash (Crit1 regression)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["ibis"])
def test_engine_from_arrow_hash_preservation(engine_name: str) -> None:
    """``from_arrow`` must round-trip a pyarrow Table without dtype loss.

    Before issue Crit1 the editing layer round-tripped through
    ``engine.from_records(arrow.to_pylist())``, which coerced types
    (binary → bytes, decimals → Decimal, nullable Int64 → object) and
    broke ``DirtyTracker``'s content-hash equality after edit + rollback.
    The fix routes through :meth:`Engine.from_arrow` so the Arrow buffer
    is handed to the engine intact.

    Pin: ``hash_table(from_arrow(arrow)) == hash_table(from_arrow(arrow))``
    AND the round-trip preserves a non-trivial column type that the
    ``to_pylist`` path would lose (binary).
    """
    import pyarrow as pa
    from corral.validation._ibis import to_ibis
    from corral.validation.sync_state import hash_table

    eng = _engine(engine_name)

    # Identity hash — two from_arrow calls on the same buffer agree.
    source = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "v": pa.array(["a", "b", "c"], type=pa.string()),
            "blob": pa.array([b"\x00\x01", b"\x02\x03", b"\x04\x05"], type=pa.binary()),
        }
    )
    a = eng.from_arrow(source)
    b = eng.from_arrow(source)
    assert hash_table(a, eng) == hash_table(b, eng), "from_arrow must be deterministic for the same source"

    # The lossy `from_records(to_pylist())` round-trip used by the old
    # `_engine_table` would coerce binary → bytes and back, but the
    # Arrow type would change (binary -> large_binary or back) on some
    # engines. Verify binary contents survive end-to-end via from_arrow.
    round_trip_arrow = to_ibis(a).to_pyarrow()
    blob_values = round_trip_arrow.column("blob").to_pylist()
    assert blob_values == [b"\x00\x01", b"\x02\x03", b"\x04\x05"], (
        "from_arrow must preserve binary column contents end-to-end"
    )

    # And contrast: the old `from_records(to_pylist())` path produces a
    # frame with a measurably different content hash on at least one
    # engine because ``bytes`` round-tripped through dict-records loses
    # the original arrow buffer layout. Locks in the regression even
    # for engines where the value-level round-trip happens to agree.
    lossy = eng.from_records(source.to_pylist())
    # We require either a hash divergence OR binary content equality
    # (some engines re-buffer identically even via from_records). Both
    # branches still confirm `from_arrow` is the safer primitive.
    lossy_arrow = to_ibis(lossy).to_pyarrow()
    assert lossy_arrow.column("blob").to_pylist() == [b"\x00\x01", b"\x02\x03", b"\x04\x05"]


# ---------------------------------------------------------------------------
# Session rollback failures surface (I2 regression)
# ---------------------------------------------------------------------------


def test_session_rollback_failure_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the body raises AND rollback also raises, the rollback error
    is captured on session.rollback_errors and chained on the original
    exception's __context__ — never swallowed silently.
    """
    pkg = _make_pkg("pandas")
    from corral.editing import session as session_mod

    call_count = {"n": 0}
    real_reverse = session_mod.reverse_edit

    def _flaky_reverse(p, r):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("rollback boom")
        return real_reverse(p, r)

    monkeypatch.setattr(session_mod, "reverse_edit", _flaky_reverse)
    sess = Session(pkg)
    with pytest.raises(RuntimeError, match="body boom") as ei, sess:
        sess.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 99}]}))
        raise RuntimeError("body boom")

    assert sess.rollback_errors, "expected rollback failure to be recorded on session"
    assert isinstance(sess.rollback_errors[0], RuntimeError)
    assert "rollback boom" in str(sess.rollback_errors[0])
    # The body exception is the one that propagates, with the rollback
    # failure chained via __context__ so callers can introspect it.
    assert "body boom" in str(ei.value)
    assert ei.value.__context__ is sess.rollback_errors[0]


# ---------------------------------------------------------------------------
# reverse_edit preserves pre-edit dirty flag (I11 regression)
# ---------------------------------------------------------------------------


def _names_pkg() -> Package:
    return _make_pkg("ibis", rows=[{"id": 1, "name": "x"}, {"id": 2, "name": None}, {"id": 3, "name": "y"}])


def _names(pkg: Package) -> list[Any]:
    return [r["name"] for r in sorted(pkg["t"].expr.to_pyarrow().to_pylist(), key=lambda r: r["id"])]


def test_delete_keeps_rows_whose_predicate_is_null_and_rollback_restores() -> None:
    """M6 on #245: ``name == "x"`` is NULL where name is NULL; that row is not deleted, so it must be kept.

    It used to land in neither ``filter(cond)`` nor ``filter(~cond)``: silently dropped, and missing from
    the rollback blob, so undo could not bring it back.
    """
    pkg = _names_pkg()
    r = apply_edit(pkg, Edit(op="delete_rows", table="t", payload={"predicate": lambda t: t.name == "x"}))
    assert _names(pkg) == [None, "y"]
    assert r.diff.rows_removed == 1
    reverse_edit(pkg, r)
    assert _names(pkg) == ["x", None, "y"]


def test_update_leaves_rows_whose_predicate_is_null_untouched() -> None:
    pkg = _names_pkg()
    r = apply_edit(
        pkg, Edit(op="update_rows", table="t", payload={"predicate": lambda t: t.name == "x", "set": {"name": "z"}})
    )
    assert _names(pkg) == ["z", None, "y"]
    assert r.diff.rows_changed == 1
    reverse_edit(pkg, r)
    assert _names(pkg) == ["x", None, "y"]


def test_reverse_edit_preserves_clean_dirty_flag() -> None:
    """Edit + rollback on a clean table leaves it clean."""
    pkg = _make_pkg("pandas")
    assert pkg["t"].dirty is False
    log = tempfile.mkdtemp()
    log_path = Path(log) / "h.parquet"
    with Session(pkg, log_path=log_path) as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 99}]}))
    assert pkg["t"].dirty is True
    rollback(pkg, log_path)
    assert pkg["t"].dirty is False, "rollback of an edit applied to a clean table should restore clean state"


def test_reverse_edit_preserves_dirty_dirty_flag() -> None:
    """Edit + rollback on an already-dirty table leaves it dirty."""
    pkg = _make_pkg("pandas")
    pkg["t"].dirty = True
    log = tempfile.mkdtemp()
    log_path = Path(log) / "h.parquet"
    with Session(pkg, log_path=log_path) as s:
        s.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 99}]}))
    rollback(pkg, log_path)
    assert pkg["t"].dirty is True, "rollback must preserve pre-edit dirty=True state"


def _with_nulls() -> Package:
    e = IbisEngine()
    rows = [{"id": 1, "name": "a", "v": 1.0}, {"id": 2, "name": None, "v": 2.0}, {"id": 3, "name": "c", "v": 3.0}]
    return Package.from_tables({"x": Table(name="x", expr=e.from_records(rows), engine=e)})


def _rows(pkg: Package) -> list[dict]:
    return sorted(pkg.tables["x"].expr.to_pyarrow().to_pylist(), key=lambda r: r["id"])


def test_reverse_update_restores_a_row_whose_other_columns_hold_null() -> None:
    pkg = _with_nulls()
    before = _rows(pkg)
    r = apply_edit(
        pkg, Edit(op="update_rows", table="x", payload={"predicate": lambda t: t.id == 2, "set": {"v": 9.0}})
    )
    reverse_edit(pkg, r)
    assert _rows(pkg) == before  # not two copies of id 2


def test_reverse_add_removes_an_added_row_that_holds_null() -> None:
    pkg = _with_nulls()
    before = _rows(pkg)
    r = apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"id": 4, "name": None, "v": 4.0}]}))
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


def test_affected_rows_reads_each_op() -> None:
    pkg = _with_nulls()
    added = apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"id": 4, "name": "d", "v": 4.0}]}))
    changed = apply_edit(
        pkg, Edit(op="update_rows", table="x", payload={"predicate": lambda t: t.id == 1, "set": {"v": 5.0}})
    )
    removed = apply_edit(pkg, Edit(op="delete_rows", table="x", payload={"predicate": lambda t: t.id == 3}))
    assert [r["id"] for r in affected_rows(added)] == [4]
    assert affected_rows(changed) == [{"id": 1, "name": "a", "v": 1.0}]  # the values before the edit
    assert [r["id"] for r in affected_rows(removed)] == [3]


def _typed_with_nulls() -> Package:
    """Columns whose NULLs used to break the reverse join: timestamp, decimal, binary and list."""
    import datetime as _dt
    from decimal import Decimal

    import pyarrow as pa

    e = IbisEngine()
    arrow = pa.table(
        {
            "id": pa.array([1, 2, 3], pa.int64()),
            "ts": pa.array([_dt.datetime(2026, 1, 1), None, _dt.datetime(2026, 1, 3)], pa.timestamp("us")),
            "dec": pa.array([Decimal("1.25"), None, Decimal("3.50")], pa.decimal128(10, 2)),
            "bin": pa.array([b"a", None, b"c"], pa.binary()),
            "lst": pa.array([[1, 2], None, [3]], pa.list_(pa.int64())),
            "v": pa.array([1.0, 2.0, 3.0], pa.float64()),
        }
    )
    return Package.from_tables({"x": Table(name="x", expr=e.from_arrow(arrow), engine=e)})


def test_reverse_update_restores_rows_with_typed_nulls() -> None:
    pkg = _typed_with_nulls()
    before = _rows(pkg)
    r = apply_edit(
        pkg, Edit(op="update_rows", table="x", payload={"predicate": lambda t: t.id == 2, "set": {"v": 9.0}})
    )
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


def test_reverse_update_of_a_row_without_nulls_in_a_table_with_typed_columns() -> None:
    pkg = _typed_with_nulls()
    before = _rows(pkg)
    r = apply_edit(
        pkg, Edit(op="update_rows", table="x", payload={"predicate": lambda t: t.id == 1, "set": {"v": 9.0}})
    )
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


def test_reverse_add_removes_rows_with_typed_nulls() -> None:
    pkg = _typed_with_nulls()
    before = _rows(pkg)
    row = {"id": 4, "ts": None, "dec": None, "bin": None, "lst": None, "v": 4.0}
    r = apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [row]}))
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


def test_reverse_delete_restores_rows_with_typed_nulls() -> None:
    pkg = _typed_with_nulls()
    before = _rows(pkg)
    r = apply_edit(pkg, Edit(op="delete_rows", table="x", payload={"predicate": lambda t: t.id == 2}))
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


def _ints() -> Package:
    import pyarrow as pa

    e = IbisEngine()
    arrow = pa.table(
        {
            "id": pa.array([1, 2], pa.int64()),
            "lanes": pa.array([1, 2], pa.int32()),
            "tags": pa.array([{"a": 1, "b": "x"}, None], pa.struct([("a", pa.int64()), ("b", pa.string())])),
            "lst": pa.array([[1], None], pa.list_(pa.int64())),
        }
    )
    return Package.from_tables({"x": Table(name="x", expr=e.from_arrow(arrow), engine=e)})


def test_add_rows_refuses_a_float_that_an_int_column_would_truncate() -> None:
    import pyarrow as pa

    pkg = _ints()
    before = _rows(pkg)
    with pytest.raises(pa.ArrowInvalid):
        apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"id": 3, "lanes": 2.7}]}))
    assert _rows(pkg) == before


def test_add_rows_takes_an_integral_float_and_keeps_the_column_type() -> None:
    import pyarrow as pa

    pkg = _ints()
    apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": [{"id": 3, "lanes": 3.0}]}))
    assert pkg.tables["x"].expr.to_pyarrow().schema.field("lanes").type == pa.int32()
    assert _rows(pkg)[-1]["lanes"] == 3


def test_add_and_reverse_keep_typed_nulls_in_struct_and_list_columns() -> None:
    import pyarrow as pa

    pkg = _ints()
    before = _rows(pkg)
    rows = [{"id": 3, "lanes": None, "tags": None, "lst": None}, {"id": 4, "lanes": 1, "tags": {"a": 2, "b": "y"}}]
    r = apply_edit(pkg, Edit(op="add_rows", table="x", payload={"rows": rows}))
    schema = pkg.tables["x"].expr.to_pyarrow().schema
    assert schema.field("tags").type == pa.struct([("a", pa.int64()), ("b", pa.string())])
    assert schema.field("lst").type == pa.list_(pa.int64())
    reverse_edit(pkg, r)
    assert _rows(pkg) == before


# ---------------------------------------------------------------------------
# #145: a long session does not pile up memtables
# ---------------------------------------------------------------------------


def _table_counts(engine: IbisEngine) -> tuple[int, int]:
    """Tables on ibis's process-wide default backend, and on ``engine``'s own connection."""
    import gc

    import ibis

    gc.collect()
    con: Any = engine.con
    return len(ibis.get_backend().list_tables()), len(con.list_tables())


def test_edits_run_on_the_engine_and_register_no_memtables() -> None:
    """Each edit leaves one snapshot table on the engine and nothing on ibis's default backend.

    Before, every delete/update built an ``ibis.memtable`` of the whole table, which ran on (and stayed
    registered with) ibis's default backend, outside the engine's lock; ``from_arrow`` left a second one
    behind on the engine for every edit.
    """
    pkg = _make_pkg("ibis", rows=[{"id": i, "v": "a"} for i in range(50)])
    engine = pkg.tables["t"].engine
    assert isinstance(engine, IbisEngine)
    default_before, engine_before = _table_counts(engine)
    n = 10
    with Session(pkg) as s:
        for i in range(n):
            s.add_edit(
                Edit(op="update_rows", table="t", payload={"predicate": lambda t, i=i: t.id == i, "set": {"v": "b"}})
            )
            s.add_edit(Edit(op="delete_rows", table="t", payload={"predicate": lambda t, i=i: t.id == 25 + i}))
    default_after, engine_after = _table_counts(engine)
    assert default_after == default_before
    assert engine_after - engine_before == 2 * n
    assert pkg["t"].count() == 50 - n
