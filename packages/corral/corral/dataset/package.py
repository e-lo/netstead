"""Lazy multi-table package wrapper — see :class:`Package`.

A :class:`Package` bundles the parsed
:class:`~corral.spec.model.DataPackage` spec, a mapping of
:class:`~corral.dataset.Table` instances, the
:class:`~corral.engines.base.Engine` that produced them, the
original ``source`` locator for round-trip writes, and an optional
:class:`~corral.validation.sync_state.DirtyTracker`.

Key invariants:

* Lazy by default — table expressions stay engine-native until
  someone asks for materialisation (``Table.to_pandas`` /
  ``.collect`` / ``.count``).
* All format dispatch routes through :mod:`corral.io`; this
  module never embeds a format-specific branch.
* The dirty-tracker is optional — every sync-aware code path no-ops
  cleanly when the tracker is absent.

See :meth:`Package.from_source` for source dispatch order and
:meth:`Package.validate` / :meth:`Package.write` for the sync-state
contract.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import warnings
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa

from corral.engines import get_engine
from corral.io import (
    FormatNotDetected,
    ResourceListing,
    ResourceRef,
    WriteUnsupportedForSchemeError,
    _scheme_of,
    dispatch,
    get_adapter,
    list_adapters,
)
from corral.reports import Category, Issue, Severity, ValidationReport
from corral.spec.loader import load_package
from corral.spec.model import DataPackage, Resource, Schema
from corral.validation import (
    check_foreign_keys,
    check_structural,
)
from corral.validation.schema_check import check_schema

from .errors import PackageError
from .table import Table

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine
    from corral.io.remote import RemoteAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional sync-state import — task 2.6 may or may not have landed yet.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - exercised by the dirty-tracker-optional test
    from corral.validation.sync_state import DirtyTracker as _DirtyTrackerImpl
except ImportError:  # pragma: no cover - task 2.6 not landed yet
    _DirtyTrackerImpl = None  # type: ignore[assignment]


# Re-exported at module level so callers can type-annotate against it
# without caring whether 2.6 has merged. When the import fails, the
# alias is ``None`` and ``Package`` accepts ``None`` as the tracker.
DirtyTracker = _DirtyTrackerImpl


__all__ = ["DirtyTracker", "OutOfSyncError", "OutOfSyncWarning", "Package", "PackageError"]


# ---------------------------------------------------------------------------
# Sync-state errors
# ---------------------------------------------------------------------------


class OutOfSyncWarning(Warning):
    """Raised when :meth:`Package.write` runs against a stale package.

    Demoted from an error to a warning by default so a routine save
    after an unintentional edit still produces a file (with a loud
    warning). Promote to :class:`OutOfSyncError` via
    ``strict_sync=True``.
    """


class OutOfSyncError(Exception):
    """Promoted version of :class:`OutOfSyncWarning`.

    Raised by :meth:`Package.write` when ``strict_sync=True`` and the
    sync-state check detects at least one stale table.
    """


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------


@dataclass
class Package:
    """Lazy wrapper around a multi-table Frictionless data package.

    Composes the spec, the table mapping, the engine, the source
    locator, and (optionally) the sync-state :class:`DirtyTracker`. The
    primary constructors are :meth:`from_source` (load from a path/URL)
    and :meth:`from_tables` (compose from already-built
    :class:`~corral.dataset.Table` instances).

    Attributes:
        spec: The parsed :class:`~corral.spec.model.DataPackage`.
        tables: Mapping ``{name: Table}``. Insertion order is preserved
            (Python 3.7+) so iteration is deterministic.
        engine: The :class:`~corral.engines.base.Engine` that
            produced the table expressions. All materialisation routes
            back through this same engine.
        source: Original source identifier (path / URL). ``None`` for
            in-memory packages built via :meth:`from_tables`.
        dirty_tracker: Optional sync-state tracker. When ``None``, the
            sync-state validation pass is a no-op and :meth:`write`
            consults only the per-table ``dirty`` flag.
        metadata: Free-form bag of extras (engine name, write
            timestamp, etc.); echoed into the JSON validation report.
        spec_missing: ``True`` when :meth:`from_source` found no spec
            (no ``spec=`` and no ``datapackage.json`` beside the source)
            and used an empty one. :meth:`validate` then reports
            ``structural.spec_missing`` so the gap is not mistaken for a
            clean pass.

    Examples:
        Load the bundled generic sample fixture, validate, then
        write to a fresh parquet directory::

            >>> import tempfile, pathlib
            >>> from corral.fixtures import sample
            >>> from corral.dataset import Package
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["book", "author"],
            ... )
            >>> "book" in pkg
            True
            >>> report = pkg.validate()
            >>> isinstance(report.issues, list)
            True
    """

    spec: DataPackage
    tables: dict[str, Table] = field(default_factory=dict)
    engine: Engine | None = None
    source: str | None = None
    dirty_tracker: Any = None  # DirtyTracker | None — Any because the type may be unimportable.
    metadata: dict[str, Any] = field(default_factory=dict)
    spec_missing: bool = False

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_source(
        cls,
        source: str | Path,
        *,
        engine: Engine | None = None,
        spec: DataPackage | str | Path | None = None,
        tables: Iterable[str] | None = None,
        format: str | None = None,
        credentials: dict[str, str] | None = None,
    ) -> Package:
        """Build a :class:`Package` from a source path/URL.

        Dispatch order (per ``docs/architecture.md`` §6.1 + the
        :mod:`corral.io` package docstring):

            1. Resolve ``engine`` (default: registry default).
            2. Resolve ``spec`` — explicit :class:`DataPackage` wins;
               else look for a ``datapackage.json`` alongside ``source``;
               else synthesise a minimal spec from whatever resources
               are discovered.
            3. Resolve ``source`` to a :class:`ResourceListing`:

                a. explicit ``format=`` short-circuits sniffing;
                b. URL scheme (``duckdb://``, ``s3://``, ...);
                c. file extension (``.csv``, ``.parquet``, ``.duckdb``,
                   ``.csv.zip``);
                d. directory-of-known-formats walk (``csv/``,
                   ``parquet/``) — directories have no extension to
                   dispatch on, so we walk children and ask each
                   matching adapter to scan one. Mirrors
                   :func:`corral.validation.structural._scan_directory_of_known_formats`
                   so the structural validator and the package loader
                   can't drift apart on "what counts as a directory of
                   tables".
                e. ``probe`` chain (any adapter that recognises the
                   source);
                f. :class:`~corral.io.FormatNotDetected`.

            4. For each :class:`~corral.io.base.ResourceRef`, ask
               the owning adapter to ``read`` it lazily through
               ``engine``, then wrap the resulting
               :class:`~corral.engines.base.TableExpr` in a
               :class:`~corral.dataset.Table`. The spec is
               consulted for the matching
               :class:`~corral.spec.model.Schema` so the validation
               layer doesn't have to re-load it.

        Args:
            source: Path / URL / directory pointing at the data
                package. Anything :func:`corral.io.dispatch` knows
                about, plus the directory-of-files convention.
            engine: Engine to materialise through. Defaults to the
                registered default (typically
                :class:`~corral.engines.ibis_engine.IbisEngine`).
            spec: Either an in-memory :class:`DataPackage`, a path /
                URL to a ``datapackage.json``, or ``None`` to
                auto-discover.
            tables: Optional subset of resource names to load. Useful
                for memory-efficient partial loads.
            format: Optional adapter-name override for the top-level
                source. Used when the source has no extension to sniff
                on (e.g. an API endpoint that returns CSV but has no
                ``.csv`` suffix). For ``s3://`` / ``https://`` URLs
                this becomes the inner-format hint passed through
                :class:`~corral.io.remote.RemoteAdapter`.
            credentials: Optional explicit credentials dict forwarded
                to :class:`~corral.io.remote.RemoteAdapter`, for the
                listing of a URL prefix and for every table read. Only
                the remote adapter consumes it; local-fs reads ignore
                the kwarg. Caller-provided keys win over env / keyring /
                netrc — see
                :func:`corral.io.credentials.resolve_credentials`.

        Returns:
            A populated :class:`Package` with lazy :class:`Table`
            entries.

        Raises:
            FormatNotDetected: If neither extension dispatch nor the
                directory walk could resolve ``source``.

        Examples:
            >>> from corral.fixtures import sample
            >>> from corral.dataset import Package
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["book"],
            ... )
            >>> list(pkg.keys())
            ['book']
        """
        eng = engine if engine is not None else get_engine()
        source_str = str(source)
        # 1. Resolve spec.
        found_spec = _resolve_spec(spec, source_str)
        # Without a spec, tables still load, but nothing can be checked
        # against a schema; keep an empty spec and flag it for validate().
        resolved_spec = found_spec if found_spec is not None else DataPackage(name="auto", resources=[])

        # 2. Discover resources. ``format=`` narrows the top-level
        #    dispatch; the directory-of-known-formats walk still uses
        #    per-child extension dispatch (no per-child override surface).
        listing = _scan_source(source_str, format=format, credentials=credentials)

        # 3. Filter by requested subset (if any).
        wanted: set[str] | None = set(tables) if tables is not None else None
        if wanted is not None:
            listing = [ref for ref in listing if ref.name in wanted]

        # 4. Build the Table mapping by deferring each read to the
        #    appropriate adapter. The adapter calls the engine's
        #    per-format primitive — we never embed format dispatch here.
        tables_out: dict[str, Table] = {}
        for ref in listing:
            schema = _schema_for(resolved_spec, ref.name)
            # Remote refs route through RemoteAdapter so the credentials
            # cascade (kwarg → env → keyring → netrc) and fsspec
            # storage_options wiring stay in one place. The remote adapter
            # then delegates to the inner format adapter using the URL's
            # extension (or explicit ``format=``). Without this branch a
            # ResourceRef like ("foo", "s3://b/foo.csv", "csv") would land
            # on CsvAdapter directly and lose the credentials path.
            adapter = get_adapter("remote") if _looks_remote(ref.path) else get_adapter(ref.format)
            read_kwargs: dict[str, Any] = {}
            # Pass credentials through only when (a) caller set them and
            # (b) we routed to the remote adapter — local adapters
            # forward unknown kwargs to engine.read_csv / read_parquet,
            # where they'd raise TypeError.
            if credentials is not None and adapter.name == "remote":
                read_kwargs["credentials"] = credentials
            # Members of multi-table containers (duckdb file, zip of csvs)
            # are marked by their scanning adapter; ``ref.format`` names the
            # *member* format (a zip member is "csv"), so the read goes back
            # to the recorded container adapter with ``table=<member>``.
            # Path text alone is never trusted: "a::b.csv" is a legal filename.
            # A remote container (a zip at a URL) still reads through the
            # remote adapter, told the container's format, so credentials apply.
            if ref.container_adapter is not None:
                read_kwargs["table"] = ref.member
                if adapter.name == "remote":
                    read_kwargs["format"] = ref.container_adapter
                else:
                    adapter = get_adapter(ref.container_adapter)
                expr = adapter.read(ref.container, engine=eng, schema=schema, **read_kwargs)
            else:
                expr = adapter.read(ref.path, engine=eng, schema=schema, **read_kwargs)
            tables_out[ref.name] = Table(
                name=ref.name,
                expr=expr,
                engine=eng,
                schema=schema,
                source=ref.path,
                format=ref.format,
            )

        return cls(
            spec=resolved_spec,
            tables=tables_out,
            engine=eng,
            source=source_str,
            spec_missing=found_spec is None,
        )

    @classmethod
    def from_tables(
        cls,
        tables: Mapping[str, Table],
        *,
        spec: DataPackage | None = None,
        engine: Engine | None = None,
    ) -> Package:
        """Build a :class:`Package` from already-constructed :class:`Table` instances.

        Useful for tests, ad-hoc compositions, and adapter-less in-memory
        sources. When ``spec`` is not provided, a minimal one is
        synthesised so :meth:`validate` and the dict-like surface still
        work.

        Args:
            tables: Mapping ``{name: Table}``.
            spec: Optional explicit :class:`DataPackage`. Synthesised
                from ``tables`` when ``None``.
            engine: Optional explicit engine. Defaults to the first
                table's engine.

        Returns:
            A new :class:`Package`.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> link = Table(name="link", expr=e.from_records([{"id": 1}]), engine=e)
            >>> p = Package.from_tables({"link": link})
            >>> "link" in p
            True
        """
        if engine is None:
            engine = next(iter(tables.values())).engine if tables else get_engine()
        if spec is None:
            spec = DataPackage(
                name="synthesized",
                resources=[Resource(name=name) for name in tables],
            )
        return cls(spec=spec, tables=dict(tables), engine=engine)

    # ------------------------------------------------------------------
    # Dict-like access
    # ------------------------------------------------------------------

    def __getitem__(self, name: str) -> Table:
        """Return the :class:`Table` named ``name``.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> t = Table(name="link", expr=e.from_records([{"id": 1}]), engine=e)
            >>> Package.from_tables({"link": t})["link"].name
            'link'
        """
        return self.tables[name]

    def __contains__(self, name: object) -> bool:
        """``True`` if ``name`` is a known table name."""
        return name in self.tables

    def __iter__(self):
        """Iterate over table names in insertion order."""
        return iter(self.tables)

    def __len__(self) -> int:
        """Return the number of tables."""
        return len(self.tables)

    def keys(self) -> list[str]:
        """Return the table names as a list (insertion order)."""
        return list(self.tables.keys())

    def values(self) -> list[Table]:
        """Return the tables as a list (insertion order)."""
        return list(self.tables.values())

    def items(self) -> list[tuple[str, Table]]:
        """Return ``(name, table)`` pairs as a list (insertion order)."""
        return list(self.tables.items())

    def safe_count(self, name: str) -> int | None:
        """Return ``self[name].count()`` or ``None`` if absent / transiently uncountable.

        Convenience used by previews (notebook ``_repr_html_``, CLI
        ``info``, HTTP ``/networks/{id}``) where a missing or
        transiently-broken table should degrade to a friendly "?"
        rather than crash the surrounding render.

        The contract: returns ``None`` when the table is absent OR
        when ``table.count()`` raises any :class:`Exception` (e.g. a
        transient backend hiccup on an ibis-against-remote-duckdb
        connection). Hard exceptions in surrounding code still
        propagate — this only shields the count call itself.

        Examples:
            >>> from corral.dataset import Package, Table
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> e = IbisEngine()
            >>> pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}]), engine=e)})
            >>> pkg.safe_count("x")
            1
            >>> pkg.safe_count("absent_table") is None
            True
        """
        table = self.tables.get(name)
        if table is None:
            return None
        try:
            return int(table.count())
        except Exception:  # pragma: no cover - resilient preview path
            return None

    # ------------------------------------------------------------------
    # Validation orchestration
    # ------------------------------------------------------------------

    def validate(
        self,
        *,
        schema: bool = True,
        structural: bool = True,
        foreign_keys: bool = True,
        sync_state: bool = True,
        strict: bool = False,
    ) -> ValidationReport:
        """Run the selected validation passes and return the combined report.

        Order: structural → schema (per table) → foreign_keys →
        sync_state. Each pass appends issues to the same report. Skip
        any pass by passing ``False`` for its flag.

        Sync-state contract: after a clean FK pass, the
        :class:`DirtyTracker` (when attached) stamps each
        non-violating FK via :meth:`DirtyTracker.stamp_fk_from_exprs`
        so subsequent edits surface ``sync.fk_stale`` instead of
        silently writing the broken FK. The ``sync_state`` pass then
        consults :meth:`DirtyTracker.check` to report any stamps that
        no longer match the live tables. No-op when ``dirty_tracker``
        is ``None``.

        Args:
            schema: Run per-table schema validation
                (:func:`corral.validation.check_schema`).
            structural: Run structural validation
                (:func:`corral.validation.check_structural`).
            foreign_keys: Run FK validation
                (:func:`corral.validation.check_foreign_keys`).
            sync_state: Consult the :class:`DirtyTracker` (when attached)
                via its ``check`` method. No-op when no tracker is
                attached.
            strict: Forwarded to validators that accept it (FK
                ``unverifiable`` becomes ERROR instead of WARNING).

        Returns:
            A :class:`~corral.validation.ValidationReport` populated
            with one :class:`~corral.validation.Issue` per finding.

        Examples:
            >>> from corral.fixtures import sample
            >>> from corral.dataset import Package
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> pkg = Package.from_source(
            ...     sample.csv_dir(),
            ...     engine=IbisEngine(),
            ...     spec=sample.DATAPACKAGE,
            ...     tables=["book", "author"],
            ... )
            >>> r = pkg.validate()
            >>> isinstance(r.issues, list)
            True
        """
        report = ValidationReport(source=self.source)

        # Structural: what the spec says should be here vs. what we
        # actually loaded. The actual_resources list is built from the
        # loaded Tables (not a fresh scan) so partial loads don't
        # spuriously look "missing".
        if structural:
            actual: ResourceListing = [
                ResourceRef(name=t.name, path=t.source or t.name, format=t.format or "") for t in self.tables.values()
            ]
            check_structural(self.spec, source=self.source, actual_resources=actual, report=report)
            if self.spec_missing:
                report.add_issue(
                    Issue(
                        severity=Severity.INFO,
                        category=Category.STRUCTURAL,
                        code="structural.spec_missing",
                        message=(
                            f"package source {self.source!r} has no datapackage.json and no spec was "
                            f"given, so no table was checked against a schema"
                        ),
                        fix_hint="Pass spec= to Package.from_source, or put a datapackage.json beside the data.",
                    )
                )

        # Schema: per-table per-field rules. Iterates only over loaded
        # tables (a partial load skips schemas it can't run).
        if schema and self.engine is not None:
            for table in self.tables.values():
                if table.schema is None:
                    continue
                check_schema(
                    table.expr,
                    table.schema,
                    table_name=table.name,
                    report=report,
                )

        # Foreign keys: needs the full table mapping.
        if foreign_keys and self.engine is not None:
            check_foreign_keys(
                self.spec,
                {name: t.expr for name, t in self.tables.items()},
                report=report,
                strict=strict,
            )
            # Stamp every cleanly-validated FK on the DirtyTracker so a
            # later mutation surfaces ``sync.fk_stale`` instead of
            # silently writing the broken FK. This is the "valve" the
            # tracker was built for — the FK validator deliberately
            # doesn't depend on the hash infrastructure, so the seam
            # lives here in the package orchestrator.
            if self.dirty_tracker is not None:
                self._stamp_clean_fks(report)

        # Sync state: only meaningful with a tracker. The contract
        # (`DirtyTracker.check`) is documented on task 2.6; we keep
        # the call site minimal so the tracker can grow surfaces
        # without forcing us back here.
        if sync_state and self.dirty_tracker is not None and self.engine is not None:
            self.dirty_tracker.check(
                {name: t.expr for name, t in self.tables.items()},
                engine=self.engine,
                report=report,
                strict=strict,
            )

        return report

    # ------------------------------------------------------------------
    # Scope (table + column subset)
    # ------------------------------------------------------------------

    def scope(
        self,
        *,
        tables: Iterable[str] | None = None,
        columns: Mapping[str, list[str]] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        polygon: Any = None,
        geometry_buffer: tuple[Any, float] | None = None,
        geometry_column: str = "geometry",
    ) -> Package:
        """Return a new :class:`Package` restricted to a table / column / spatial subset.

        Composes table+column subsetting with the spatial scopes in
        :mod:`corral.dataset.view`. The spatial kwargs (``bbox``,
        ``polygon``, ``geometry_buffer``) apply to **every** loaded
        table that carries ``geometry_column``; tables without that
        column are passed through untouched (link/node/use_definition
        in the GMNS convention only the ``geometry`` table holds WKT,
        so an ``bbox=`` scope effectively filters geometry and the
        caller relies on FK trimming in a follow-up pass).

        Exactly one of ``bbox`` / ``polygon`` / ``geometry_buffer`` may
        be supplied per call. The returned :class:`Package` shares the
        same spec + engine + dirty tracker; only the table mapping is
        filtered.

        Args:
            tables: Optional iterable of table names to keep.
            columns: Optional ``{table_name: [col, ...]}`` projection.
            bbox: ``(minx, miny, maxx, maxy)`` — forwarded to
                :func:`corral.dataset.view.from_bbox`.
            polygon: A shapely (multi-)polygon or WKT string —
                forwarded to :func:`corral.dataset.view.from_polygon`.
            geometry_buffer: ``(geometry, distance_m)`` — forwarded to
                :func:`corral.dataset.view.from_geometry_buffer`.
            geometry_column: WKT/WKB column name shared across the
                spatial calls. Default ``"geometry"``.

        Returns:
            A new :class:`Package` carrying the subset.

        Raises:
            ValueError: If more than one spatial scope kwarg is set.

        Examples:
            Table subset (the original surface)::

                >>> from corral.fixtures import sample
                >>> from corral.dataset import Package
                >>> from corral.engines.ibis_engine import IbisEngine
                >>> pkg = Package.from_source(
                ...     sample.csv_dir(),
                ...     engine=IbisEngine(),
                ...     spec=sample.DATAPACKAGE,
                ...     tables=["book", "author"],
                ... )
                >>> pkg.scope(tables=["book"]).keys()
                ['book']
        """
        from .view import from_bbox, from_geometry_buffer, from_polygon

        spatial_count = sum(1 for x in (bbox, polygon, geometry_buffer) if x is not None)
        if spatial_count > 1:
            raise ValueError("Package.scope: at most one of bbox=, polygon=, geometry_buffer= may be set per call.")

        # Table filter.
        if tables is not None:
            wanted = set(tables)
            scoped = {name: t for name, t in self.tables.items() if name in wanted}
        else:
            scoped = dict(self.tables)

        # Column filter — only touches the named tables.
        if columns:
            for table_name, cols in columns.items():
                if table_name in scoped:
                    scoped[table_name] = scoped[table_name].select(*cols)

        # Spatial filter — applied to every table that has the geometry
        # column. ``from_*`` no-ops on tables that don't, so this is
        # safe to fan out blindly.
        if bbox is not None:
            minx, miny, maxx, maxy = bbox
            scoped = {
                name: from_bbox(t, minx, miny, maxx, maxy, geometry_column=geometry_column)
                for name, t in scoped.items()
            }
        elif polygon is not None:
            scoped = {name: from_polygon(t, polygon, geometry_column=geometry_column) for name, t in scoped.items()}
        elif geometry_buffer is not None:
            geom, distance_m = geometry_buffer
            scoped = {
                name: from_geometry_buffer(t, geom, distance_m, geometry_column=geometry_column)
                for name, t in scoped.items()
            }

        return Package(
            spec=self.spec,
            tables=scoped,
            engine=self.engine,
            source=self.source,
            dirty_tracker=self.dirty_tracker,
            metadata=dict(self.metadata),
            spec_missing=self.spec_missing,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(
        self,
        dest: str | Path,
        *,
        format: str | None = None,
        overwrite: bool = False,
        strict_sync: bool = False,
    ) -> None:
        """Persist every table in the package to ``dest``.

        Sync-state contract: before writing, consults two signals per
        table — the per-:class:`Table` ``dirty`` flag (always
        available) and :meth:`DirtyTracker.is_table_dirty` (when a
        tracker is attached). EITHER signal flags the table as stale
        — the union is the conservative thing to surface. Stale tables
        trigger :class:`OutOfSyncWarning`, or :class:`OutOfSyncError`
        under ``strict_sync=True``. When no tracker is attached, the
        per-table flag alone is consulted, so a caller can still flag
        a mutation via :meth:`Table.invalidate` without installing the
        tracker.

        Format dispatch goes through :mod:`corral.io` exactly as on
        the read path:

        * ``format`` explicit → adapter resolved by name;
        * else inferred from ``dest`` extension;
        * else default to ``"parquet"`` when ``dest`` looks like a
          directory.

        For partitioned/multi-table formats (parquet directory, duckdb
        file), each :class:`Table` writes to a per-table location under
        ``dest``. The exact layout matches the read-side scan so a
        round-trip via :meth:`from_source` returns the same logical
        package. A parquet or csv directory also gets a
        ``datapackage.json`` with inlined schemas, so a directory source
        rewritten over itself keeps its descriptor. A zip (``.zip`` /
        ``.csv.zip``, or ``format="zip"``) holds one flat ``<table>.csv``
        member per table plus the same ``datapackage.json``.

        Every format is staged beside ``dest`` and swapped into place
        once the write is complete. The tables are lazy expressions
        that may still read from ``dest`` itself (writing a package
        back over its own source), so nothing at ``dest`` is removed
        before the new files exist, and a failed write leaves an
        existing ``dest`` intact.

        Args:
            dest: Target directory or file.
            format: Optional explicit format name (an adapter name;
                ``"zip"`` is accepted as an alias for ``"zipcsv"``).
            overwrite: When ``False`` (the default), refuses to clobber
                an existing ``dest``. When ``True``, replaces the existing
                target once the new files are complete.
            strict_sync: When ``True``, stale tables raise
                :class:`OutOfSyncError` instead of warning.

        Raises:
            FileExistsError: When ``dest`` exists and
                ``overwrite=False``.
            OutOfSyncError: When ``strict_sync=True`` and any table is
                dirty.
            PackageError: When the package has no engine attached (so
                materialisation can't run).
            FormatNotDetected: When ``format`` is not given and the
                ``dest`` extension is unknown — the user must pass
                ``format=`` explicitly to disambiguate.
            WriteUnsupportedForSchemeError: When ``dest`` is a URL —
                packages are written to local paths only.

        Examples:
            Roundtrip the bundled sample fixture through parquet::

                >>> import tempfile, pathlib
                >>> from corral.fixtures import sample
                >>> from corral.dataset import Package
                >>> from corral.engines.ibis_engine import IbisEngine
                >>> with tempfile.TemporaryDirectory() as tmp:
                ...     pkg = Package.from_source(
                ...         sample.csv_dir(),
                ...         engine=IbisEngine(),
                ...         spec=sample.DATAPACKAGE,
                ...         tables=["book"],
                ...     )
                ...     out = pathlib.Path(tmp) / "out.pkg"
                ...     pkg.write(out, format="parquet")
                ...     out.exists()
                True
        """
        if self.engine is None:
            raise PackageError(
                "Package has no engine attached; cannot write. "
                "Build the package via Package.from_source(..., engine=...) "
                "or Package.from_tables(..., engine=...) before calling write()."
            )

        # 1. Sync-state pre-check. Any dirty signal trips warning/error.
        stale = self._stale_tables()
        if stale:
            msg = (
                f"Package source {self.source!r} has {len(stale)} stale table(s): "
                f"{sorted(stale)}. Writing may produce a snapshot whose FK "
                f"validations are out of date."
            )
            if strict_sync:
                raise OutOfSyncError(msg)
            warnings.warn(msg, OutOfSyncWarning, stacklevel=2)

        # 2. Resolve target format. Explicit wins; else sniff dest tail;
        #    else default to parquet (the recommended persistent layout
        #    per architecture §6.1).
        if isinstance(dest, str) and (scheme := _scheme_of(dest)) is not None:
            # Path("s3://b/x") is the local relative path "s3:/b/x".
            raise WriteUnsupportedForSchemeError(
                f"Package.write: writing to a {scheme}:// URL is not supported; write to a local path instead"
            )
        dest_path = Path(dest)
        target_format = _resolve_write_format(dest_path, format)

        # 3. Overwrite guard. The existing ``dest`` stays in place until
        #    the staged write is complete (see step 4): the tables may
        #    still read from it.
        if dest_path.exists() and not overwrite:
            raise FileExistsError(f"Destination {dest_path!r} already exists. Pass overwrite=True to replace it.")
        if target_format == "zipcsv":
            _write_zipcsv_package(self, dest_path)
            return

        # 4. Per-table write into a staging directory beside ``dest``
        #    (same filesystem, so the final swap is a rename), then the
        #    swap. The staging directory, and the displaced old ``dest``
        #    moved into it, go away with the context.
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{dest_path.name}.", dir=dest_path.parent) as staging_dir:
            staged = Path(staging_dir) / dest_path.name
            _write_package_tables(self, staged, target_format)
            _swap_into_place(staged, dest_path, aside=Path(staging_dir) / f"{dest_path.name}.previous")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_table(self, name: str, table: Table) -> None:
        """Insert a new :class:`Table` under ``name`` (or replace an existing one).

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> p = Package.from_tables(
            ...     {"a": Table(name="a", expr=e.from_records([{"x": 1}]), engine=e)}
            ... )
            >>> p.add_table("b", Table(name="b", expr=e.from_records([{"y": 1}]), engine=e))
            >>> sorted(p.keys())
            ['a', 'b']
        """
        self.tables[name] = table

    def remove_table(self, name: str) -> None:
        """Remove the table named ``name``.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> p = Package.from_tables(
            ...     {"a": Table(name="a", expr=e.from_records([{"x": 1}]), engine=e)}
            ... )
            >>> p.remove_table("a")
            >>> "a" in p
            False
        """
        del self.tables[name]

    def batch(self, *, log_path: Path | str | None = None, strict: bool = False) -> Any:
        """Return a :class:`~corral.operations.Batch` bound to this package.

        Convenience wrapper for the deferred-mutation pattern from
        architecture §6.5::

            with pkg.batch() as b:
                b.add_edit(...)
                b.add_edit(...)
            # On exit: coalesced + applied atomically + validated once.

        Args:
            log_path: Optional sidecar path forwarded to the underlying
                :class:`~corral.editing.Session` (rollback-log file).
            strict: When ``True``, an ERROR-severity issue in the
                post-commit validation report triggers a Session
                rollback and re-raises as
                :class:`~corral.operations.BatchValidationError`.

        Examples:
            >>> from corral.dataset import Package, Table
            >>> from corral.editing import Edit
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> e = IbisEngine()
            >>> pkg = Package.from_tables(
            ...     {"t": Table(name="t", expr=e.from_records([{"id": 1}]), engine=e)}
            ... )
            >>> with pkg.batch() as b:
            ...     b.add_edit(Edit(op="add_rows", table="t", payload={"rows": [{"id": 2}]}))
            >>> pkg["t"].count()
            2
        """
        # Local import so the dataset → operations edge stays one-way
        # at module-load time (operations imports from editing + reports,
        # not the other way around).
        from corral.operations import Batch

        return Batch(self, log_path=log_path, strict=strict)

    def invalidate(self, name: str) -> None:
        """Mark a single table dirty.

        Convenience for :meth:`Table.invalidate` on the indexed entry.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> t = Table(name="a", expr=e.from_records([{"x": 1}]), engine=e)
            >>> p = Package.from_tables({"a": t})
            >>> p.invalidate("a")
            >>> p["a"].dirty
            True
        """
        self.tables[name].invalidate()
        if self.dirty_tracker is not None:
            mark = getattr(self.dirty_tracker, "mark_dirty", None)
            if callable(mark):
                mark(name)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """One-line summary: source, table count, dirty count."""
        n_dirty = sum(1 for t in self.tables.values() if t.dirty)
        bits = [f"source={self.source!r}" if self.source else "in_memory"]
        bits.append(f"tables={len(self.tables)}")
        if n_dirty:
            bits.append(f"dirty={n_dirty}")
        return f"Package({', '.join(bits)})"

    def _repr_html_(self) -> str:
        """Render a Jupyter-friendly card summarising the package.

        Header carries the package name (from
        :attr:`spec.name`) and the source locator; the body lists the
        engine and a small ``name | rows | cols`` table for the first
        eight tables, truncated with a "…+N more" note when there are
        more. Row counts use :meth:`Table.count` which pushes down to
        the engine, and column lists use the engine's lazy schema —
        nothing forces a full materialisation.

        The card composes via :func:`corral.notebook.card` so every
        ``_repr_html_`` on the public surface shares the same look.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.dataset import Package, Table
            >>> e = IbisEngine()
            >>> t = Table(name="a", expr=e.from_records([{"x": 1}]), engine=e)
            >>> html = Package.from_tables({"a": t})._repr_html_()
            >>> html.startswith("<div")
            True
            >>> "Package" in html
            True
        """
        # Local import to keep dataset/package.py's top-level imports
        # focused on dataset-layer deps. The notebook module is stdlib
        # only so the cost is negligible.
        from corral.notebook import card, kv_line, small_table, truncation_note

        return _render_package_card(self, card, kv_line, small_table, truncation_note)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stamp_clean_fks(self, report: ValidationReport) -> None:
        """Stamp every cleanly-validated FK on :attr:`dirty_tracker`.

        Walks :attr:`spec.resources` for every declared FK; for each
        FK whose validator did NOT emit a matching :class:`Issue`
        (category=FOREIGN_KEY, table=source, extra.target_table+field
        matching), call :meth:`DirtyTracker.stamp_fk_from_exprs` so the
        tracker can later detect drift.

        Composite FKs are stamped via :meth:`DirtyTracker.stamp_fk`
        with combined hashes computed here so we don't impose the
        composite case on :meth:`stamp_fk_from_exprs` (which is the
        single-column convenience).

        Safe to call when ``dirty_tracker`` is ``None`` (no-op) or
        when no FKs are present in the spec (no-op).
        """
        if self.dirty_tracker is None or self.engine is None:
            return
        stamp_fk_from_exprs = getattr(self.dirty_tracker, "stamp_fk_from_exprs", None)
        stamp_fk = getattr(self.dirty_tracker, "stamp_fk", None)
        if not callable(stamp_fk_from_exprs) or not callable(stamp_fk):
            return

        # Pre-bucket FK issues by (source_table, target_table,
        # target_field_spec) so the per-FK lookup is O(1).
        fk_issue_keys: set[tuple[str | None, str | None, str | None]] = set()
        for issue in report.issues:
            if issue.category != Category.FOREIGN_KEY:
                continue
            fk_issue_keys.add(
                (
                    issue.table,
                    issue.extra.get("target_table"),
                    issue.extra.get("target_field"),
                )
            )

        # Defer the heavy imports to avoid a load-time cycle into the
        # validation layer — only needed when we actually stamp.
        from corral.validation.sync_state import hash_column

        for resource in self.spec.resources:
            schema = resource.table_schema
            if schema is None or isinstance(schema, str):
                continue
            if not schema.foreign_keys:
                continue
            source_name = resource.name
            source_table = self.tables.get(source_name)
            if source_table is None:
                continue
            for fk in schema.foreign_keys:
                source_fields = [fk.fields] if isinstance(fk.fields, str) else list(fk.fields)
                target_fields_raw = fk.reference.fields
                target_fields = [target_fields_raw] if isinstance(target_fields_raw, str) else list(target_fields_raw)
                target_name = fk.reference.resource or source_name
                target_table = self.tables.get(target_name)
                if target_table is None:
                    continue  # FK validator already emitted fk.unverifiable
                target_field_spec = target_fields[0] if len(target_fields) == 1 else ",".join(target_fields)
                # Skip if the FK validator surfaced ANY issue against
                # this exact (source_table, target_table, target_field)
                # triple — a stale stamp on a known-broken FK would be
                # actively misleading.
                if (source_name, target_name, target_field_spec) in fk_issue_keys:
                    continue
                source_field_spec = source_fields[0] if len(source_fields) == 1 else ",".join(source_fields)
                try:
                    if len(source_fields) == 1 and len(target_fields) == 1:
                        stamp_fk_from_exprs(
                            source_name,
                            source_fields[0],
                            source_table.expr,
                            target_name,
                            target_fields[0],
                            target_table.expr,
                            engine=self.engine,
                        )
                    else:
                        # Composite FK — hash each component column and
                        # combine, matching _column_hash_from_expr in
                        # sync_state so stale-check uses the same recipe.
                        import hashlib

                        src_parts = [f"{c}:{hash_column(source_table.expr, c, self.engine)}" for c in source_fields]
                        tgt_parts = [f"{c}:{hash_column(target_table.expr, c, self.engine)}" for c in target_fields]
                        src_hash = hashlib.sha256("|".join(src_parts).encode("utf-8")).hexdigest()
                        tgt_hash = hashlib.sha256("|".join(tgt_parts).encode("utf-8")).hexdigest()
                        stamp_fk(
                            source_name,
                            source_field_spec,
                            target_name,
                            target_field_spec,
                            source_hash=src_hash,
                            target_hash=tgt_hash,
                        )
                except (KeyError, AttributeError, ValueError) as exc:
                    # Column missing / spec-vs-data mismatch — the FK
                    # validator would have surfaced this if it ran;
                    # don't fail the validate() call just because we
                    # couldn't compute a stamp.
                    logger.warning(
                        "could not stamp FK %s.%s -> %s.%s: %s",
                        source_name,
                        source_field_spec,
                        target_name,
                        target_field_spec,
                        exc,
                    )
        return

    def _stale_tables(self) -> set[str]:
        """Return the set of table names that look out-of-sync.

        Combines two signals:

        * the per-:class:`Table` ``dirty`` flag (always available);
        * the :class:`DirtyTracker`'s ``is_table_dirty`` (when a
          tracker is attached).

        Either signal alone flags the table as stale — the union of
        the two is the conservative thing to report to the user.
        """
        stale: set[str] = {name for name, t in self.tables.items() if t.dirty}
        if self.dirty_tracker is None or self.engine is None:
            return stale
        is_dirty = getattr(self.dirty_tracker, "is_table_dirty", None)
        if not callable(is_dirty):
            return stale
        for name, table in self.tables.items():
            try:
                if is_dirty(name, table.expr, self.engine):
                    stale.add(name)
            except (pa.ArrowInvalid, ValueError, AttributeError) as exc:
                # Hash-check raised against the live expression — most
                # plausibly because the table shape drifted from what
                # was stamped (schema change, dropped column). Treat as
                # stale: that's the conservative "warn the user, don't
                # crash" outcome. Real bugs (KeyError / TypeError) keep
                # propagating so they show up loudly in CI.
                logger.warning(
                    "DirtyTracker.is_table_dirty raised for %r; treating as stale: %s",
                    name,
                    exc,
                    exc_info=True,
                )
                stale.add(name)
        return stale


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _resolve_spec(
    spec: DataPackage | str | Path | None,
    source_str: str,
) -> DataPackage | None:
    """Resolve the ``spec`` argument to a :class:`DataPackage`.

    Order:
        1. Explicit :class:`DataPackage` — passed through.
        2. Explicit path / URL — loaded via :func:`load_package`.
        3. ``None`` — look for a sibling ``datapackage.json`` next to
           ``source_str``; if absent, log it and return ``None`` so
           :meth:`Package.from_source` can mark the package
           ``spec_missing``.
    """
    if isinstance(spec, DataPackage):
        return spec
    if isinstance(spec, (str, Path)):
        return load_package(spec)
    # Auto-discover.
    candidate = Path(source_str) / "datapackage.json"
    if candidate.exists():
        return load_package(candidate)
    logger.info(
        "no spec given and no datapackage.json in %s; tables load unchecked (validate() reports structural.spec_missing)",
        source_str,
    )
    return None


def _looks_remote(path: str) -> bool:
    """True if ``path`` starts with a URL scheme that :class:`RemoteAdapter` claims.

    Used by :meth:`Package.from_source` to route remote ResourceRefs
    through the remote adapter (so the credentials cascade fires)
    rather than handing the URL straight to the inner format adapter.

    Source-of-truth for the scheme list is
    :data:`corral.io.remote.REMOTE_SCHEMES`; we import lazily to
    avoid a top-level cycle.
    """
    # Local import keeps the dataset → io edge load-order-friendly
    # (io self-registers RemoteAdapter at module import, which is
    # what populates REMOTE_SCHEMES — we want the import to fire
    # the first time _looks_remote runs, not at package.py load).
    from corral.io import _scheme_of
    from corral.io.remote import REMOTE_SCHEMES

    # Same urlparse-based scheme as ``dispatch`` (via ``_scheme_of``), so the two can never
    # disagree about whether a string is remote.
    return _scheme_of(path) in REMOTE_SCHEMES


def _scan_source(
    source_str: str,
    *,
    format: str | None = None,
    credentials: dict[str, str] | None = None,
) -> ResourceListing:
    """Resolve ``source_str`` to a :class:`ResourceListing`.

    Two branches, in order of preference:
        1. The source is a local directory of known-format files
           (``csv/``, ``parquet/``). Walk children and ask each
           matching adapter to scan one. ``format=`` is not consulted
           here — per-child extension dispatch already routes each
           file to the right adapter.
        2. Otherwise let :func:`corral.io.dispatch` resolve the
           source (honouring ``format=`` when given) and call the
           adapter's ``scan`` method. ``credentials`` reach the remote
           adapter only (a URL prefix is listed with them); other
           adapters' ``scan`` takes no such kwarg.
    """
    source_path = Path(source_str)
    if source_path.exists() and source_path.is_dir() and not _is_partitioned_parquet_dir(source_path):
        return _scan_directory_of_known_formats(source_path)
    try:
        adapter = dispatch(source_str, format=format)
    except FormatNotDetected:
        return []
    if credentials is not None and adapter.name == "remote":
        # Only RemoteAdapter.scan takes credentials; the registry hands back the protocol type.
        return cast("RemoteAdapter", adapter).scan(source_str, credentials=credentials)
    return adapter.scan(source_str)


def _scan_directory_of_known_formats(source_path: Path) -> ResourceListing:
    """Walk ``source_path`` asking each known format adapter to scan child files.

    Mirrors :func:`corral.validation.structural._scan_directory_of_known_formats`
    so the two paths can't drift apart on "what counts as a directory of
    tables". We don't import the helper directly to avoid the
    validation → dataset cycle.
    """
    listings: list[ResourceRef] = []
    seen: set[str] = set()

    ext_owners: list[tuple[str, str]] = []
    for adapter_name in list_adapters():
        adapter = get_adapter(adapter_name)
        for ext in adapter.extensions:
            ext_owners.append((ext.lower().lstrip("."), adapter_name))

    for child in sorted(source_path.iterdir()):
        if not child.is_file():
            continue
        name_lower = child.name.lower()
        for ext, adapter_name in ext_owners:
            if name_lower.endswith("." + ext):
                adapter = get_adapter(adapter_name)
                try:
                    refs = adapter.scan(child)
                except (FormatNotDetected, OSError) as exc:
                    # Adapter rejected the file (format mismatch under a
                    # known extension, e.g. a `.csv` that's actually
                    # binary; or an OS-level read failure). Skip the
                    # child rather than abort the whole directory scan.
                    logger.warning(
                        "adapter %r failed to scan %s: %s",
                        adapter_name,
                        child,
                        exc,
                    )
                    refs = []
                for ref in refs:
                    if ref.name in seen:
                        continue
                    seen.add(ref.name)
                    listings.append(ref)
                break
    return listings


def _is_partitioned_parquet_dir(path: Path) -> bool:
    """Cheap probe — does ``path`` look like a Hive-partitioned parquet dataset?

    Same heuristic as
    :func:`corral.validation.structural._is_partitioned_parquet_dir`.
    """
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and "=" in child.name:
                return True
    except OSError:
        return False
    return False


def _schema_for(spec: DataPackage, table_name: str) -> Schema | None:
    """Return the resolved :class:`Schema` for ``table_name``, or ``None``."""
    for resource in spec.resources:
        if resource.name != table_name:
            continue
        sch = resource.table_schema
        if isinstance(sch, Schema):
            return sch
        return None
    return None


def _resolve_write_format(dest: Path, format: str | None) -> str:
    """Return the canonical write-format name for :meth:`Package.write`.

    An explicit ``format`` wins (``"zip"`` is an alias for the
    ``"zipcsv"`` adapter, matching the user-facing ``--format zip``);
    otherwise the format is inferred from ``dest``.
    """
    if format is None:
        return _infer_write_format(dest)
    return "zipcsv" if format == "zip" else format


def _write_package_tables(pkg: Package, dest: Path, target_format: str) -> None:
    """Write every table of ``pkg`` to ``dest`` in ``target_format``.

    The layout depends on the format:

    * parquet / csv: a directory with one ``<table>.<ext>`` file per table
      plus a ``datapackage.json`` describing them (see
      :func:`_package_descriptor`), so a directory source that carried its
      own descriptor keeps one when written back over itself;
    * duckdb: a single multi-table file, one table per :attr:`Package.tables` entry;
    * anything else: one ``dest / <table>`` location per table through the
      resolved adapter, which raises if the layout doesn't fit. This is the
      seam new formats slot into without editing the central if/elif ladder.
    """
    if target_format in {"parquet", "csv"}:
        dest.mkdir(parents=True, exist_ok=True)
        adapter = get_adapter(target_format)
        for name, table in pkg.tables.items():
            adapter.write(table.expr, dest / f"{name}.{target_format}", engine=pkg.engine)
        descriptor = _package_descriptor(pkg.spec, pkg.tables, format=target_format)
        (dest / "datapackage.json").write_text(json.dumps(descriptor, indent=2))
    elif target_format == "duckdb":
        adapter = get_adapter("duckdb")
        for name, table in pkg.tables.items():
            adapter.write(table.expr, dest, engine=pkg.engine, table=name)
    else:
        adapter = get_adapter(target_format)
        for name, table in pkg.tables.items():
            adapter.write(table.expr, dest / name, engine=pkg.engine)


def _swap_into_place(staged: Path, dest: Path, *, aside: Path) -> None:
    """Rename ``staged`` to ``dest``, first moving anything at ``dest`` to ``aside``.

    A directory cannot be renamed over a non-empty directory, so an
    existing ``dest`` (file or directory) is moved out of the way first.
    If the final rename then fails, the displaced ``dest`` is put back
    before the error propagates.
    """
    if not os.path.lexists(dest):
        os.replace(staged, dest)
        return
    os.replace(dest, aside)
    try:
        os.replace(staged, dest)
    except OSError:
        os.replace(aside, dest)
        raise


def _write_zipcsv_package(pkg: Package, dest: Path) -> None:
    """Write ``pkg`` as a CSV-in-zip data package at ``dest``, atomically.

    Layout: one flat ``<table>.csv`` member per table plus a
    ``datapackage.json`` (see :func:`_package_descriptor`) — the same
    shape :class:`~corral.io.zipcsv_adapter.ZipCsvAdapter` scans back.

    Each table is encoded by the csv adapter into a staging directory
    created *beside* ``dest`` (same filesystem), added to a staging
    archive, and deleted, so at most one uncompressed CSV is on disk at a
    time. The finished archive is ``os.replace``-d over ``dest``: readers
    never see a partial zip, and a failed write leaves a previous *file*
    ``dest`` intact. Replacing an existing *directory* ``dest`` is not
    atomic — it is ``rmtree``-d just before the rename. The staging
    directory is always removed. Resources without a resolvable schema
    are written without a ``schema`` key.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    csv_adapter = get_adapter("csv")
    with tempfile.TemporaryDirectory(prefix=f".{dest.name}.", dir=dest.parent) as staging_dir:
        staging = Path(staging_dir)
        staged_zip = staging / ".package.zip"
        with zipfile.ZipFile(staged_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, table in pkg.tables.items():
                member = staging / f"{name}.csv"
                csv_adapter.write(table.expr, member, engine=pkg.engine)
                zf.write(member, arcname=member.name)
                member.unlink()
            descriptor = _package_descriptor(pkg.spec, pkg.tables, format="csv")
            zf.writestr("datapackage.json", json.dumps(descriptor, indent=2))
        if dest.is_dir():
            shutil.rmtree(dest)
        os.replace(staged_zip, dest)


#: Per-format resource properties written into a flat-layout descriptor.
_FLAT_RESOURCE_PROPS: dict[str, dict[str, str]] = {
    "csv": {"format": "csv", "mediatype": "text/csv", "encoding": "utf-8"},
    "parquet": {"format": "parquet", "mediatype": "application/vnd.apache.parquet"},
}


def _package_descriptor(spec: DataPackage, table_names: Iterable[str], *, format: str) -> dict[str, Any]:
    """Build a ``datapackage.json`` for flat ``<table>.<format>`` resources.

    Package-level metadata and each resource's own properties are carried
    over from ``spec``; ``path`` / ``format`` / ``mediatype`` / ``encoding``
    are rewritten for the flat layout (a csv or parquet directory, or the
    members of a csv zip). Resolved schemas are inlined so the result is
    self-describing — a relative schema path from the source package would
    dangle next to the written files, so it is dropped instead.
    """
    props = _FLAT_RESOURCE_PROPS[format]
    descriptor = spec.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"resources"})
    originals = {r.name: r for r in spec.resources}
    resources: list[dict[str, Any]] = []
    for name in table_names:
        original = originals.get(name)
        entry = original.model_dump(mode="json", by_alias=True, exclude_none=True) if original else {"name": name}
        entry.pop("encoding", None)
        entry.update(path=f"{name}.{format}", **props)
        schema = _schema_for(spec, name)
        if schema is not None:
            entry["schema"] = schema.model_dump(mode="json", by_alias=True, exclude_none=True)
        else:
            entry.pop("schema", None)
        resources.append(entry)
    descriptor["resources"] = resources
    return descriptor


def _infer_write_format(dest: Path) -> str:
    """Infer a write format from ``dest``.

    Order:
        1. Extension match (``.parquet``, ``.csv``, ``.duckdb``,
           ``.csv.zip`` / ``.zip``).
        2. No extension at all → default to ``"parquet"`` (the
           recommended persistent layout per architecture §6.1) so the
           common "give me a directory" call site keeps working.
        3. Unknown extension → raise :class:`FormatNotDetected` rather
           than silently coercing to parquet; the user almost certainly
           meant a specific format and a silent default would hide the
           bug.
    """
    name = dest.name.lower()
    if name.endswith(".duckdb"):
        return "duckdb"
    if name.endswith(".zip"):
        return "zipcsv"
    if name.endswith(".parquet"):
        return "parquet"
    if name.endswith(".csv"):
        return "csv"
    # No extension at all → directory-ish; default to parquet per §6.1.
    if "." not in name:
        return "parquet"
    raise FormatNotDetected(
        f"Could not infer write format for {dest!s}; "
        f"pass format= explicitly. Known write formats: "
        f"{sorted(list_adapters())}."
    )


def _html_escape(value: str) -> str:
    """Tiny stdlib HTML escape — kept inline for the legacy module surface."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Notebook card rendering — shared by :meth:`Package._repr_html_` and the
# subclass override in :class:`netstead.Network` so the two can't drift.
# ---------------------------------------------------------------------------


#: Cap on how many tables to enumerate in the per-card rows table before
#: collapsing the rest into a "…+N more" note. Keeps the card height
#: bounded for very wide packages (every GMNS table at once is ~24 rows).
_PACKAGE_TABLE_PREVIEW = 8


def _render_package_card(
    pkg: Package,
    card: Any,
    kv_line: Any,
    small_table: Any,
    truncation_note: Any,
    *,
    title: str = "Package",
    extra_kv: list[tuple[str, object]] | None = None,
) -> str:
    """Render the shared Package/Network card body.

    Factored out so :meth:`netstead.Network._repr_html_` can reuse the
    same table preview without copy-pasting the row-count probing
    (which catches every engine-specific failure mode for cheap counts).

    ``title`` is the card header (``"Package"`` by default, ``"Network"``
    for the subclass) and ``extra_kv`` is forwarded into the metadata
    line so the subclass can prepend ``spec_version``, ``links``,
    ``nodes`` without re-implementing the table preview.
    """
    items = list(pkg.tables.items())
    preview = items[:_PACKAGE_TABLE_PREVIEW]
    rows: list[list[object]] = []
    for name, table in preview:
        rows.append([name, _safe_row_count(table), _safe_col_count(table)])
    table_html = small_table(["name", "rows", "cols"], rows)
    note_html = truncation_note(len(items) - len(preview), "tables")
    kv_items: list[tuple[str, object]] = []
    if extra_kv:
        kv_items.extend(extra_kv)
    kv_items.append(("engine", _engine_label(pkg.engine)))
    kv_items.append(("tables", len(items)))
    body = kv_line(kv_items) + table_html + note_html
    subtitle = pkg.source or "in-memory"
    header_title = f"{title}: {pkg.spec.name}" if getattr(pkg.spec, "name", None) else title
    return card(header_title, body, subtitle=subtitle)


def _safe_row_count(table: Table) -> object:
    """Return ``table.count()`` or ``"?"`` if the engine call raises.

    The cheap count path on :meth:`Table.count` pushes through the
    engine — for an ibis expression backed by an unreachable backend it
    can throw. We don't want a notebook preview to crash, so we swallow
    the most common engine errors and fall through to ``"?"``. Real
    surprises (programmer errors like ``KeyError`` / ``TypeError``)
    still propagate so they're visible during development.
    """
    try:
        return int(table.count())
    except (RuntimeError, OSError, ValueError):
        return "?"


def _safe_col_count(table: Table) -> object:
    """Return ``len(table.columns())`` or ``"?"`` on engine error.

    Same conservative posture as :func:`_safe_row_count` — a notebook
    preview should never crash the cell.
    """
    try:
        return len(table.columns())
    except (RuntimeError, OSError, ValueError):
        return "?"


def _engine_label(engine: Engine | None) -> str:
    """Short engine identifier for the metadata line.

    Uses :attr:`Engine.name` when present (every stock engine sets it),
    falling back to the class name so a third-party engine still shows
    something meaningful.
    """
    if engine is None:
        return "—"
    return str(getattr(engine, "name", type(engine).__name__))
