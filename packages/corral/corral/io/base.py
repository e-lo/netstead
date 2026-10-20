"""FormatAdapter protocol, source/resource types, and exception hierarchy.

This module defines the contract every storage-format adapter must satisfy
(csv, parquet, duckdb, zipcsv, remote-fsspec, ...). Concrete adapters live
in sibling modules and are expected to be registered via
``corral.io.register_adapter`` at import time of their host module.

Design notes for adapter implementers:
    - ``probe`` must be cheap (header sniff / magic-byte check / extension
      heuristic). It is called by the dispatcher only as a last resort,
      after explicit ``format=``, URL scheme, and filename extension
      dispatch have failed.
    - ``read`` must return a *lazy* table expression created via the engine
      (e.g., ``engine.read_csv(path)``); it must not materialize.
    - ``scan`` is the discovery surface for multi-table containers
      (a directory of csvs, a duckdb file with multiple tables, a zip with
      multiple csvs). Single-table formats simply return a one-element list.
    - The engine reference is passed through so adapters can defer all
      actual I/O to the engine's native readers --this keeps adapters thin
      and engine-agnostic at the call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from corral.spec.model import Schema
from corral.types import SourceRef

if TYPE_CHECKING:
    # engines imports from io/. A type-only import breaks the cycle.
    from corral.engines.base import Engine, TableExpr  # pragma: no cover


# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

# ``SourceRef`` is re-exported from :mod:`corral.types` (the canonical
# definition lives there so ``engines`` and ``io`` cannot drift apart).
# Re-exporting here keeps ``from corral.io.base import SourceRef``
# working for existing call sites and tests.


# ---------------------------------------------------------------------------
# Resource listing model
# ---------------------------------------------------------------------------


class ResourceRef(BaseModel):
    """A single discoverable resource (table) at a source.

    Attributes:
        name: Logical resource name (e.g., ``"link"``, ``"node"``). For a
            single-file source this is typically the file stem.
        path: Concrete locator for the resource --a filesystem path, a URL,
            or a sub-locator within a container (e.g., ``"my.duckdb::link"``
            for a duckdb table). Always a string for serializability.
        format: Short identifier of the format adapter that produced this
            ref (e.g., ``"csv"``, ``"parquet"``, ``"duckdb"``). For a member
            of a container this is the *member's* format (a zip member is
            ``"csv"``).
        container_adapter: Set only by multi-table container adapters
            (duckdb, zipcsv): the name of the adapter that owns the
            container and must read the member back. ``None`` for plain
            single-file refs, so a filename that merely contains ``"::"``
            is never mistaken for a sub-locator.
        member: The selector to pass as ``table=`` to
            ``container_adapter``. When set, ``path`` is exactly
            ``f"{container}::{member}"``.
        table_schema: The resource's schema, when the adapter already
            knows it from the source itself (a zip's own
            ``datapackage.json`` with inline schemas), so a consumer need
            not reopen the source to find it. ``None`` means "not known
            from the scan", not "no schema". Spelled ``schema`` in JSON
            and accepted under either name, like
            :attr:`corral.spec.model.Resource.table_schema`.
    """

    name: str = Field(..., description="Logical resource name (table name).")
    path: str = Field(..., description="Concrete locator for the resource.")
    format: str = Field(..., description="Short adapter identifier.")
    container_adapter: str | None = Field(default=None, description="Adapter owning the container, if any.")
    member: str | None = Field(default=None, description="Member selector within the container, if any.")
    table_schema: Schema | None = Field(
        default=None, alias="schema", description="Schema known from the source itself, if any."
    )

    @property
    def container(self) -> str | None:
        """Path of the owning container, or ``None`` for a plain single-file ref."""
        if self.member is None:
            return None
        return self.path.removesuffix(f"::{self.member}")

    model_config = ConfigDict(frozen=True, populate_by_name=True)


ResourceListing = list[ResourceRef]
"""An ordered list of resources discovered at a source.

Single-table formats (one csv, one parquet) yield a one-element listing;
multi-table containers (directory of csvs, duckdb file, zipped package) may
yield many.
"""


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FormatAdapter(Protocol):
    """A read/write adapter for one storage format.

    Implementations declare their identity (``name``), the filename
    extensions they own (``extensions``, no leading dot), and any URL
    schemes they handle (``schemes``, no trailing colon-slash). The
    dispatcher uses these to route a :data:`SourceRef` to the right
    adapter; ``probe`` is the fallback when neither extension nor scheme
    yields a unique match.

    Attributes:
        name: Short identifier --e.g. ``"csv"``, ``"parquet"``,
            ``"duckdb"``, ``"zipcsv"``. Must be unique across the registry.
        extensions: Tuple of filename extensions (no dot) this adapter
            owns. Compound extensions are written longest-first joined by
            dots --e.g. ``("csv.zip",)`` for a csv-in-zip adapter.
        schemes: Tuple of URL schemes (no ``://``) this adapter handles.
            Usually empty; populated for adapters that own a scheme like
            ``duckdb://``.
    """

    name: str
    extensions: tuple[str, ...]
    schemes: tuple[str, ...]

    def probe(self, source: SourceRef) -> bool:
        """Return True if this adapter can read ``source``.

        Implementations must be cheap --a header sniff, a magic-byte read,
        or an extension/heuristic check. Called by the dispatcher only
        when explicit format, scheme, and extension lookup all miss.

        Args:
            source: The candidate source reference.

        Returns:
            True if the adapter is willing to attempt a read.
        """
        ...

    def read(
        self,
        source: SourceRef,
        engine: Engine,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Return an unmaterialized table expression for ``source``.

        Args:
            source: The source reference to read.
            engine: The engine to defer actual I/O to (e.g., ibis/duckdb).
            schema: Optional resolved Frictionless schema; when provided,
                adapters should pass column-type hints to the engine.
            **kwargs: Adapter-specific options (compression, delimiter,
                partition pruning predicate, etc.).

        Returns:
            A lazy table expression. Materialization happens only on the
            consumer side (``.to_pandas()``, ``.collect()``, ...).
        """
        ...

    def write(
        self,
        expr: TableExpr,
        dest: SourceRef,
        engine: Engine,
        **kwargs: Any,
    ) -> None:
        """Persist ``expr`` to ``dest`` via ``engine``.

        Args:
            expr: A table expression to materialize and write.
            dest: Target reference (path or URL).
            engine: The engine performing the write.
            **kwargs: Adapter-specific options (partitioning, compression,
                overwrite mode, ...).
        """
        ...

    def scan(self, source: SourceRef, engine: Engine | None = None) -> ResourceListing:
        """Enumerate the tables/resources discoverable at ``source``.

        Multi-table containers (directory of csvs, duckdb file with
        multiple tables, zip with multiple csvs) return many entries.
        Single-table formats return a one-element listing.

        Most adapters can answer this from on-disk metadata alone and
        don't need an engine — single-file csv/parquet read the file
        stem; duckdb opens the file's system catalogue directly; zipcsv
        reads the zip's central directory. The ``engine`` parameter is
        therefore optional and defaults to ``None``; an adapter that
        truly needs an engine for cheap metadata reads documents that
        requirement in its own signature.

        Args:
            source: The source to enumerate.
            engine: Optional engine for cheap metadata reads. Most
                adapters ignore it.

        Returns:
            An ordered :data:`ResourceListing`.
        """
        ...


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class FormatError(Exception):
    """Base class for all I/O format errors raised by ``corral.io``."""


class FormatNotDetected(FormatError):
    """No registered adapter could be matched to a source.

    Raised by :func:`corral.io.dispatch` after all resolution stages
    (explicit format, URL scheme, filename extension, ``probe`` chain)
    have failed.
    """


class AdapterNotAvailableError(FormatError):
    """A named adapter was requested but is not registered.

    Raised when a caller passes ``format="excel"`` (or similar) and no
    adapter with that ``name`` has been registered. Typically this means
    an optional dependency is not installed or the adapter module was not
    imported.
    """


class InvalidAdapterError(FormatError):
    """A registration attempt received an object that is not a valid adapter.

    Raised by ``register_adapter`` when the supplied object does not satisfy
    the :class:`FormatAdapter` protocol or lacks a non-empty ``name``.
    Mirrors :class:`corral.engines.EngineNotAvailableError`'s role on the
    engines side so callers (and AI agents) get a categorical exception type
    for the "you handed me something I can't register" failure mode rather
    than a bare ``TypeError`` / ``ValueError``.
    """


class DuplicateAdapterError(FormatError):
    """A registration would displace an adapter that is already registered.

    Raised by ``register_adapter`` when a different adapter class already
    holds the name, or another adapter owns one of the extensions or URL
    schemes. Pass ``replace=True`` to override deliberately.
    """


__all__ = [
    "AdapterNotAvailableError",
    "DuplicateAdapterError",
    "FormatAdapter",
    "FormatError",
    "FormatNotDetected",
    "InvalidAdapterError",
    "ResourceListing",
    "ResourceRef",
    "SourceRef",
]
