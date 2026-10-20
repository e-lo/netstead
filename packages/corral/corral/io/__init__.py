"""Format adapter registry (csv, parquet, duckdb, zipcsv, remote URL + credentials).

This package owns the :class:`FormatAdapter` protocol and the registry +
dispatcher that route a :data:`SourceRef` (path or URL) to the correct
concrete adapter. Concrete adapters (csv/parquet/duckdb/zipcsv/remote)
are implemented in sibling modules and self-register at import time via
:func:`register_adapter`.

Resolution order in :func:`dispatch`:

    1. Explicit ``format=`` keyword --exact name lookup.
    2. URL scheme match (``duckdb://...`` → adapter declaring ``duckdb``
       in ``schemes``).
    3. Filename extension match. Compound extensions (e.g. ``.csv.zip``)
       are tried before the simple tail (``.zip``) so a zip-of-csv adapter
       wins over a generic zip handler.
    4. ``probe`` chain --every registered adapter is asked, in
       registration order, whether it can read the source. The first
       ``True`` wins.
    5. :class:`FormatNotDetected` raised, listing registered adapters.

Importing this package triggers self-registration of the five stock
adapters (csv, parquet, duckdb, zipcsv, remote). Each adapter module
calls ``register_adapter`` at import time; importing ``corral.io``
imports all five. Tests that need an empty registry can call
``_clear_registry()``; ``_reset_registry()`` puts the stock adapters back
(``packages/corral/tests/io/conftest.py`` does so around every io test).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from ._paths import normalize_to_str
from .base import (
    AdapterNotAvailableError,
    DescriptorFactory,
    DuplicateAdapterError,
    FormatAdapter,
    FormatError,
    FormatNotDetected,
    InvalidAdapterError,
    PackageWriter,
    ResourceListing,
    ResourceRef,
    SourceRef,
)
from .errors import WriteUnsupportedForSchemeError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from corral.engines.base import Engine

_logger: Final = logging.getLogger(__name__)

# ``scheme://user:secret@host`` userinfo, so a probe error that echoes its
# source never puts a password in the log.
_URL_USERINFO: Final = re.compile(r"(?<=://)[^/@\s]+@")

# ---------------------------------------------------------------------------
# Registry state
# ---------------------------------------------------------------------------
# Three indexed dicts kept in sync by ``register_adapter``:
#   _REGISTRY:   name           → adapter
#   _BY_EXT:     extension key  → adapter name  (compound extensions allowed)
#   _BY_SCHEME:  url scheme     → adapter name
# Insertion order in _REGISTRY is preserved (Python 3.7+) and is the order
# in which probe() is consulted in the fallback chain.

_REGISTRY: dict[str, FormatAdapter] = {}
_BY_EXT: dict[str, str] = {}
_BY_SCHEME: dict[str, str] = {}


def register_adapter(adapter: FormatAdapter, *, replace: bool = False) -> None:
    """Register ``adapter`` into the global registry.

    A collision is an error unless ``replace=True``: once adapters arrive
    from third-party packages, two of them claiming one name, extension or
    URL scheme would otherwise let import order pick the winner silently.
    Re-registering an instance of the *same* class under its own name is
    not a collision (a module reload, a test re-registering a stock
    adapter); it replaces the previous instance.

    Args:
        adapter: A :class:`FormatAdapter` instance with non-empty ``name``.
        replace: Override whatever already holds ``adapter.name`` or any of
            its extensions/schemes. The replaced adapter's bindings are
            dropped first.

    Raises:
        DuplicateAdapterError: If, without ``replace=True``, a different
            adapter class is registered under ``adapter.name``, or one of
            ``adapter``'s extensions/schemes belongs to another adapter.
            Nothing is registered.
        InvalidAdapterError: If ``adapter`` does not satisfy the
            :class:`FormatAdapter` protocol, or if ``adapter.name`` is
            empty. Catch via the ``FormatError`` base class to handle
            any format-layer registration failure.

    Examples:
        Register a minimal fake adapter and confirm it appears in the
        registry. The fake uses a unique name to avoid colliding with
        the stock adapters registered in later tasks:

        >>> from corral.io import register_adapter, get_adapter
        >>> from corral.io.base import ResourceRef
        >>> class _DoctestAdapter:
        ...     name = "doctest-register"
        ...     extensions = ("doctestfmt",)
        ...     schemes = ()
        ...     def probe(self, source): return False
        ...     def read(self, source, engine, schema=None, **kw): return None
        ...     def write(self, expr, dest, engine, **kw): return None
        ...     def scan(self, source, engine):
        ...         return [ResourceRef(
        ...             name=self.name, path=str(source), format=self.name
        ...         )]
        >>> fake = _DoctestAdapter()
        >>> from corral import io as _io
        >>> try:
        ...     register_adapter(fake)
        ...     get_adapter("doctest-register") is fake
        ... finally:
        ...     _ = _io._REGISTRY.pop("doctest-register", None)
        ...     _ = _io._BY_EXT.pop("doctestfmt", None)
        True
    """
    if not isinstance(adapter, FormatAdapter):
        raise InvalidAdapterError(
            f"{adapter!r} does not satisfy the FormatAdapter protocol "
            "(needs a 'name' attribute plus probe/read/write/scan methods)"
        )
    if not adapter.name:
        raise InvalidAdapterError("FormatAdapter.name must be a non-empty string")

    name = adapter.name

    if not replace:
        _check_no_collision(adapter)

    # If overwriting, scrub the previous adapter's ext/scheme bindings: the new
    # adapter may claim fewer extensions, and a stale binding would route those
    # sources to it anyway.
    if name in _REGISTRY:
        for bindings in (_BY_EXT, _BY_SCHEME):
            for key in [key for key, owner in bindings.items() if owner == name]:
                del bindings[key]

    _REGISTRY[name] = adapter
    for ext in adapter.extensions:
        _BY_EXT[ext.lower().lstrip(".")] = name
    for scheme in adapter.schemes:
        _BY_SCHEME[scheme.lower()] = name


def _implementation(adapter: FormatAdapter) -> str:
    """The adapter's class as ``module.QualName``: stable across a module reload, unlike the class object."""
    cls = type(adapter)
    return f"{cls.__module__}.{cls.__qualname__}"


def _check_no_collision(adapter: FormatAdapter) -> None:
    """Raise :class:`DuplicateAdapterError` if ``adapter`` would displace another adapter."""
    existing = _REGISTRY.get(adapter.name)
    if existing is not None and _implementation(existing) != _implementation(adapter):
        raise DuplicateAdapterError(
            f"An adapter named {adapter.name!r} is already registered ({_implementation(existing)}); "
            f"pass replace=True to register {_implementation(adapter)} in its place."
        )
    claims = [("extension", ext.lower().lstrip("."), _BY_EXT) for ext in adapter.extensions]
    claims += [("URL scheme", scheme.lower(), _BY_SCHEME) for scheme in adapter.schemes]
    for kind, key, bindings in claims:
        owner = bindings.get(key)
        if owner is not None and owner != adapter.name:
            raise DuplicateAdapterError(
                f"The {kind} {key!r} already belongs to adapter {owner!r}; "
                f"pass replace=True to give it to {adapter.name!r}."
            )


def get_adapter(name: str) -> FormatAdapter:
    """Look up an adapter by name.

    Args:
        name: The adapter's short identifier.

    Returns:
        The registered adapter.

    Raises:
        AdapterNotAvailableError: If no adapter with ``name`` is registered.

    Examples:
        Asking for an unknown adapter raises a clear error that lists
        what *is* registered:

        >>> from corral.io import get_adapter, AdapterNotAvailableError
        >>> try:
        ...     get_adapter("not-a-real-adapter")
        ... except AdapterNotAvailableError as exc:
        ...     "not-a-real-adapter" in str(exc)
        True

        Round-trip via :func:`register_adapter`:

        >>> from corral.io import register_adapter
        >>> from corral.io.base import ResourceRef
        >>> from corral import io as _io
        >>> class _DoctestAdapter:
        ...     name = "doctest-get"
        ...     extensions = ()
        ...     schemes = ()
        ...     def probe(self, source): return False
        ...     def read(self, source, engine, schema=None, **kw): return None
        ...     def write(self, expr, dest, engine, **kw): return None
        ...     def scan(self, source, engine):
        ...         return [ResourceRef(
        ...             name=self.name, path=str(source), format=self.name
        ...         )]
        >>> fake = _DoctestAdapter()
        >>> try:
        ...     register_adapter(fake)
        ...     get_adapter("doctest-get") is fake
        ... finally:
        ...     _ = _io._REGISTRY.pop("doctest-get", None)
        True
    """
    try:
        return _REGISTRY[name]
    except KeyError as err:
        raise AdapterNotAvailableError(
            f"No adapter registered under name {name!r}. Registered adapters: {list(_REGISTRY)!r}"
        ) from err


def list_adapters() -> list[str]:
    """Return the names of all registered adapters in registration order.

    Examples:
        With no adapters auto-registered at import time, ``list_adapters``
        reflects exactly what callers have registered themselves:

        >>> from corral.io import register_adapter, list_adapters
        >>> from corral.io.base import ResourceRef
        >>> from corral import io as _io
        >>> class _DoctestAdapter:
        ...     name = "doctest-list"
        ...     extensions = ()
        ...     schemes = ()
        ...     def probe(self, source): return False
        ...     def read(self, source, engine, schema=None, **kw): return None
        ...     def write(self, expr, dest, engine, **kw): return None
        ...     def scan(self, source, engine):
        ...         return [ResourceRef(
        ...             name=self.name, path=str(source), format=self.name
        ...         )]
        >>> try:
        ...     register_adapter(_DoctestAdapter())
        ...     "doctest-list" in list_adapters()
        ... finally:
        ...     _ = _io._REGISTRY.pop("doctest-list", None)
        True
    """
    return list(_REGISTRY)


def _clear_registry() -> None:
    """Drop all registered adapters (test helper).

    Not part of the public API --intended for test fixtures that need a
    clean slate. Not exported from ``__all__``.
    """
    _REGISTRY.clear()
    _BY_EXT.clear()
    _BY_SCHEME.clear()


def _reset_registry() -> None:
    """Restore the registry to the stock adapters registered at import (test helper).

    The one canonical teardown for tests that clear or re-populate the
    registry: the same adapter instances and the same extension/scheme
    bindings ``import corral.io`` produced. Not exported from ``__all__``.
    """
    _clear_registry()
    _REGISTRY.update(_STOCK_REGISTRY)
    _BY_EXT.update(_STOCK_BY_EXT)
    _BY_SCHEME.update(_STOCK_BY_SCHEME)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _scheme_of(source_str: str) -> str | None:
    """Extract a URL scheme from ``source_str``, if any.

    Returns the scheme lowercase (without ``://``), or ``None`` if the
    source has no scheme, only a single-letter scheme (which is most
    likely a Windows drive letter like ``C:\\path``), or is not literally
    ``scheme://...`` (``s3:../x`` is a relative local path to every
    inner adapter, so it must never be routed by scheme).
    """
    parsed = urlparse(source_str)
    scheme = parsed.scheme.lower()
    if not scheme or len(scheme) == 1:
        return None
    if not source_str.lower().startswith(f"{scheme}://"):
        return None
    return scheme


def _extension_keys(source_str: str) -> list[str]:
    """Return candidate extension keys for ``source_str``, longest first.

    For ``"foo.csv.zip"`` returns ``["csv.zip", "zip"]`` so a compound
    adapter (zipcsv) gets first crack before a generic single-extension
    adapter.
    """
    # Strip any URL fragments/queries before looking at the path tail.
    parsed = urlparse(source_str)
    path = parsed.path if parsed.scheme and len(parsed.scheme) > 1 else source_str
    name = Path(path).name.lower()

    parts = name.split(".")
    if len(parts) <= 1:
        return []
    # Build progressively shorter compound suffixes.
    # e.g. parts = ["foo", "csv", "zip"] → suffixes = ["csv.zip", "zip"]
    suffixes: list[str] = []
    for i in range(1, len(parts)):
        suffixes.append(".".join(parts[i:]))
    return suffixes


def dispatch(source: SourceRef, *, format: str | None = None) -> FormatAdapter:
    """Resolve ``source`` to a registered :class:`FormatAdapter`.

    Args:
        source: The source reference.
        format: Optional explicit adapter name. When given, takes
            precedence over all sniffing.

    Returns:
        The resolved adapter.

    Raises:
        AdapterNotAvailableError: If ``format`` is given but unregistered.
        FormatNotDetected: If no resolution stage matched.

    Examples:
        Extension-based routing — register a fake that owns a unique
        extension, then dispatch a matching path:

        >>> from corral.io import register_adapter, dispatch
        >>> from corral.io.base import ResourceRef
        >>> from corral import io as _io
        >>> class _DoctestAdapter:
        ...     name = "doctest-dispatch"
        ...     extensions = ("doctestfmt",)
        ...     schemes = ()
        ...     def probe(self, source): return False
        ...     def read(self, source, engine, schema=None, **kw): return None
        ...     def write(self, expr, dest, engine, **kw): return None
        ...     def scan(self, source, engine):
        ...         return [ResourceRef(
        ...             name=self.name, path=str(source), format=self.name
        ...         )]
        >>> fake = _DoctestAdapter()
        >>> try:
        ...     register_adapter(fake)
        ...     dispatch("data/example.doctestfmt") is fake
        ... finally:
        ...     _ = _io._REGISTRY.pop("doctest-dispatch", None)
        ...     _ = _io._BY_EXT.pop("doctestfmt", None)
        True

        An explicit ``format=`` short-circuits all sniffing. The
        extension on the source is ignored when ``format`` is given:

        >>> other = _DoctestAdapter()
        >>> try:
        ...     register_adapter(other)
        ...     dispatch("anything-at-all", format="doctest-dispatch").name
        ... finally:
        ...     _ = _io._REGISTRY.pop("doctest-dispatch", None)
        ...     _ = _io._BY_EXT.pop("doctestfmt", None)
        'doctest-dispatch'
    """
    # 1. Explicit format wins.
    if format is not None:
        return get_adapter(format)

    # The same coercion every adapter uses, so the dispatcher and its delegates
    # agree on what a path-shaped source is. A ``dict`` source raises
    # UnsupportedSourceError (a TypeError): pass ``format=`` to skip sniffing.
    source_str = normalize_to_str(source, adapter="dispatch")

    # 2. URL scheme match.
    scheme = _scheme_of(source_str)
    if scheme is not None and scheme in _BY_SCHEME:
        return _REGISTRY[_BY_SCHEME[scheme]]

    # 3. Extension match --longest compound first.
    for ext_key in _extension_keys(source_str):
        if ext_key in _BY_EXT:
            return _REGISTRY[_BY_EXT[ext_key]]

    # 4. Probe chain --registration order.
    for adapter in _REGISTRY.values():
        try:
            if adapter.probe(source):
                return adapter
        except Exception as exc:
            # An adapter's probe must be cheap and total; if it raises,
            # treat it as a non-match rather than letting it crash the
            # dispatch pipeline, but leave a trace for whoever debugs it.
            _logger.debug(
                "probe of adapter %r raised %s: %s; skipping it",
                adapter.name,
                type(exc).__name__,
                _URL_USERINFO.sub("***@", str(exc)),
            )
            continue

    # 5. Give up with a useful error message.
    raise FormatNotDetected(
        f"Could not determine format for source {source_str!r}. Registered adapters: {list(_REGISTRY)!r}"
    )


# ---------------------------------------------------------------------------
# Whole-package writes
# ---------------------------------------------------------------------------


def write_package(
    adapter: FormatAdapter,
    tables: Mapping[str, Any],
    dest: Path,
    engine: Engine,
    *,
    describe: DescriptorFactory,
) -> None:
    """Write a multi-table package to ``dest`` in ``adapter``'s layout.

    The layout is the adapter's decision: an adapter implementing
    :class:`PackageWriter` lays the package out itself (csv / parquet: a
    directory with a ``datapackage.json``; duckdb: one multi-table file;
    zipcsv: one zip). Any other adapter gets one ``dest / <table>``
    location per table through its ``write``, and raises there if that
    layout doesn't fit. A new format slots in by implementing
    ``write_package``; nothing central needs editing.

    Args:
        adapter: The adapter for the target format.
        tables: ``{table name: expression}``, in package order.
        dest: A location that does not exist yet (callers stage the write).
        engine: The engine performing the writes.
        describe: Builds the ``datapackage.json`` descriptor for a member
            format, for layouts that carry one.
    """
    if isinstance(adapter, PackageWriter):
        adapter.write_package(tables, dest, engine, describe=describe)
        return
    for name, expr in tables.items():
        adapter.write(expr, dest / name, engine=engine)


__all__ = [
    "AdapterNotAvailableError",
    "DescriptorFactory",
    "DuplicateAdapterError",
    "FormatAdapter",
    "FormatError",
    "FormatNotDetected",
    "InvalidAdapterError",
    "PackageWriter",
    "ResourceListing",
    "ResourceRef",
    "SourceRef",
    "WriteUnsupportedForSchemeError",
    "dispatch",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "write_package",
]


# ---------------------------------------------------------------------------
# Auto-registered adapters
# ---------------------------------------------------------------------------
# Each adapter module self-registers via ``register_adapter`` at the bottom
# of its module body. Importing them here ensures the registry is populated
# when callers do ``from corral.io import dispatch``.
#
# Order matters only insofar as the probe-chain fallback consults adapters
# in registration order; explicit format, scheme, and extension lookup are
# all order-independent.

# Stock adapters (order = probe-chain fallback order; explicit format,
# scheme, and extension dispatch are all order-independent).
from . import csv_adapter as _csv_adapter  # noqa: E402,F401  -- self-registers
from . import duckdb_adapter as _duckdb_adapter  # noqa: E402,F401  -- self-registers
from . import parquet_adapter as _parquet_adapter  # noqa: E402,F401  -- self-registers
from . import remote as _remote  # noqa: E402,F401  -- self-registers; claims URL schemes per remote.REMOTE_SCHEMES
from . import zipcsv_adapter as _zipcsv_adapter  # noqa: E402,F401  -- self-registers

# Snapshot of the stock registry, for :func:`_reset_registry`.
_STOCK_REGISTRY: dict[str, FormatAdapter] = dict(_REGISTRY)
_STOCK_BY_EXT: dict[str, str] = dict(_BY_EXT)
_STOCK_BY_SCHEME: dict[str, str] = dict(_BY_SCHEME)
