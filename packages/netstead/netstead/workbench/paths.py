"""Local-path policy for the workbench: every local read or write stays under ``io.allowed_roots``.

An empty ``io.allowed_roots`` means "the user's home directory". Paths are
resolved (``~`` expanded, symlinks followed, ``..`` collapsed) *before* the
containment check, so neither a symlink nor a ``..`` segment can escape a root.
Only remote URLs (corral's :data:`~corral.io.remote.REMOTE_SCHEMES`: http(s), s3, gs/gcs,
az/abfs(s)) skip the check. ``duckdb://<path>`` and ``file://<path>`` are local paths in disguise
and are checked like any other; every other scheme, and fsspec ``::`` chains, are rejected.
:func:`classify_source` is the one entry point for "a source string the user typed".
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname

from corral.io.remote import REMOTE_SCHEMES

from netstead.config import Settings

from .errors import PathNotAllowed
from .redact import scrub_source

__all__ = [
    "SourceKind",
    "allowed_roots",
    "classify_source",
    "is_allowed",
    "is_url",
    "local_locator",
    "open_locator",
    "resolve_allowed",
    "split_source",
]


SourceKind = Literal["remote", "local"]


def _scheme(source: str) -> str:
    """The URL scheme, lower-cased; ``""`` for a plain path (a one-letter Windows drive is not a scheme)."""
    scheme = urlsplit(source).scheme
    return scheme.lower() if len(scheme) > 1 else ""


def is_url(source: str) -> bool:
    """Whether ``source`` is a remote URL (a scheme in corral's ``REMOTE_SCHEMES``)."""
    try:
        return split_source(source)[0] == "remote"
    except PathNotAllowed:
        return False


def split_source(source: str) -> tuple[SourceKind, str]:
    """Split ``source`` into ``("remote", url)`` or ``("local", path)`` without checking the roots.

    ``duckdb://`` and ``file://`` prefixes are stripped to their local path. Raises
    :class:`PathNotAllowed` for an fsspec ``::`` chain, any other scheme, or a remote URL carrying
    ``user:password@`` credentials (those belong in env/keyring/netrc, never in a recorded source),
    ``..`` path segments, or backslashes.
    """
    source = str(source)
    # urlsplit drops leading whitespace/C0 characters and deletes tab/CR/LF, so " s3://..." or
    # "s\t3://..." would look remote here while corral reads them as relative local paths.
    if source != source.strip() or any(ord(c) < 32 or ord(c) == 127 for c in source):
        raise PathNotAllowed(
            f"{scrub_source(source)!r}: sources must not have surrounding whitespace or control characters"
        )
    if "::" in source:
        raise PathNotAllowed(f"{scrub_source(source)}: chained (::) URLs are not supported")
    scheme = _scheme(source)
    if not scheme:
        return "local", source
    if scheme in REMOTE_SCHEMES:
        # "s3:../x" or "http:/abs" parse with a remote scheme but corral's inner adapters read
        # them as relative local paths: only a literal scheme://host is remote.
        parts = urlsplit(source)
        if not source.lower().startswith(f"{scheme}://") or not parts.hostname:
            raise PathNotAllowed(f"{scrub_source(source)}: remote URLs must look like scheme://host/...")
        if "@" in parts.netloc:  # its userinfo is the secret: never accept (or echo) it
            raise PathNotAllowed(
                f"{scrub_source(source)}: credentials in the URL (user:password@) are not accepted; "
                "set them in the environment, the keyring, or ~/.netrc (the credential cascade) instead"
            )
        if ".." in PurePosixPath(parts.path).parts:
            raise PathNotAllowed(f"{scrub_source(source)}: remote URLs must not contain '..' path segments")
        if "\\" in source:  # WHATWG URL parsers read a backslash as "/", which could hide a '..' segment
            raise PathNotAllowed(f"{scrub_source(source)}: remote URLs must not contain backslashes")
        return "remote", source
    if scheme == "duckdb":
        return "local", re.sub(r"^duckdb:(//)?", "", source, flags=re.IGNORECASE)
    if scheme == "file":
        parts = urlsplit(source)
        if parts.netloc not in ("", "localhost"):
            raise PathNotAllowed(f"{scrub_source(source)}: file:// URLs must name a local path")
        return "local", url2pathname(parts.path)
    remote = ", ".join(REMOTE_SCHEMES)
    raise PathNotAllowed(
        f"{scrub_source(source)}: unsupported URL scheme {scheme!r}; use a local path or one of {remote}"
    )


def classify_source(source: str, settings: Settings) -> tuple[SourceKind, str | Path]:
    """``("remote", url)``, or ``("local", resolved_path)`` checked against ``io.allowed_roots``.

    Raises :class:`PathNotAllowed` for an unsupported scheme or a local path outside every root.
    """
    kind, target = split_source(source)
    return (kind, target) if kind == "remote" else (kind, resolve_allowed(target, settings))


def local_locator(source: str, path: str | Path) -> str:
    """What to open for local ``path`` named by ``source``: keeps a ``duckdb://`` format hint (``net.db``)."""
    return f"duckdb://{path}" if _scheme(str(source)) == "duckdb" else str(path)


def open_locator(source: str, settings: Settings) -> str:
    """The locator to hand ``Network.from_source``: the remote URL, or the checked local path.

    Like :func:`classify_source` (and raises the same errors), but a ``duckdb://`` source stays
    ``duckdb://<resolved path>`` so a DuckDB file without a ``.duckdb`` extension still opens.
    """
    kind, target = split_source(source)
    if kind == "local" and _bundled(target):
        return local_locator(source, _resolve(target))
    kind, target = classify_source(source, settings)
    return str(target) if kind == "remote" else local_locator(source, target)


def _bundled(path: str) -> bool:
    """Whether ``path`` is the folder of an example network bundled with netstead (openable outside the roots)."""
    from .examples import is_bundled_example  # deferred: importing it loads the fixtures and duckdb

    try:
        return is_bundled_example(_resolve(path))
    except PathNotAllowed:
        return False


def allowed_roots(settings: Settings) -> list[Path]:
    """The resolved allowed roots (``io.allowed_roots``, or ``[home]`` when that list is empty)."""
    return [Path(r).expanduser().resolve() for r in settings.io.allowed_roots or [str(Path.home())]]


def _inside(candidate: Path, roots: list[Path]) -> bool:
    return any(candidate.is_relative_to(root) for root in roots)


def _resolve(path: str | Path) -> Path:
    """``path`` with ``~`` expanded and fully resolved; :class:`PathNotAllowed` if it cannot be."""
    try:
        return Path(path).expanduser().resolve()
    except (RuntimeError, ValueError, OSError) as exc:  # ~nosuchuser, NUL bytes, symlink loops
        raise PathNotAllowed(f"{path!r} is not a usable local path: {exc}") from exc


def is_allowed(path: str | Path, settings: Settings) -> bool:
    """Whether ``path`` resolves inside an allowed root."""
    try:
        return _inside(_resolve(path), allowed_roots(settings))
    except PathNotAllowed:
        return False


def resolve_allowed(path: str | Path, settings: Settings) -> Path:
    """Return ``path`` fully resolved, or raise :class:`PathNotAllowed` if it falls outside every allowed root."""
    candidate = _resolve(path)
    roots = allowed_roots(settings)
    if not _inside(candidate, roots):
        shown = ", ".join(str(r) for r in roots)
        raise PathNotAllowed(
            f"{path} is outside the allowed folders ({shown}); add a parent folder to io.allowed_roots"
        )
    return candidate
