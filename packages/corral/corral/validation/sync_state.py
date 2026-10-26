r"""Sync-state model — DirtyTracker for out-of-sync awareness (task 2.6 / issue #65).

This module is the "did anything change since I last validated?" layer
of the corral validation framework. Other validators (schema, FK,
structural) answer "is the data valid *right now*?"; this one answers
"is the validation result I'm holding still *trustworthy*?".

The motivating bug class is v0.3's silent FK staleness: a user would
``validate(net)`` (clean), then edit a node row, then ``write(net)``,
and the on-disk file would carry the now-broken FK with no warning at
all. The :class:`DirtyTracker` closes that loop — every clean FK
validation stamps content hashes for both sides of the FK; any later
read walks the FK graph and flags every stamp whose hashes don't match
the current tables.

Public surface
--------------

- :class:`DirtyTracker` — the stateful tracker. Mutable, not
  thread-safe (matches the rest of the engine / dataset layer in v1.0).
- :class:`TableHash` — frozen value type: ``(table, content_hash,
  computed_at)``.
- :class:`FKStamp` — frozen value type: hashes for the source FK
  column + target FK column at validation time.
- :func:`hash_table` — sha256 of a whole table's content.
- :func:`hash_column` — sha256 of a single column's content.

Hash algorithm
--------------

Both helpers route the expression through
:func:`corral.validation._ibis.to_ibis` (pass-through for the
default ibis engine; ``ibis.memtable`` wrap for pandas/polars) and
materialize via ``.to_pyarrow()``. We then hash each column by walking
its pyarrow buffers and sha256-ing the concatenated bytes:

.. code-block:: python

    arr = arrow_table.column(name).combine_chunks()
    h = hashlib.sha256()
    for buf in arr.buffers():
        h.update(b"\\x01" if buf is not None else b"\\x00")
        if buf is not None:
            h.update(buf.to_pybytes())
    return h.hexdigest()

Properties of this choice:

- **Cross-engine stable** — going through the ibis duckdb backend
  produces an identical arrow schema (``int64`` / ``float64`` /
  ``string`` / ``bool``) regardless of source engine. The cross-engine
  parity test pins this.
- **Pandas-free** — the validators in this package no longer
  materialize tables to pandas; the only place pandas appears here is
  the legacy ``hash_column`` raising ``KeyError`` on missing columns
  (a no-pandas error type).
- **Value-sensitive** — different bytes in the buffer → different
  digest. Adding rows, changing values, and reordering rows all change
  the digest.
- **Column-scoped** — :func:`hash_column` hashes one arrow column;
  unrelated columns can change without invalidating the FK stamp.
- **Row-order matters** — arrow buffers carry values in row order. A
  reordered table is treated as a different table; that matches the
  sync-state contract (if the row layout on disk changed, the user
  did *something* and we shouldn't pretend nothing happened). Callers
  that want order-independent comparison should sort first.

Codes emitted
-------------

============================  =====================================================
Code                          When emitted
============================  =====================================================
``sync.fk_stale``             A previously validated FK has source OR target
                              content hash that no longer matches. Severity:
                              WARNING by default, ERROR under ``strict=True``.
``sync.unverifiable``         An FK was stamped but the table is no longer in
                              ``current_tables``. Severity: WARNING by default,
                              ERROR under ``strict=True``.
============================  =====================================================

v0.3 regression
---------------

The v0.3 behavior we're locking out: a stale FK silently passing
because there was no hash record to compare against. The
:func:`test_v03_silent_failure_regression` test pins the inverse — a
stale FK MUST surface an Issue, not silently pass.

Examples:
    >>> from corral.engines.ibis_engine import IbisEngine
    >>> from corral.validation.sync_state import DirtyTracker
    >>> e = IbisEngine()
    >>> link = e.scan({"data": [{"link_id": 1, "from_node_id": 1}]})
    >>> tracker = DirtyTracker()
    >>> stamp = tracker.stamp_table("link", link, e)
    >>> tracker.is_table_dirty("link", link, e)
    False
    >>> # Unknown tables are NOT dirty — we have no baseline to compare.
    >>> tracker.is_table_dirty("never_stamped", link, e)
    False
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pyarrow as pa

from corral.reports import Category, Severity, ValidationReport

from ._ibis import to_ibis

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine, TableExpr

__all__ = [
    "DirtyTracker",
    "FKStamp",
    "TableHash",
    "hash_column",
    "hash_table",
]


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableHash:
    """Content fingerprint for one table at one point in time.

    Frozen + hashable so it can be stored in sets / dicts and carried
    alongside :class:`Issue` records without risk of mutation.

    Attributes:
        table: Logical table name (matches the Frictionless resource name).
        content_hash: Hex-encoded sha256 of the table's arrow buffers.
        computed_at: Wall-clock time when the hash was computed.

    Examples:
        >>> from datetime import datetime
        >>> h = TableHash(table="link", content_hash="abc123",
        ...               computed_at=datetime(2025, 1, 1))
        >>> h.table
        'link'
        >>> hash(h) == hash(h)  # frozen + hashable
        True
    """

    table: str
    content_hash: str
    computed_at: datetime


@dataclass(frozen=True)
class FKStamp:
    """Validation-time fingerprint of one FK relationship.

    Captures the content hashes of BOTH sides of a single foreign-key
    relationship at the moment the FK validator stamped them. A later
    read of the same FK is "stale" if EITHER side's current hash has
    changed.

    Composite FKs store their fields as a comma-joined string so the
    type stays trivially hashable.

    Attributes:
        source_table: Source table name (``"link"``).
        source_field: Source field name(s); composite FKs are joined with ``","``.
        target_table: Target table name (``"node"``).
        target_field: Target field name(s); composite-joined like ``source_field``.
        source_hash: Hex sha256 of the source column at validation time.
        target_hash: Hex sha256 of the target column at validation time.
        validated_at: When the stamp was recorded.

    Examples:
        >>> from datetime import datetime
        >>> stamp = FKStamp(
        ...     source_table="link", source_field="from_node_id",
        ...     target_table="node", target_field="node_id",
        ...     source_hash="aaa", target_hash="bbb",
        ...     validated_at=datetime(2025, 1, 1),
        ... )
        >>> stamp.source_table
        'link'
    """

    source_table: str
    source_field: str
    target_table: str
    target_field: str
    source_hash: str
    target_hash: str
    validated_at: datetime


# ---------------------------------------------------------------------------
# Hash helpers — content-addressed fingerprints (pyarrow-based)
# ---------------------------------------------------------------------------


def _hash_arrow_array(array: pa.Array | pa.ChunkedArray) -> str:
    """Sha256 hex digest of an arrow array's underlying buffers.

    Walks each buffer (null bitmap, offsets, data) and includes a
    presence-byte before each so a null-bitmap-only vs all-present
    distinction stays in the digest. ``combine_chunks`` collapses
    multi-chunk arrays so the buffer layout is canonical regardless
    of how the producer batched.
    """
    arr = array.combine_chunks() if isinstance(array, pa.ChunkedArray) else array
    h = hashlib.sha256()
    for buf in arr.buffers():
        if buf is None:
            h.update(b"\x00")
        else:
            h.update(b"\x01")
            h.update(buf.to_pybytes())
    return h.hexdigest()


def hash_table(expr: TableExpr, engine: Engine) -> str:
    """Compute the content hash of an entire table.

    Normalizes ``expr`` to ibis, materializes to pyarrow, then hashes
    the rows column-by-column and combines the per-column digests into
    a single stable hex digest. Column order affects the hash; this is
    a deliberate choice — a reordered schema IS a different table for
    sync-state purposes.

    Args:
        expr: An engine-native table expression.
        engine: The engine that produced ``expr``. Retained for
            signature compatibility; the pyarrow-based hash no longer
            routes through ``engine.to_pandas``.

    Returns:
        Hex-encoded sha256 digest of the table's content.

    Examples:
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> t1 = e.scan({"data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]})
        >>> t2 = e.scan({"data": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]})
        >>> hash_table(t1, e) == hash_table(t2, e)
        True
        >>> t3 = e.scan({"data": [{"a": 9, "b": 2}, {"a": 3, "b": 4}]})
        >>> hash_table(t1, e) == hash_table(t3, e)
        False
    """
    arrow = to_ibis(expr).to_pyarrow()
    # Hash each column independently and combine. Hashing the
    # concatenation of per-column digests gives a stable
    # column-order-aware aggregate.
    parts = [f"{col}:{_hash_arrow_array(arrow.column(col))}" for col in arrow.column_names]
    combined = "|".join(parts).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def hash_column(expr: TableExpr, column: str, engine: Engine) -> str:
    """Compute the content hash of a single column.

    Same arrow-buffer algorithm as :func:`hash_table` but scoped to one
    column. Used by FK-validator callers so each :class:`FKStamp`
    captures JUST the FK columns — a change to an unrelated column
    doesn't invalidate the stamp.

    The column projection happens at the ibis layer (``ibis_table
    .select(column).to_pyarrow()``) so duckdb materializes a
    one-column arrow array, not the entire table. At Bay-Area scale
    this turns a single-column hash from a multi-gigabyte
    materialization into a sub-second projection.

    Args:
        expr: Engine-native table expression.
        column: The column to hash. Must exist in the table; missing
            columns raise :class:`KeyError`.
        engine: The engine that produced ``expr``. Retained for
            signature compatibility.

    Returns:
        Hex-encoded sha256 digest of the column's content.

    Raises:
        KeyError: If ``column`` is not in the table.

    Examples:
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> t = e.scan({"data": [{"a": 1, "b": 2}, {"a": 1, "b": 9}]})
        >>> h_a = hash_column(t, "a", e)
        >>> h_b = hash_column(t, "b", e)
        >>> h_a == h_b
        False
    """
    ibis_table = to_ibis(expr)
    if column not in ibis_table.columns:
        raise KeyError(f"hash_column: column {column!r} not in table; available: {list(ibis_table.columns)}")
    arrow = ibis_table.select(column).to_pyarrow()
    return _hash_arrow_array(arrow.column(column))


def _column_hash_from_expr(expr: TableExpr, field_spec: str, engine: Engine) -> str:
    """Hash a (possibly composite) column spec against a live table expression.

    ``field_spec`` is either a single column name or a comma-joined
    list (the same convention :class:`FKStamp` stores). For the
    single-column case we delegate straight to :func:`hash_column`,
    which projects to just that column at the ibis layer — so duckdb
    materializes a one-column arrow array instead of the whole table.
    For composite specs we project each component column separately
    and combine the digests, matching :func:`hash_table`'s combine
    step so the result is column-order aware and any single-column
    drift invalidates the stamp.

    Raises :class:`KeyError` (via :func:`hash_column`) if any column is
    not present.
    """
    fields = [f.strip() for f in field_spec.split(",")] if "," in field_spec else [field_spec]
    if len(fields) == 1:
        return hash_column(expr, fields[0], engine)
    parts = [f"{column}:{hash_column(expr, column, engine)}" for column in fields]
    combined = "|".join(parts).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


# ---------------------------------------------------------------------------
# DirtyTracker
# ---------------------------------------------------------------------------


class DirtyTracker:
    """Records content-hash stamps for tables + FK validations.

    The answer to "has anything changed since I validated this?". Used
    by writes (warn on stale FK), interactive editing (netstead.clean /
    .edit surfaces), and the long-lived ``Package`` (task 2.7) so a
    ``net.write()`` call can re-check sync state before flushing.

    Lifecycle:

    1. :meth:`stamp_table` records the current content hash of a
       table. Idempotent — calling it again replaces the stamp.
    2. :meth:`stamp_fk` records a validated FK relationship; called
       by the FK validator after a clean pass.
    3. :meth:`is_table_dirty` returns ``True`` iff the current table's
       content hash differs from the recorded one. Returns ``False``
       for unstamped tables (unknown != dirty).
    4. :meth:`stale_fks` returns the list of stamps whose source or
       target hash no longer matches.
    5. :meth:`mark_dirty` explicitly removes a stamp.
    6. :meth:`check` is the convenience method: walks the FK stamps,
       compares against ``current_tables``, returns a populated
       :class:`ValidationReport`.

    Thread safety: NOT thread-safe. Consistent with the rest of the
    engine / dataset layer in v1.0.

    Examples:
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> link = e.scan({"data": [{"link_id": 1, "from_node_id": 1}]})
        >>> node = e.scan({"data": [{"node_id": 1}]})
        >>> tracker = DirtyTracker()
        >>> _ = tracker.stamp_table("link", link, e)
        >>> _ = tracker.stamp_table("node", node, e)
        >>> tracker.stamp_fk_from_exprs(
        ...     "link", "from_node_id", link,
        ...     "node", "node_id", node,
        ...     engine=e,
        ... ).source_table
        'link'
        >>> tracker.stale_fks({"link": link, "node": node}, e)
        []
    """

    def __init__(self) -> None:
        """Construct an empty tracker — no table stamps, no FK stamps."""
        self._tables: dict[str, TableHash] = {}
        self._fks: list[FKStamp] = []

    # ------------------------------------------------------------------
    # Table stamps
    # ------------------------------------------------------------------

    def stamp_table(self, name: str, expr: TableExpr, engine: Engine) -> TableHash:
        """Record (or replace) the current content hash for a table."""
        content_hash = hash_table(expr, engine)
        stamp = TableHash(table=name, content_hash=content_hash, computed_at=datetime.now())
        self._tables[name] = stamp
        return stamp

    def get_table_stamp(self, name: str) -> TableHash | None:
        """Return the most recent stamp for ``name``, or ``None`` if unstamped."""
        return self._tables.get(name)

    def is_table_dirty(self, name: str, expr: TableExpr, engine: Engine) -> bool:
        """Return ``True`` iff the current content hash differs from the stamp.

        Returns ``False`` for unstamped tables — "unknown" is not the
        same as "dirty".
        """
        stamp = self._tables.get(name)
        if stamp is None:
            return False
        return hash_table(expr, engine) != stamp.content_hash

    def mark_dirty(self, name: str) -> None:
        """Explicitly drop the stamp for ``name``.

        Use this after a direct DataFrame mutation that bypassed the
        engine — the recorded hash no longer represents the live data.
        """
        self._tables.pop(name, None)

    def known_tables(self) -> list[str]:
        """Return the names of all currently stamped tables."""
        return list(self._tables.keys())

    # ------------------------------------------------------------------
    # FK stamps
    # ------------------------------------------------------------------

    def stamp_fk(
        self,
        source_table: str,
        source_field: str,
        target_table: str,
        target_field: str,
        *,
        source_hash: str,
        target_hash: str,
    ) -> FKStamp:
        """Record an FK validation, given precomputed column hashes.

        The hashes are supplied by the caller — typically the FK
        validator, which has already materialized both sides at
        validation time. Composite FKs join their field names with
        ``","`` so :class:`FKStamp` stays trivially hashable.
        """
        stamp = FKStamp(
            source_table=source_table,
            source_field=source_field,
            target_table=target_table,
            target_field=target_field,
            source_hash=source_hash,
            target_hash=target_hash,
            validated_at=datetime.now(),
        )
        self._fks.append(stamp)
        return stamp

    def stamp_fk_from_exprs(
        self,
        source_table: str,
        source_field: str,
        source_expr: TableExpr,
        target_table: str,
        target_field: str,
        target_expr: TableExpr,
        *,
        engine: Engine,
    ) -> FKStamp:
        """Convenience — stamp an FK by computing hashes from expressions.

        Equivalent to calling :func:`hash_column` on each side and
        forwarding the results to :meth:`stamp_fk`.

        Only handles single-field FKs (column-scoped hash is one
        column). Composite FKs should call :meth:`stamp_fk` directly
        with a precomputed combined hash.
        """
        src_hash = hash_column(source_expr, source_field, engine)
        tgt_hash = hash_column(target_expr, target_field, engine)
        return self.stamp_fk(
            source_table,
            source_field,
            target_table,
            target_field,
            source_hash=src_hash,
            target_hash=tgt_hash,
        )

    def stale_fks(
        self,
        current_tables: dict[str, TableExpr],
        engine: Engine,
    ) -> list[FKStamp]:
        """Return every FK stamp whose source or target hash no longer matches.

        Composite FKs (with comma-joined field names) are handled by
        :func:`_column_hash_from_arrow` so the combined digest matches
        the original stamp's hashing convention.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> e = IbisEngine()
            >>> link = e.scan({"data": [{"link_id": 1, "from_node_id": 1}]})
            >>> node = e.scan({"data": [{"node_id": 1}]})
            >>> tracker = DirtyTracker()
            >>> _ = tracker.stamp_fk_from_exprs(
            ...     "link", "from_node_id", link,
            ...     "node", "node_id", node,
            ...     engine=e,
            ... )
            >>> tracker.stale_fks({"link": link, "node": node}, e)
            []
        """
        stale: list[FKStamp] = []
        for stamp in self._fks:
            src_expr = current_tables.get(stamp.source_table)
            tgt_expr = current_tables.get(stamp.target_table)
            if src_expr is None or tgt_expr is None:
                stale.append(stamp)
                continue
            try:
                current_src = _column_hash_from_expr(src_expr, stamp.source_field, engine)
                current_tgt = _column_hash_from_expr(tgt_expr, stamp.target_field, engine)
            except KeyError:
                stale.append(stamp)
                continue
            if current_src != stamp.source_hash or current_tgt != stamp.target_hash:
                stale.append(stamp)
        return stale

    def clear_fk_stamps(self) -> None:
        """Drop every recorded FK stamp."""
        self._fks.clear()

    # ------------------------------------------------------------------
    # Report emission
    # ------------------------------------------------------------------

    def check(
        self,
        current_tables: dict[str, TableExpr],
        *,
        engine: Engine,
        report: ValidationReport | None = None,
        strict: bool = False,
    ) -> ValidationReport:
        """Run staleness checks; populate a :class:`ValidationReport`.

        For every recorded :class:`FKStamp` whose source or target hash
        no longer matches the current data, emits either
        ``sync.fk_stale`` (data drifted) or ``sync.unverifiable``
        (table dropped from ``current_tables``). Severity is
        ``WARNING`` by default and ``ERROR`` under ``strict=True``.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> e = IbisEngine()
            >>> link = e.scan({"data": [{"link_id": 1, "from_node_id": 1}]})
            >>> node = e.scan({"data": [{"node_id": 1}]})
            >>> tracker = DirtyTracker()
            >>> _ = tracker.stamp_fk_from_exprs(
            ...     "link", "from_node_id", link,
            ...     "node", "node_id", node,
            ...     engine=e,
            ... )
            >>> r = tracker.check({"link": link, "node": node}, engine=e)
            >>> r.is_clean
            True
        """
        if report is None:
            report = ValidationReport()
        severity = Severity.ERROR if strict else Severity.WARNING
        fix_hint = "Run validate() to refresh, or pass strict=False to skip this check."

        for stamp in self._fks:
            src_label = f"{stamp.source_table}.{stamp.source_field}"
            tgt_label = f"{stamp.target_table}.{stamp.target_field}"
            fk_label = f"FK {src_label} -> {tgt_label}"
            base_extra = {
                "target_table": stamp.target_table,
                "target_field": stamp.target_field,
                "source_field": stamp.source_field,
            }

            # Unverifiable — source or target table missing entirely.
            src_expr = current_tables.get(stamp.source_table)
            tgt_expr = current_tables.get(stamp.target_table)
            if src_expr is None:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.unverifiable",
                    message=(
                        f"{fk_label}: source table {stamp.source_table!r} is no longer present; cannot verify staleness"
                    ),
                    table=stamp.source_table,
                    fix_hint=fix_hint,
                    extra=base_extra,
                )
                continue
            if tgt_expr is None:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.unverifiable",
                    message=(
                        f"{fk_label}: target table {stamp.target_table!r} is no longer present; cannot verify staleness"
                    ),
                    table=stamp.source_table,
                    fix_hint=fix_hint,
                    extra=base_extra,
                )
                continue

            # Both tables present — recompute hashes (column-scoped at
            # the ibis layer so we don't materialize the whole table
            # just to fingerprint one column).
            try:
                current_src = _column_hash_from_expr(src_expr, stamp.source_field, engine)
            except KeyError:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.unverifiable",
                    message=(
                        f"{fk_label}: source column {stamp.source_field!r} is no longer present "
                        f"in {stamp.source_table!r}; cannot verify staleness"
                    ),
                    table=stamp.source_table,
                    column=stamp.source_field,
                    fix_hint=fix_hint,
                    extra=base_extra,
                )
                continue
            try:
                current_tgt = _column_hash_from_expr(tgt_expr, stamp.target_field, engine)
            except KeyError:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.unverifiable",
                    message=(
                        f"{fk_label}: target column {stamp.target_field!r} is no longer present "
                        f"in {stamp.target_table!r}; cannot verify staleness"
                    ),
                    table=stamp.source_table,
                    column=stamp.source_field,
                    fix_hint=fix_hint,
                    extra=base_extra,
                )
                continue

            src_changed = current_src != stamp.source_hash
            tgt_changed = current_tgt != stamp.target_hash
            if not (src_changed or tgt_changed):
                continue

            if src_changed:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.fk_stale",
                    message=(
                        f"{fk_label}: source table {stamp.source_table!r} has changed since "
                        f"validation; re-run validate() to confirm"
                    ),
                    table=stamp.source_table,
                    column=stamp.source_field,
                    fix_hint=fix_hint,
                    extra={**base_extra, "side": "source"},
                )
            if tgt_changed:
                report.add(
                    severity=severity,
                    category=Category.SYNC_STATE,
                    code="sync.fk_stale",
                    message=(
                        f"{fk_label}: target table {stamp.target_table!r} has changed since "
                        f"validation; re-run validate() to confirm"
                    ),
                    table=stamp.source_table,
                    column=stamp.source_field,
                    fix_hint=fix_hint,
                    extra={**base_extra, "side": "target"},
                )
        return report
