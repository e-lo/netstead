"""Lazy single-table wrapper — see :class:`Table`.

A :class:`Table` is the unit of "one logical resource" inside a
:class:`~corral.dataset.Package`. It composes:

* a logical ``name`` (matches the Frictionless ``Resource.name`` and
  drives FK lookups);
* an engine-native lazy ``expr`` (``ibis.expr.types.Table`` /
  ``polars.LazyFrame`` / ``pandas.DataFrame``);
* a back-pointer to the :class:`~corral.engines.base.Engine` that
  produced the expression (so materialization always goes back through
  the same engine);
* an optional Frictionless :class:`~corral.spec.model.Schema` used
  by the validation layer;
* an optional source locator (``source`` + ``format``) for round-trip
  reads / writes;
* a mutable ``dirty`` flag the sync-state tracker (task 2.6) reads
  before any write.

Design rules:
    * **Lazy by default.** ``head``, ``select``, ``filter`` return a new
      :class:`Table` wrapping a transformed expression. Nothing touches
      the engine backend until the caller asks for materialization via
      :meth:`Table.to_pandas`, :meth:`Table.to_polars`,
      :meth:`Table.collect`, or :meth:`Table.count`.
    * **Each transform returns a new Table** — the original is
      unchanged. This matches the underlying engine semantics
      (TableExpr is immutable per engine) and lets callers safely hold
      a reference across a chain of ops.
    * **Mutations mark dirty.** :meth:`Table.invalidate` flips the
      ``dirty`` flag; the sync-state tracker reads it. Full Edit/Diff
      semantics arrive in task 2.9.

The cross-engine round-trip story is identical to the rest of
corral: every engine implements ``to_pandas`` and ``to_polars`` as
convergence points. The :class:`Table` simply delegates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd
    import polars as pl

    from corral.engines.base import Engine, TableExpr
    from corral.spec.model import Schema

__all__ = ["Table"]


@dataclass
class Table:
    """Lazy wrapper around one table in a data package.

    The wrapper is *mutable* (its ``expr`` may be replaced — for
    example by an editing op or a scoped view), but each underlying
    ``TableExpr`` is *immutable* per the engine's semantics. Lazy ops
    (:meth:`filter`, :meth:`select`, :meth:`head`) therefore return a
    new :class:`Table`, leaving the original unchanged.

    Mutation: this class intentionally does not expose an in-place
    ``update`` shim. For row-level edits with full diff / rollback /
    audit, use :class:`corral.editing.Edit` +
    :class:`corral.editing.Session`. For low-level mutations that
    bypass the editing framework, build a new expression directly and
    call :meth:`invalidate` on the table to flip its ``dirty`` flag so
    the sync-state tracker sees the change.

    Attributes:
        name: Logical resource name. Matches
            :attr:`corral.spec.model.Resource.name` and is used as
            the dict key inside a :class:`~corral.dataset.Package`.
        expr: Engine-native lazy expression. Concrete type depends on
            the engine (``ibis.expr.types.Table`` for ibis,
            ``polars.LazyFrame`` for polars, ``pandas.DataFrame`` for
            pandas — pandas is eager by design).
        engine: The engine that produced ``expr``. Materialization
            always routes back through this same engine to preserve
            the cross-engine dtype contract.
        schema: Optional resolved Frictionless schema. Carried so the
            validation layer can run per-field checks without
            re-loading the spec.
        source: Optional source locator (path / URL / sub-locator
            like ``"net.duckdb::link"``). Set by
            :meth:`Package.from_source`; ``None`` for tables
            constructed inline.
        format: Optional short format identifier (``"csv"``,
            ``"parquet"``, ``"duckdb"``). Mirrors
            :attr:`corral.io.base.ResourceRef.format`.
        dirty: True when the table has been mutated (or its source
            hash differs from a sync-state stamp). Read by the
            sync-state tracker (task 2.6) before any write.
        metadata: Free-form bag of extras. Carried through writes via
            the package-level metadata sidecar in Phase 3.

    Examples:
        Construct directly from in-memory records via an engine::

            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> expr = e.from_records([{"a": 1}, {"a": 2}, {"a": 3}])
            >>> t = Table(name="t", expr=expr, engine=e)
            >>> t.count()
            3
            >>> t.dirty
            False
    """

    name: str
    expr: TableExpr
    engine: Engine
    schema: Schema | None = None
    source: str | None = None
    format: str | None = None
    dirty: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lazy ops — return a new Table; original unchanged
    # ------------------------------------------------------------------

    def filter(self, predicate: Callable[[TableExpr], TableExpr]) -> Table:
        """Return a new :class:`Table` whose expression is ``predicate(expr)``.

        The predicate receives the engine-native expression and returns
        a new engine-native expression (typically a filtered one). The
        caller writes the predicate in the engine's own dialect — this
        method doesn't translate between engines.

        Args:
            predicate: Callable taking the current ``expr`` and
                returning a new expression of the same engine type.

        Returns:
            A new :class:`Table` wrapping the transformed expression;
            the original :class:`Table` is unchanged.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> expr = e.from_records([{"a": 1}, {"a": 2}, {"a": 3}])
            >>> t = Table(name="t", expr=expr, engine=e)
            >>> t2 = t.filter(lambda df: df.filter(df["a"] > 1))
            >>> t2 is t
            False
            >>> t.count(), t2.count()
            (3, 2)
        """
        return self._derived(predicate(self.expr))

    def select(self, *columns: str) -> Table:
        """Return a new :class:`Table` projected to the named ``columns``.

        Args:
            *columns: Column names to keep. Must all exist on the
                current ``expr``.

        Returns:
            A new :class:`Table` carrying only the projected columns.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> expr = e.from_records([{"a": 1, "b": 2}])
            >>> Table(name="t", expr=expr, engine=e).select("a").columns()
            ['a']
        """
        cols = list(columns)
        return self._derived(self.engine.select(self.expr, cols))

    def head(self, n: int = 5) -> Table:
        """Return a new :class:`Table` containing only the first ``n`` rows.

        Lazy — the underlying engine builds a head expression, but no
        materialization runs until the caller asks for it.

        Args:
            n: Row count to keep (default 5).

        Returns:
            A new :class:`Table` whose expression is the head.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> expr = e.from_records([{"a": i} for i in range(10)])
            >>> Table(name="t", expr=expr, engine=e).head(3).count()
            3
        """
        return self._derived(self.engine.head(self.expr, n))

    def order_by(self, *columns: str, descending: bool = False) -> Table:
        """Return a new :class:`Table` sorted by ``columns``.

        Lazy — the engine builds an ORDER BY expression (ibis/polars) or
        sorts the frame (pandas). With :meth:`limit` this is the
        server-side paging primitive the viewer's data-table view uses, so
        a single page is fetched without materializing the whole table.

        Args:
            *columns: Column names to sort by, in priority order.
            descending: Sort descending instead of ascending (applies to
                all ``columns``).

        Returns:
            A new :class:`Table`; the original is unchanged.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": 3}, {"a": 1}, {"a": 2}]), engine=e)
            >>> t.order_by("a").to_pandas()["a"].tolist()
            [1, 2, 3]
        """
        return self._derived(self.engine.order_by(self.expr, list(columns), descending))

    def limit(self, n: int, offset: int = 0) -> Table:
        """Return a new :class:`Table` of ``n`` rows starting at ``offset``.

        Lazy — ibis emits ``LIMIT n OFFSET offset``, polars ``.slice``,
        pandas positional ``.iloc``. Compose with :meth:`order_by` for
        stable server-side paging.

        Args:
            n: Maximum number of rows to keep.
            offset: Number of leading rows to skip (default 0).

        Returns:
            A new :class:`Table`; the original is unchanged.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": i} for i in range(5)]), engine=e)
            >>> t.limit(2, offset=2).to_pandas()["a"].tolist()
            [2, 3]
        """
        return self._derived(self.engine.limit(self.expr, n, offset))

    # ------------------------------------------------------------------
    # Materialization
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the row count, pushing down to the engine where possible.

        Delegates to :meth:`Engine.count`, which routes through each
        backend's native cheap-count path (``ibis`` pushes a
        ``SELECT COUNT(*)`` to duckdb; ``polars`` selects ``pl.len()``
        inside the lazy plan; ``pandas`` calls ``len`` on the already-
        materialized frame). No table is materialized just to count it.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> Table(name="t", expr=e.from_records([{"a": 1}]), engine=e).count()
            1
        """
        return self.engine.count(self.expr)

    def to_pandas(self) -> pd.DataFrame:
        """Materialize as a ``pandas.DataFrame`` using the engine's converter.

        The returned frame uses the cross-engine **nullable numpy-backed
        dtype** family (``Int64`` / ``Float64`` / ``string`` /
        ``boolean``) regardless of which engine produced the expression
        — see :meth:`corral.engines.base.Engine.to_pandas` for the
        full contract.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": 1}]), engine=e)
            >>> t.to_pandas()["a"].tolist()
            [1]
        """
        return self.engine.to_pandas(self.expr)

    def to_polars(self) -> pl.DataFrame:
        """Materialize as a ``polars.DataFrame`` using the engine's converter.

        Raises :class:`~corral.engines.errors.EngineNotAvailableError`
        if polars is not installed and the engine cannot satisfy the
        conversion.

        Examples:
            >>> import pytest
            >>> _ = pytest.importorskip("polars")
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": 1}]), engine=e)
            >>> t.to_polars().shape[0]
            1
        """
        return self.engine.to_polars(self.expr)

    def collect(self) -> TableExpr:
        """Force eager materialization; return the engine-native frame.

        For ibis this triggers backend execution and returns a pyarrow
        ``Table``; for polars this collects the ``LazyFrame`` to a
        ``DataFrame``; for pandas this is essentially an identity
        (pandas is already eager).

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": 1}]), engine=e)
            >>> out = t.collect()
            >>> out is not t
            True
        """
        return self.engine.materialize(self.expr)

    # ------------------------------------------------------------------
    # Mutation surface (marks dirty)
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Manually mark the table dirty.

        Use after a direct mutation that bypassed the editing
        framework (e.g. the caller poked the underlying engine frame
        outside this wrapper). The sync-state tracker reads
        :attr:`dirty` before the next write.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> t = Table(name="t", expr=e.from_records([{"a": 1}]), engine=e)
            >>> t.invalidate()
            >>> t.dirty
            True
        """
        self.dirty = True

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def columns(self) -> list[str]:
        """Return the column names of the underlying expression.

        Delegates to :meth:`Engine.columns`, which uses the engine's
        lazy schema introspection (ibis ``expr.schema().names``, polars
        ``expr.collect_schema().names()``, pandas ``list(expr.columns)``).
        No row materialization runs.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> Table(name="t", expr=e.from_records([{"a": 1, "b": 2}]), engine=e).columns()
            ['a', 'b']
        """
        return self.engine.columns(self.expr)

    def __repr__(self) -> str:
        """One-line summary: name, format if known, row count if cheap, dirty flag."""
        bits: list[str] = [f"name={self.name!r}"]
        if self.format:
            bits.append(f"format={self.format!r}")
        if self.source:
            bits.append(f"source={self.source!r}")
        bits.append("dirty" if self.dirty else "clean")
        return f"Table({', '.join(bits)})"

    def _repr_html_(self) -> str:
        """Render a Jupyter-friendly card summarizing the table.

        Header carries the table name plus a ``dirty`` / ``clean``
        badge; the body shows the engine, the row count
        (:meth:`count` — pushes down to the engine), and the column
        list truncated to twelve names with a "…+N more" note when
        there are more. The card composes via
        :func:`corral.notebook.card`.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Table
            >>> e = IbisEngine()
            >>> html = Table(name="t", expr=e.from_records([{"a": 1}]), engine=e)._repr_html_()
            >>> html.startswith("<div")
            True
            >>> "Table" in html
            True
        """
        # Local import — keeps the dataset → notebook edge load-time
        # free and confines the dependency to the render path.
        from corral.notebook import card, escape, kv_line, truncation_note

        return _render_table_card(self, card, escape, kv_line, truncation_note)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _derived(self, new_expr: TableExpr) -> Table:
        """Build a child :class:`Table` carrying the same identity metadata.

        The child inherits ``name``, ``engine``, ``schema``, ``source``,
        ``format``, and a shallow copy of ``metadata``. It starts
        ``dirty=False`` — a transform doesn't imply a mutation. Callers
        flip ``dirty`` themselves (or use :meth:`invalidate`) when they
        intend the result to be sync-state-relevant.
        """
        return Table(
            name=self.name,
            expr=new_expr,
            engine=self.engine,
            schema=self.schema,
            source=self.source,
            format=self.format,
            dirty=False,
            metadata=dict(self.metadata),
        )


def _html_escape(value: str) -> str:
    """Tiny stdlib HTML escape — kept inline for the legacy module surface."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Notebook card rendering
# ---------------------------------------------------------------------------

#: Cap on how many columns to show in the notebook card before
#: collapsing the rest into a "…+N more" note. Twelve is wide enough
#: for a typical GMNS table header line at default font size.
_TABLE_COLUMN_PREVIEW = 12


def _render_table_card(
    table: Table,
    card: Any,
    escape: Any,
    kv_line: Any,
    truncation_note: Any,
) -> str:
    """Render the per-:class:`Table` notebook card.

    Lives at module level so callers (the method on :class:`Table` plus
    any subclass that wants to reuse the layout) can share the
    column-truncation + dirty-badge logic.
    """
    try:
        all_cols = list(table.columns())
    except (RuntimeError, OSError, ValueError):
        all_cols = []
    preview_cols = all_cols[:_TABLE_COLUMN_PREVIEW]
    cols_html = ", ".join(escape(c) for c in preview_cols) if preview_cols else "<em>(no columns)</em>"
    extra_cols = max(0, len(all_cols) - len(preview_cols))
    if extra_cols:
        cols_html += f" <span style='color:#656d76;'>(+{extra_cols} more)</span>"

    try:
        row_count: object = int(table.count())
    except (RuntimeError, OSError, ValueError):
        row_count = "?"

    badge_word = "dirty" if table.dirty else "clean"
    kv_items: list[tuple[str, object]] = [
        ("engine", getattr(table.engine, "name", type(table.engine).__name__)),
        ("rows", row_count),
        ("cols", len(all_cols) if all_cols else "?"),
        ("state", badge_word),
    ]
    if table.format:
        kv_items.append(("format", table.format))

    body = (
        kv_line(kv_items)
        + f'<div style="margin-top:4px;"><span style="color:#656d76;">columns:</span> {cols_html}</div>'
        + truncation_note(0)  # no-op; column truncation already inlined above
    )
    subtitle = table.source or ""
    return card(f"Table: {table.name}", body, subtitle=subtitle)
