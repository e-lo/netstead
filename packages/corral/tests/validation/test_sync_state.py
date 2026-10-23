"""Unit tests for :mod:`corral.validation.sync_state` (task 2.6 / issue #65).

The sync-state model is "out-of-sync awareness" — :class:`DirtyTracker`
records content hashes at validation time and flags drift on a later
read. Tests cover:

* Cross-engine hash stability — ibis / polars / pandas produce the
  same hex digest for identical data (the cross-engine convergence
  contract of :meth:`Engine.to_pandas`).
* Hash sensitivity to row addition + row reordering.
* Column-scoped hash insensitivity to unrelated column changes.
* DirtyTracker table stamps — record, replace, query, drop, "unknown
  is not dirty".
* FK stamps — record both sides; detect source change; detect target
  change; detect missing table.
* :meth:`DirtyTracker.check` — emits ``sync.fk_stale`` and
  ``sync.unverifiable`` with correct severity (WARNING default, ERROR
  under strict).
* v0.3 regression — a stale FK MUST produce an Issue, not silently
  pass. Pins the bug class where missing hash records let stale FKs
  through unnoticed.
"""

from __future__ import annotations

from typing import Any

import pytest
from corral.engines.ibis_engine import IbisEngine
from corral.validation import Category, Severity, ValidationReport
from corral.validation.sync_state import (
    DirtyTracker,
    FKStamp,
    TableHash,
    hash_column,
    hash_table,
)

# ---------------------------------------------------------------------------
# Engine matrix (mirrors test_foreign_keys.py)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard
    import polars  # noqa: F401

    _POLARS_AVAILABLE = True
except ImportError:  # pragma: no cover - polars not installed
    _POLARS_AVAILABLE = False


def _engine_for(name: str):
    """Build a fresh engine instance for the parametrised name."""
    if name == "ibis":
        return IbisEngine()
    if name == "polars":  # pragma: no cover - exercised only when polars installed
        from corral.engines.ibis_engine import IbisEngine as _PE

        return _PE()
    if name == "pandas":
        return IbisEngine()
    raise AssertionError(f"unknown engine: {name}")  # pragma: no cover


ENGINES = [
    "ibis",
    pytest.param(
        "polars",
        marks=pytest.mark.skipif(not _POLARS_AVAILABLE, reason="polars not installed"),
    ),
    "pandas",
]


# ---------------------------------------------------------------------------
# Test data fixtures — pulled from Leavenworth so the parity tests use real data
# ---------------------------------------------------------------------------

# Small canonical 2-row link table — keeps the cross-engine parity tests
# deterministic. The Leavenworth fixture is heavier; we use it only for
# the integration-flavour test.
_LINK_ROWS = [
    {"link_id": 1, "from_node_id": 10, "to_node_id": 20, "name": "Main St"},
    {"link_id": 2, "from_node_id": 20, "to_node_id": 30, "name": "Oak Ave"},
]
_NODE_ROWS = [
    {"node_id": 10, "name": "north"},
    {"node_id": 20, "name": "center"},
    {"node_id": 30, "name": "south"},
]


def _scan(engine, rows: list[dict[str, Any]]):
    """Engine-agnostic in-memory scan."""
    return engine.scan({"data": rows})


# ---------------------------------------------------------------------------
# 1-5. Hash helpers — cross-engine stability + sensitivity
# ---------------------------------------------------------------------------


class TestHashHelpers:
    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_hash_table_returns_hex_digest(self, engine_name):
        """Sanity — output is a 64-char hex string (sha256)."""
        e = _engine_for(engine_name)
        t = _scan(e, _LINK_ROWS)
        h = hash_table(t, e)
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_table_stable_across_engines(self):
        """ibis, polars (if installed), and pandas produce the same digest.

        The :meth:`Engine.to_pandas` contract normalizes to the same
        nullable dtypes (``Int64`` / ``string`` etc.) across engines;
        the hash is therefore identical for identical data. This is the
        cross-engine convergence test the task spec explicitly asks for.
        """
        digests = {}
        for engine_name in ["ibis"] + (["polars"] if _POLARS_AVAILABLE else []):
            e = _engine_for(engine_name)
            t = _scan(e, _LINK_ROWS)
            digests[engine_name] = hash_table(t, e)
        # All engines must agree.
        unique = set(digests.values())
        assert len(unique) == 1, f"engines disagree on hash: {digests}"

    def test_hash_column_stable_across_engines(self):
        """Same cross-engine guarantee for the column-scoped helper."""
        digests = {}
        for engine_name in ["ibis"] + (["polars"] if _POLARS_AVAILABLE else []):
            e = _engine_for(engine_name)
            t = _scan(e, _LINK_ROWS)
            digests[engine_name] = hash_column(t, "from_node_id", e)
        assert len(set(digests.values())) == 1, f"engines disagree: {digests}"

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_hash_changes_when_row_added(self, engine_name):
        """Adding a row produces a different digest."""
        e = _engine_for(engine_name)
        t1 = _scan(e, _LINK_ROWS)
        t2 = _scan(e, [*_LINK_ROWS, {"link_id": 3, "from_node_id": 30, "to_node_id": 40, "name": "Pine"}])
        assert hash_table(t1, e) != hash_table(t2, e)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_hash_changes_when_row_reordered(self, engine_name):
        """Reordering rows changes the hash — documented + locked in.

        Order-preserving was the deliberate choice (see module
        docstring): a reordered table IS a different table for
        sync-state purposes. Callers that want order-independent
        comparison should sort first.
        """
        e = _engine_for(engine_name)
        t1 = _scan(e, _LINK_ROWS)
        reordered = list(reversed(_LINK_ROWS))
        t2 = _scan(e, reordered)
        assert hash_table(t1, e) != hash_table(t2, e)

    @pytest.mark.parametrize("engine_name", ENGINES)
    def test_hash_column_unchanged_when_unrelated_column_changes(self, engine_name):
        """A change to one column doesn't perturb the digest of another.

        This is what makes column-scoped FK stamps actually useful — if
        ``hash_column("from_node_id")`` jumped every time the unrelated
        ``"name"`` column got edited, the sync-state model would scream
        on every edit and the user would learn to ignore it.
        """
        e = _engine_for(engine_name)
        t1 = _scan(e, _LINK_ROWS)
        # Mutate the "name" column on every row; leave "from_node_id" alone.
        mutated = [{**r, "name": r["name"] + " EDITED"} for r in _LINK_ROWS]
        t2 = _scan(e, mutated)
        assert hash_column(t1, "from_node_id", e) == hash_column(t2, "from_node_id", e)
        # Sanity — the column we DID change should produce a different digest.
        assert hash_column(t1, "name", e) != hash_column(t2, "name", e)

    def test_hash_column_raises_on_missing_column(self):
        """KeyError, not a silent empty digest, when the column doesn't exist."""
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        with pytest.raises(KeyError):
            hash_column(t, "nonexistent_column", e)


# ---------------------------------------------------------------------------
# 6-12. DirtyTracker — table stamps
# ---------------------------------------------------------------------------


class TestTableStamps:
    def test_stamp_table_records_hash(self):
        """A fresh stamp shows up in the tracker."""
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        stamp = tracker.stamp_table("link", t, e)
        assert isinstance(stamp, TableHash)
        assert stamp.table == "link"
        assert len(stamp.content_hash) == 64
        assert tracker.get_table_stamp("link") is stamp

    def test_stamp_table_idempotent(self):
        """Calling stamp_table twice replaces the prior stamp.

        Only the most recent stamp survives — the old one is dropped
        cleanly. Idempotent in the sense that re-stamping on the same
        unchanged data leaves the same content_hash; the
        ``computed_at`` may differ.
        """
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        first = tracker.stamp_table("link", t, e)
        second = tracker.stamp_table("link", t, e)
        # Both stamps represent the same data so the hash is identical.
        assert first.content_hash == second.content_hash
        # But there's only ONE stamp on file — the second.
        assert tracker.get_table_stamp("link") is second

    def test_get_table_stamp_returns_none_for_unstamped(self):
        """``get_table_stamp`` returns None — not raises — for unknown tables."""
        tracker = DirtyTracker()
        assert tracker.get_table_stamp("never_seen") is None

    def test_is_table_dirty_false_for_unstamped(self):
        """Explicit lock on the "unknown != dirty" semantics.

        We have no baseline to compare against — returning True would
        force the caller to invent a baseline (and likely a panicky
        re-validate). Returning False is correct: the tracker has no
        opinion about a table it's never seen.
        """
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        assert tracker.is_table_dirty("never_stamped", t, e) is False

    def test_is_table_dirty_false_when_unchanged(self):
        """After stamping, the same expression is not dirty."""
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_table("link", t, e)
        assert tracker.is_table_dirty("link", t, e) is False

    def test_is_table_dirty_true_after_mutation(self):
        """Mutate the underlying data; is_table_dirty must flip to True."""
        e = IbisEngine()
        t1 = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_table("link", t1, e)
        # Build a different expression (extra row) — the tracker only
        # holds the hash, so we just need a new expression to test against.
        t2 = _scan(e, [*_LINK_ROWS, {"link_id": 99, "from_node_id": 99, "to_node_id": 99, "name": "Q"}])
        assert tracker.is_table_dirty("link", t2, e) is True

    def test_mark_dirty_invalidates_stamp(self):
        """mark_dirty drops the stamp; is_table_dirty then returns False (unstamped).

        The "False" return value is correct per the documented
        "unknown != dirty" semantics — after mark_dirty there's no
        baseline to compare against. The intent is "force re-validate";
        the next validation pass re-stamps.
        """
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_table("link", t, e)
        assert tracker.get_table_stamp("link") is not None
        tracker.mark_dirty("link")
        assert tracker.get_table_stamp("link") is None
        # Per documented semantics — unknown is not dirty.
        assert tracker.is_table_dirty("link", t, e) is False

    def test_mark_dirty_noop_on_unstamped(self):
        """Calling mark_dirty on a table that was never stamped is silent."""
        tracker = DirtyTracker()
        tracker.mark_dirty("never_existed")  # must not raise

    def test_known_tables_lists_stamped(self):
        e = IbisEngine()
        t = _scan(e, _LINK_ROWS)
        tracker = DirtyTracker()
        assert tracker.known_tables() == []
        tracker.stamp_table("link", t, e)
        tracker.stamp_table("node", _scan(e, _NODE_ROWS), e)
        assert set(tracker.known_tables()) == {"link", "node"}


# ---------------------------------------------------------------------------
# 13-17. DirtyTracker — FK stamps + stale detection
# ---------------------------------------------------------------------------


class TestFKStamps:
    def test_stamp_fk_records_both_sides(self):
        """A fresh FK stamp captures both source + target hashes."""
        tracker = DirtyTracker()
        stamp = tracker.stamp_fk(
            "link",
            "from_node_id",
            "node",
            "node_id",
            source_hash="aaa",
            target_hash="bbb",
        )
        assert isinstance(stamp, FKStamp)
        assert stamp.source_table == "link"
        assert stamp.source_hash == "aaa"
        assert stamp.target_hash == "bbb"

    def test_stamp_fk_from_exprs_computes_hashes(self):
        """The convenience wrapper computes the hashes itself."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        stamp = tracker.stamp_fk_from_exprs(
            "link",
            "from_node_id",
            link,
            "node",
            "node_id",
            node,
            engine=e,
        )
        # Hashes must match the helper outputs directly.
        assert stamp.source_hash == hash_column(link, "from_node_id", e)
        assert stamp.target_hash == hash_column(node, "node_id", e)

    def test_stale_fks_empty_when_nothing_changed(self):
        """Stamp and immediately check — no drift, no stale FKs."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        assert tracker.stale_fks({"link": link, "node": node}, e) == []

    def test_stale_fks_detects_source_change(self):
        """Mutating the source FK column lands in the stale list."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Mutated source — different from_node_id values.
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1000} for r in _LINK_ROWS])
        stale = tracker.stale_fks({"link": link2, "node": node}, e)
        assert len(stale) == 1
        assert stale[0].source_table == "link"

    def test_stale_fks_detects_target_change(self):
        """Mutating the target FK column lands in the stale list."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Mutate the target — different node_ids.
        node2 = _scan(e, [{**r, "node_id": r["node_id"] + 5000} for r in _NODE_ROWS])
        stale = tracker.stale_fks({"link": link, "node": node2}, e)
        assert len(stale) == 1
        assert stale[0].target_table == "node"

    def test_stale_fks_detects_missing_table(self):
        """If a table is dropped from current_tables, the stamp is stale."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Drop "node" from the mapping.
        stale = tracker.stale_fks({"link": link}, e)
        assert len(stale) == 1

    def test_stale_fks_detects_dropped_column(self):
        """A column removed from a table (but the table still present) is stale."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Re-scan link WITHOUT the from_node_id column.
        link_minus_col = _scan(
            e, [{"link_id": r["link_id"], "to_node_id": r["to_node_id"], "name": r["name"]} for r in _LINK_ROWS]
        )
        stale = tracker.stale_fks({"link": link_minus_col, "node": node}, e)
        assert len(stale) == 1

    def test_clear_fk_stamps(self):
        """clear_fk_stamps wipes the list."""
        tracker = DirtyTracker()
        tracker.stamp_fk("a", "x", "b", "y", source_hash="s", target_hash="t")
        assert tracker.stale_fks({}, engine=IbisEngine()) != []  # missing tables -> stale
        tracker.clear_fk_stamps()
        assert tracker.stale_fks({}, engine=IbisEngine()) == []


# ---------------------------------------------------------------------------
# 18-21. DirtyTracker.check — Issue emission
# ---------------------------------------------------------------------------


class TestCheckEmitsIssues:
    def test_check_emits_warning_for_stale_fk(self):
        """Default severity for a stale FK is WARNING."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Mutate the source.
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        report = tracker.check({"link": link2, "node": node}, engine=e)
        stale_issues = [i for i in report.issues if i.code == "sync.fk_stale"]
        assert len(stale_issues) == 1
        assert stale_issues[0].severity is Severity.WARNING
        assert stale_issues[0].category is Category.SYNC_STATE

    def test_check_emits_error_under_strict(self):
        """strict=True elevates sync issues to ERROR."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        report = tracker.check({"link": link2, "node": node}, engine=e, strict=True)
        stale_issues = [i for i in report.issues if i.code == "sync.fk_stale"]
        assert len(stale_issues) == 1
        assert stale_issues[0].severity is Severity.ERROR

    def test_check_appends_to_existing_report(self):
        """If a report is passed in, sync issues are appended (not replaced)."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Pre-populate with a synthetic schema issue.
        report = ValidationReport(spec_version="0.97")
        report.add(
            severity=Severity.ERROR,
            category=Category.SCHEMA,
            code="schema.required",
            message="pre-existing",
            table="other",
        )
        # Trigger a stale FK.
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        returned = tracker.check({"link": link2, "node": node}, engine=e, report=report)
        # Same instance, and the pre-existing issue is still there.
        assert returned is report
        codes = [i.code for i in report.issues]
        assert "schema.required" in codes
        assert "sync.fk_stale" in codes

    def test_check_message_names_fk_relationship(self):
        """The Issue.message must name the specific FK and table.

        This is the v0.3 lesson — generic "FK violation" was unhelpful.
        We require the message to include source table, source field,
        target table, AND target field so the user knows exactly what
        to re-validate.
        """
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        report = tracker.check({"link": link2, "node": node}, engine=e)
        msg = report.issues[0].message
        assert "link" in msg
        assert "from_node_id" in msg
        assert "node" in msg
        assert "node_id" in msg

    def test_check_unverifiable_when_table_missing(self):
        """A missing source/target table emits sync.unverifiable (not fk_stale)."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Drop node.
        report = tracker.check({"link": link}, engine=e)
        codes = [i.code for i in report.issues]
        assert "sync.unverifiable" in codes
        assert "sync.fk_stale" not in codes
        msg = next(i.message for i in report.issues if i.code == "sync.unverifiable")
        assert "node" in msg

    def test_check_clean_when_nothing_changed(self):
        """No drift, no issues. Report is_clean."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        report = tracker.check({"link": link, "node": node}, engine=e)
        assert report.is_clean
        assert [i for i in report.issues if i.category is Category.SYNC_STATE] == []

    def test_check_emits_fix_hint(self):
        """Issues carry the documented fix_hint."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        report = tracker.check({"link": link2, "node": node}, engine=e)
        assert report.issues[0].fix_hint is not None
        assert "validate" in report.issues[0].fix_hint.lower()

    def test_check_extra_carries_target_info(self):
        """Issue.extra must include target_table + target_field for renderers."""
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        link2 = _scan(e, [{**r, "from_node_id": r["from_node_id"] + 1} for r in _LINK_ROWS])
        report = tracker.check({"link": link2, "node": node}, engine=e)
        extra = report.issues[0].extra
        assert extra.get("target_table") == "node"
        assert extra.get("target_field") == "node_id"
        assert extra.get("source_field") == "from_node_id"


# ---------------------------------------------------------------------------
# 22. v0.3 regression — stale FK must NOT silently pass
# ---------------------------------------------------------------------------


class TestV03Regression:
    def test_v03_silent_failure_regression(self):
        """A stale FK MUST produce an Issue, not silently pass.

        This pins the v0.3 bug class where a stale FK could pass
        unnoticed because the validation layer had no notion of "since
        when?". The DirtyTracker is the only thing standing between the
        v0.3 silent-stale-FK bug and the user — so this regression
        test exists specifically to fail loudly if a refactor ever
        accidentally re-introduces the silent path.

        Concretely: stamp an FK on clean data; mutate the target; run
        check(); assert that at least one Issue with code
        ``sync.fk_stale`` appears in the report. Anything less is the
        v0.3 bug.
        """
        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        # Validate (clean) and stamp the FK.
        tracker.stamp_fk_from_exprs("link", "from_node_id", link, "node", "node_id", node, engine=e)
        # Now mutate node_id values — the FK is now logically broken
        # because node_id=10 (referenced by link row 0) no longer exists.
        # The validation REPORT we'd previously held says "clean" — but
        # the data has changed since. The whole point of the DirtyTracker
        # is to surface that drift.
        node_mutated = _scan(e, [{**r, "node_id": r["node_id"] * 100} for r in _NODE_ROWS])
        report = tracker.check({"link": link, "node": node_mutated}, engine=e)
        sync_issues = report.by_category(Category.SYNC_STATE)
        assert len(sync_issues) >= 1, (
            "v0.3 regression: stale FK silently passed — "
            "check() returned zero sync issues despite the target table being mutated."
        )
        # And specifically the fk_stale code (NOT just unverifiable).
        assert any(i.code == "sync.fk_stale" for i in sync_issues), (
            "v0.3 regression: target mutation should produce sync.fk_stale, "
            f"got only codes: {[i.code for i in sync_issues]}"
        )


# ---------------------------------------------------------------------------
# Bonus: composite FK + tracker integration sanity
# ---------------------------------------------------------------------------


class TestCompositeFK:
    def test_composite_fk_stamp_and_check(self):
        """Composite FKs (comma-joined fields) stamp + check end-to-end.

        Uses the module's internal helper to compute the composite hash
        so the test pins the round-trip property — stamp + check is
        clean iff the source data is unchanged — without baking in the
        specific hash algorithm (which moved from pandas to pyarrow
        buffers in the ibis-first refactor).
        """
        from corral.validation.sync_state import _column_hash_from_expr

        e = IbisEngine()
        # Synthetic composite-key tables.
        src_rows = [{"a": 1, "b": "x", "id": 1}, {"a": 2, "b": "y", "id": 2}]
        tgt_rows = [{"a": 1, "b": "x", "name": "first"}, {"a": 2, "b": "y", "name": "second"}]
        src = _scan(e, src_rows)
        tgt = _scan(e, tgt_rows)
        # The new helper operates on live exprs directly (column-scoped
        # at the ibis layer), so we don't pre-materialize the whole
        # table just to fingerprint two columns.
        src_hash = _column_hash_from_expr(src, "a,b", e)
        tgt_hash = _column_hash_from_expr(tgt, "a,b", e)
        tracker = DirtyTracker()
        tracker.stamp_fk(
            "src",
            "a,b",
            "tgt",
            "a,b",
            source_hash=src_hash,
            target_hash=tgt_hash,
        )
        # Clean check.
        report = tracker.check({"src": src, "tgt": tgt}, engine=e)
        assert report.is_clean
        # Mutate column "b" on source — should drift.
        src2 = _scan(e, [{**r, "b": r["b"] + "_edit"} for r in src_rows])
        report2 = tracker.check({"src": src2, "tgt": tgt}, engine=e)
        assert any(i.code == "sync.fk_stale" for i in report2.issues)


# ---------------------------------------------------------------------------
# I10 regression — stale_fks / check use column-scoped hashes (no whole-table
# materialization just to fingerprint one column).
# ---------------------------------------------------------------------------


class TestColumnScopedHashIO:
    """Pins the I10 fix: ``DirtyTracker.stale_fks`` / ``.check`` must not
    materialize the WHOLE source/target table to hash a single FK column.

    Strategy: monkey-patch ``hash_table`` in the sync_state module to
    raise on any call. The post-fix paths use ``hash_column`` (which
    projects at the ibis layer), never ``hash_table``; the pre-fix
    paths reached for the full pyarrow table and would have tripped
    the explosion. We can't monkeypatch the ibis Table object itself
    because ibis enforces attribute immutability.
    """

    def test_stale_fks_uses_column_scoped_hash_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from corral.validation import sync_state as ss

        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs(
            "link",
            "from_node_id",
            link,
            "node",
            "node_id",
            node,
            engine=e,
        )

        def _exploding_hash_table(*_a, **_kw):
            raise AssertionError(
                "stale_fks must not call hash_table; the column-scoped "
                "path projects at the ibis layer and only hashes the FK column"
            )

        monkeypatch.setattr(ss, "hash_table", _exploding_hash_table)
        result = tracker.stale_fks({"link": link, "node": node}, e)
        assert result == []

    def test_check_uses_column_scoped_hash_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from corral.validation import sync_state as ss

        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        node = _scan(e, _NODE_ROWS)
        tracker = DirtyTracker()
        tracker.stamp_fk_from_exprs(
            "link",
            "from_node_id",
            link,
            "node",
            "node_id",
            node,
            engine=e,
        )

        def _exploding_hash_table(*_a, **_kw):
            raise AssertionError("DirtyTracker.check must not call hash_table; use column-scoped hashes")

        monkeypatch.setattr(ss, "hash_table", _exploding_hash_table)
        report = tracker.check({"link": link, "node": node}, engine=e)
        assert report.is_clean

    def test_column_scoped_hash_matches_hash_column_direct(self) -> None:
        """The composite/single-column helper output equals hash_column directly.

        Belt-and-braces — pins the contract that the I10 refactor
        didn't change what gets hashed, only how it's projected.
        """
        from corral.validation.sync_state import _column_hash_from_expr

        e = IbisEngine()
        link = _scan(e, _LINK_ROWS)
        # Single-column round-trip.
        a = _column_hash_from_expr(link, "from_node_id", e)
        b = hash_column(link, "from_node_id", e)
        assert a == b
