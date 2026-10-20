"""Remote (URL) format adapter -- two-layer dispatch over fsspec.

RemoteAdapter is the front for any URL-based source: ``http(s)://``,
``s3://``, ``gs://`` / ``gcs://``, ``az://`` / ``abfs(s)://``. It does
**not** parse the underlying bytes itself. Instead it:

    1. Parses the URL and extracts the host.
    2. Resolves storage credentials via :func:`corral.io.credentials.resolve_credentials`.
    3. Determines the **inner** format from the URL's extension (or an
       explicit ``format=`` kwarg).
    4. Looks up the matching inner :class:`FormatAdapter` and delegates
       to its ``read`` / ``write`` / ``scan``, passing
       ``storage_options=...`` through so the inner adapter can hand it
       to fsspec / ibis / polars natively.

This means the credentials cascade is enforced in exactly one place even
though every format (csv, parquet, duckdb, zipcsv) can live behind a URL.

The full list of URL schemes claimed by this adapter is :data:`REMOTE_SCHEMES`
— other modules (notably ``io/__init__.py``) point at that tuple rather than
repeat the list.

Self-registers at module import via :func:`corral.io.register_adapter`.

Examples:
    Direct use is rare; the front-door is :func:`corral.io.dispatch`,
    which routes any URL through RemoteAdapter automatically:

    >>> from corral.io import dispatch
    >>> adapter = dispatch("s3://bucket/data.csv")  # doctest: +SKIP
    >>> adapter.name  # doctest: +SKIP
    'remote'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from fsspec.core import url_to_fs

from corral.io import _scheme_of, dispatch, register_adapter
from corral.io.base import (
    FormatAdapter,
    FormatNotDetected,
    ResourceListing,
    ResourceRef,
    SourceRef,
)
from corral.io.credentials import resolve_credentials
from corral.io.errors import WriteUnsupportedForSchemeError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine, TableExpr
    from corral.spec.model import Schema

__all__ = ["REMOTE_SCHEMES", "RemoteAdapter"]

_logger: Final = logging.getLogger(__name__)

# Schemes RemoteAdapter claims. Order matches the architecture spec.
# Other modules (io/__init__.py, docs) should reference this tuple
# rather than repeat the list — N4 keeps the spelling in one place.
REMOTE_SCHEMES: Final[tuple[str, ...]] = (
    "http",
    "https",
    "s3",
    "gs",
    "gcs",
    "az",
    "abfs",
    "abfss",
)

# Schemes that are read-only over HTTP semantics. ``s3``/``gs``/``az`` etc.
# do support writes through fsspec when credentials and the matching extra
# (``s3fs``/``gcsfs``/``adlfs``) are installed.
_READONLY_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# Inner adapters whose single file holds several tables and that can list
# them over fsspec themselves. ``scan`` delegates to these so each member
# becomes its own ResourceRef (a remote ``.duckdb`` file is not supported).
_REMOTE_CONTAINER_ADAPTERS: Final[frozenset[str]] = frozenset({"zipcsv"})


# ---------------------------------------------------------------------------
# Parsed-URL cache (S6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedUrl:
    """Cheap parse-once view of a URL.

    Before this dataclass existed, ``remote.py`` called ``urlparse(url)`` in
    five+ separate places per ``read`` / ``write`` / ``scan`` invocation
    (S6). One parse, passed by reference, makes the data flow obvious and
    cuts the repeated work.
    """

    raw: str
    scheme: str  # lowercase, no trailing ``://``; ``""`` if absent
    host: str  # bare ``hostname`` (no ``user:pw@`` userinfo, no port) or ``""``
    path: str  # URL path component (the part after scheme://host/)
    stripped: str  # ``raw`` with ``scheme://netloc/`` removed (best-effort)


def _parse(url_str: str) -> _ParsedUrl:
    """Parse ``url_str`` once into the local cache shape."""
    parsed = urlparse(url_str)
    scheme = (parsed.scheme or "").lower()
    # ``hostname``, not ``netloc``: userinfo (``user:secret@``) must never reach the credential
    # lookups or logs, and the cascade keys on the bare host.
    host = parsed.hostname or ""
    path = parsed.path or ""
    # urlparse already strips ``scheme://netloc`` from ``path``; leading
    # ``/`` is part of the path, which we drop for the "use this as the
    # bare local-style key" view. If the resulting string is empty (e.g.
    # the URL was just ``s3://``), fall back to the raw URL so downstream
    # callers don't get an empty surprise.
    stripped = (path.lstrip("/") or url_str) if scheme else url_str
    return _ParsedUrl(raw=url_str, scheme=scheme, host=host, path=path, stripped=stripped)


class RemoteAdapter:
    """URL-based source adapter with credentials cascade + inner-format dispatch.

    The adapter itself owns no parsing logic -- it resolves a URL, attaches
    storage_options, then delegates to whichever sibling adapter owns the
    URL's extension. This keeps the URL and credential machinery in exactly
    one place across all formats.
    """

    name: str = "remote"
    extensions: tuple[str, ...] = ()
    schemes: tuple[str, ...] = REMOTE_SCHEMES

    # ----- probe -----------------------------------------------------------

    def probe(self, source: SourceRef) -> bool:
        """Return True if ``source`` looks like a URL with a known scheme.

        Total -- never raises on malformed input. Non-string sources
        return False.
        """
        if not isinstance(source, str):
            return False
        try:
            scheme = _scheme_of(source)  # None unless literally ``scheme://...`` (same rule as dispatch)
        except (ValueError, AttributeError):
            return False
        return scheme in REMOTE_SCHEMES

    # ----- read ------------------------------------------------------------

    def read(
        self,
        source: SourceRef,
        engine: Engine,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Open ``source`` via fsspec and delegate to the inner-format adapter.

        Args:
            source: A URL string.
            engine: The execution engine, forwarded to the inner adapter.
            schema: Optional Frictionless schema, forwarded.
            **kwargs: Adapter options. Two special keys are consumed here
                and not forwarded:

                - ``credentials``: explicit dict for the credentials
                  cascade. Overrides env / keyring / netrc.
                - ``format``: explicit inner-format override. Used when
                  the URL extension is ambiguous (e.g. an
                  extensionless API endpoint that returns parquet).

                All other kwargs are forwarded verbatim. A
                ``storage_options=...`` entry built from the resolved
                credentials is added only when non-empty — some inner
                engines (notably the ibis duckdb backend) reject an
                unexpected ``storage_options={}`` kwarg, so the empty
                case is omitted entirely (I1).

        Returns:
            Whatever the inner adapter's ``read`` returns -- a lazy table
            expression.

        Raises:
            FormatNotDetected: If the URL has no extension and no
                ``format=`` override was given.
        """
        explicit_creds = kwargs.pop("credentials", None)
        inner_format_override = kwargs.pop("format", None)

        url_str = str(source)
        parsed = _parse(url_str)
        storage_options = self._storage_options_for(parsed, explicit_creds)
        inner = self._inner_adapter(parsed, format=inner_format_override)

        # Don't double-log the credential values -- log identity only.
        _logger.debug("remote.read host=%s inner=%s", parsed.host, inner.name)

        # I1: only forward ``storage_options`` when non-empty. Engines that
        # don't support it (e.g. the ibis duckdb backend on local-fs reads)
        # would otherwise reject an unexpected kwarg.
        inner_kwargs = dict(kwargs)
        if storage_options:
            inner_kwargs["storage_options"] = storage_options
        return inner.read(
            url_str,
            engine,
            schema=schema,
            **inner_kwargs,
        )

    # ----- write -----------------------------------------------------------

    def write(
        self,
        expr: TableExpr,
        dest: SourceRef,
        engine: Engine,
        **kwargs: Any,
    ) -> None:
        """Delegate write to the inner-format adapter, or refuse for HTTP.

        Raises:
            WriteUnsupportedForSchemeError: If ``dest`` is an
                ``http(s)://`` URL. HTTP is read-only in our default
                cloud-backend matrix. Object stores (s3/gs/az) do route
                through to the inner adapter with credentials attached.
                The exception inherits from ``FormatError`` so existing
                ``except FormatError`` blocks keep matching.
        """
        explicit_creds = kwargs.pop("credentials", None)
        inner_format_override = kwargs.pop("format", None)

        url_str = str(dest)
        parsed = _parse(url_str)
        if parsed.scheme in _READONLY_SCHEMES:
            raise WriteUnsupportedForSchemeError(
                f"Writing to {parsed.scheme}:// URLs is not supported "
                "(HTTP is read-only). For cloud writes use s3://, gs://, or az:// "
                "with the matching corral extra installed."
            )

        storage_options = self._storage_options_for(parsed, explicit_creds)
        inner = self._inner_adapter(parsed, format=inner_format_override)

        _logger.debug("remote.write host=%s inner=%s", parsed.host, inner.name)

        inner_kwargs = dict(kwargs)
        if storage_options:
            inner_kwargs["storage_options"] = storage_options
        inner.write(
            expr,
            url_str,
            engine,
            **inner_kwargs,
        )

    # ----- scan ------------------------------------------------------------

    def scan(
        self,
        source: SourceRef,
        engine: Engine | None = None,
        *,
        credentials: dict | None = None,
    ) -> ResourceListing:
        """List one-or-more resources at ``source``.

        For a URL pointing at a single file the listing has one entry,
        except a zip of csvs, whose members are listed by the zipcsv
        adapter (refs carry ``container_adapter``/``member``; read each
        back with ``read(ref.container, format=ref.container_adapter,
        table=ref.member)``). For a URL pointing at a directory-like
        prefix (``s3://b/p/``), the fsspec filesystem for the URL is
        consulted — built from ``credentials`` when given, else from the
        credentials cascade for the host (same rule as :meth:`read`).
        Each child with a recognised extension contributes one
        :class:`ResourceRef` whose path is the child's full URL (same
        scheme and host as ``source``), so reading it routes back through
        this adapter and the same credentials.
        """
        url_str = str(source)
        parsed = _parse(url_str)

        # Trailing-slash heuristic: an explicit directory-shaped URL.
        looks_like_dir = url_str.endswith("/")

        if looks_like_dir or _path_has_no_extension(parsed):
            try:
                storage_options = self._storage_options_for(parsed, credentials)
                fs, dir_path = url_to_fs(url_str, **storage_options)
            except (ValueError, ImportError):
                # fsspec couldn't build a filesystem for this scheme
                # (missing optional dep, etc.). Fall back to single-resource.
                fs = None

            if fs is not None:
                # Only consult ls when fsspec confirms a directory. We catch
                # narrowly here (I4): a network blip, permission denied, or
                # missing prefix should degrade to single-resource with a
                # logged warning, but anything else (TypeError, KeyError,
                # bug in fsspec) propagates so we don't paper over real
                # programmer errors.
                is_dir = False
                try:
                    is_dir = bool(fs.isdir(dir_path))
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    _logger.warning(
                        "listing fell back to single-resource for host=%s: %s",
                        parsed.host,
                        exc,
                    )
                    is_dir = looks_like_dir
                if is_dir:
                    # ``ls`` names children in the filesystem's own form (``bucket/p/x.csv``,
                    # no scheme; adlfs also drops the ``@account``). Rebuild each child's URL
                    # from the listed URL so the scheme and host survive exactly as given.
                    base = url_str.rstrip("/")
                    children = [str(child) for child in fs.ls(dir_path, detail=False)]
                    return [
                        _resource_ref_for(f"{base}/{PurePosixPath(child).name}")
                        for child in children
                        if _has_known_extension(child)
                    ]

        try:
            inner = self._inner_adapter(parsed)
        except FormatNotDetected:
            inner = None
        if inner is not None and inner.name in _REMOTE_CONTAINER_ADAPTERS:
            return inner.scan(url_str, engine)

        # Single-resource fallback (also the path for non-dir URLs).
        return [_resource_ref_for(url_str)]

    # ----- internals -------------------------------------------------------

    def _storage_options_for(self, parsed: _ParsedUrl, explicit: dict | None) -> dict:
        """Resolve credentials for ``parsed``'s host."""
        return resolve_credentials(parsed.host, explicit=explicit)

    def _inner_adapter(self, parsed: _ParsedUrl | str, format: str | None = None) -> FormatAdapter:
        """Resolve the inner-format adapter for ``parsed``.

        Accepts either a pre-parsed :class:`_ParsedUrl` (the public
        ``read``/``write`` paths pass this) or a plain URL string (for
        ad-hoc callers and unit tests that don't want to touch the
        internal dataclass).

        Constraint: dispatching the URL itself would come straight back
        here, because this adapter owns the URL schemes. So dispatch once,
        on the path with ``scheme://netloc`` stripped: with no scheme left
        to match, only ``format=``, the extension or a probe can answer.
        If the answer is still this adapter (a URL with no path, or an
        extension bound to ``remote``), raise rather than recurse.

        Raises:
            FormatNotDetected: If nothing but this adapter claims the path.
        """
        if isinstance(parsed, str):
            parsed = _parse(parsed)
        adapter = dispatch(parsed.stripped, format=format)
        if adapter.name == self.name:
            # Name the URL without its userinfo: the message may end up in a log.
            shown = f"{parsed.scheme}://{parsed.host}{parsed.path}"
            raise FormatNotDetected(
                f"RemoteAdapter cannot resolve inner format for {shown!r}: "
                "no other adapter claims its path after the scheme is stripped. "
                "Pass format= or use a URL whose path ends in a registered extension."
            )
        return adapter


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _host_of(url_str: str) -> str:
    """Return the host portion of a URL, or the empty string.

    Kept for back-compat with callers (and tests) that still import the
    helper directly. New code should use :func:`_parse` and read
    ``parsed.host``.
    """
    return _parse(url_str).host


def _strip_scheme(url_str: str) -> str:
    """Return ``url_str`` with the ``scheme://netloc`` prefix removed.

    Kept for back-compat; new code should use :func:`_parse` and read
    ``parsed.stripped``.
    """
    return _parse(url_str).stripped


def _path_has_no_extension(parsed: _ParsedUrl) -> bool:
    """True if the URL's tail has no ``.ext`` suffix."""
    name = PurePosixPath(parsed.path).name
    return "." not in name


def _has_known_extension(path: str) -> bool:
    """Return True if ``path`` ends in an extension registered with the registry.

    Before S1 this was a permissive ``"." in name`` check that admitted
    junk like ``_SUCCESS.txt`` or stale ``manifest.json`` files. Now we
    consult the registry's extension map so only files whose suffix is
    actually owned by a registered adapter survive.

    Fast path: if the registry has no entries (early import order or a
    test that called ``_clear_registry``), fall back to the cheap
    ``"." in name`` heuristic so we don't silently drop everything.
    """
    name = PurePosixPath(path).name
    if "." not in name:
        return False
    # Import inside the function so we read the *current* registry state
    # rather than a snapshot captured at module-import time. This matters
    # for the test harness, which re-clears the registry between tests.
    from corral.io import _BY_EXT

    if not _BY_EXT:
        # Registry not (yet) populated — fall back to the loose check.
        return True

    lower = name.lower()
    # Try compound extensions first (``foo.csv.zip`` should hit
    # ``csv.zip`` before ``zip``).
    parts = lower.split(".")
    return any(".".join(parts[i:]) in _BY_EXT for i in range(1, len(parts)))


def _resource_ref_for(path: str) -> ResourceRef:
    """Build a ResourceRef from a single file path / URL.

    The ``format`` field is best-effort: the file's extension when
    present, else ``"remote"``.
    """
    parsed_path = urlparse(path).path or path
    posix = PurePosixPath(parsed_path)
    name = posix.stem or path
    suffixes = posix.suffixes
    fmt = suffixes[-1].lstrip(".").lower() if suffixes else "remote"
    return ResourceRef(name=name, path=path, format=fmt)


# ---------------------------------------------------------------------------
# Self-registration
# ---------------------------------------------------------------------------

register_adapter(RemoteAdapter())
