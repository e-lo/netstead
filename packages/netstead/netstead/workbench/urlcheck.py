"""The wizard's URL **Check**: is a remote GMNS source reachable, which credential layer applies, what's in it.

Read-only and never records an action. It reports the *name* of the credential source
(``env``/``keyring``/``netrc``/``none``, from :func:`corral.io.credentials.credential_source`),
never a credential value, and lists table names only (no data is read).

This probes an arbitrary ``http(s)``/``s3``/``gs``/``az``/... host supplied by the caller; it is meant
for the workbench's local, single-user server, not for exposure to untrusted callers. ``check_url``
itself never raises: every failure, including a bug in this module's own error handling, is reported
back as ``error`` rather than propagated.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import fsspec
from corral.io.credentials import credential_source, resolve_credentials

from .errors import PathNotAllowed
from .paths import split_source

# Re-imported under urlcheck's own names: ``_check_url`` looks ``_scrub`` up here, so a test can
# monkeypatch ``urlcheck._scrub`` to prove that ``check_url`` never raises.
from .redact import safe_scrub as _safe_scrub
from .redact import scrub as _scrub

__all__ = ["check_url"]

logger = logging.getLogger(__name__)


_TABLE_SUFFIXES = (".csv", ".parquet")
#: Cap on how many directory entries we *process* when listing tables. ``fs.ls`` itself may still
#: fetch (and pay for) the full listing from the backend; this only bounds the work done on it here.
_MAX_LS_ENTRIES = 1000


def _tables(fs: Any, path: str) -> list[str]:
    entries = fs.ls(path, detail=False)[:_MAX_LS_ENTRIES]
    names = (PurePosixPath(str(p)).name for p in entries)
    return sorted({n.split(".", 1)[0] for n in names if n.lower().endswith(_TABLE_SUFFIXES)})


def _http_timeout_options(storage_options: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Merge an ``aiohttp.ClientTimeout`` into ``client_kwargs`` without dropping existing keys.

    fsspec's HTTP backend passes ``client_kwargs`` straight to ``aiohttp.ClientSession``, which (as
    of aiohttp 3.x) rejects a bare numeric ``timeout=`` with "use timeout=ClientTimeout(...)" -- a
    plain ``{"timeout": timeout_s}`` breaks every http(s) check outright. Imported lazily so a
    missing aiohttp only disables this best-effort knob rather than the whole module.
    """
    try:
        import aiohttp
    except ImportError:
        return storage_options
    client_kwargs = {**storage_options.get("client_kwargs", {}), "timeout": aiohttp.ClientTimeout(total=timeout_s)}
    return {**storage_options, "client_kwargs": client_kwargs}


def check_url(
    url: str,
    *,
    url_to_fs: Callable[..., tuple[Any, str]] = fsspec.core.url_to_fs,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Probe ``url`` and describe it for the wizard.

    The probe (``url_to_fs`` plus ``exists``/``isdir``/``ls``) runs on a daemon thread so a slow or
    unresponsive host cannot hang the caller, or the interpreter at exit: we wait at most
    ``timeout_s`` for it (``Thread.join(timeout_s)``). On a timeout the thread is not canceled
    (threads cannot be killed from outside) and may keep running in the background until the
    underlying call itself returns or errors; only its result is discarded, and being a daemon
    thread it never blocks process shutdown.

    Args:
        url: An ``http(s)``/``s3``/``gs``/``az``/... URL to a GMNS folder or file.
        url_to_fs: ``fsspec.core.url_to_fs``-compatible factory (injectable for tests).
        timeout_s: Maximum time to wait for the probe before reporting it as unreachable.

    Returns:
        ``{"url", "reachable", "credential_source", "kind", "tables", "error"}`` where ``kind`` is
        ``"folder"``, ``"file"``, or ``None`` when unreachable, and ``tables`` lists the table names
        found directly inside a folder (``link``, ``node``, ...). Both ``url`` and ``error`` are
        scrubbed: neither ever contains a credential value, and any URL inside either has had its
        query string and userinfo stripped. This function never raises.
    """
    try:
        return _check_url(url, url_to_fs=url_to_fs, timeout_s=timeout_s)
    except Exception as exc:  # pragma: no cover - final boundary; check_url must never raise
        logger.debug("check_url crashed: %s", _safe_scrub(str(exc)))
        return {
            "url": _safe_scrub(url),
            "reachable": False,
            "credential_source": "none",
            "kind": None,
            "tables": [],
            "error": _safe_scrub(f"{type(exc).__name__}: {exc}"),
        }


def _check_url(url: str, *, url_to_fs: Callable[..., tuple[Any, str]], timeout_s: float) -> dict[str, Any]:
    parts = urlsplit(url)
    report: dict[str, Any] = {
        "url": _scrub(url),
        "reachable": False,
        "credential_source": "none",
        "kind": None,
        "tables": [],
        "error": None,
    }
    scheme = parts.scheme.lower()
    if "::" in url:
        report["error"] = "chained (::) URLs are not supported; give a single remote URL"
        return report
    if scheme == "file":
        report["error"] = "file:// is not accepted here; use the local file browser for local paths"
        return report
    # The same rules as OpenNetwork (scheme://host, no '..', no userinfo, ...), so Check and Open agree.
    try:
        kind, _ = split_source(url)
    except PathNotAllowed as exc:
        report["error"] = _scrub(str(exc))
        return report
    if kind == "local":
        report["error"] = "not a URL; use the local file browser for local paths"
        return report
    # Bare hostname, never userinfo (a presigned/keyed URL's netloc can be "key:secret@host") and
    # never the port: credential_source/resolve_credentials already key on the bare host (see
    # corral.io.credentials._sanitize_host), so this also matches their existing lookup shape.
    host = parts.hostname or ""
    report["credential_source"] = credential_source(host)
    storage_options = resolve_credentials(host)
    if scheme in ("http", "https"):
        storage_options = _http_timeout_options(storage_options, timeout_s)

    def _probe() -> dict[str, Any]:
        result: dict[str, Any] = {"reachable": False, "kind": None, "tables": [], "error": None}
        try:
            fs, path = url_to_fs(url, **storage_options)
            if not fs.exists(path):
                result["error"] = "not found"
                return result
            result["reachable"] = True
            if fs.isdir(path):
                result["kind"], result["tables"] = "folder", _tables(fs, path)
            else:
                result["kind"] = "file"
        except ImportError as exc:
            result["error"] = _scrub(f"missing filesystem support for {scheme}:// ({exc})")
        except Exception as exc:  # boundary: any remote failure is reported to the user, not raised
            port = f":{parts.port}" if parts.port else ""
            logger.debug("check_url %s%s failed: %s", host, port, _scrub(str(exc)))
            result["error"] = _scrub(f"{type(exc).__name__}: {exc}")
        return result

    outcome: dict[str, Any] = {}

    def _runner() -> None:
        outcome["result"] = _probe()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        report["error"] = f"timed out after {timeout_s:g}s"
    elif "result" in outcome:
        report.update(outcome["result"])
    else:  # pragma: no cover - defensive: _probe always sets outcome["result"] before returning
        report["error"] = "probe thread exited without a result"
    return report
