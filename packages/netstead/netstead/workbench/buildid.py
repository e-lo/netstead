"""Build ids that put the front end under a URL no stale browser cache can mix with another build's.

The front end is ES modules importing one another by relative URL. A browser that kept an old module from an
earlier netstead (heuristically cached, before ``Cache-Control: no-cache`` was sent) and fetches a fresh one
next to it gets a missing-export SyntaxError and a page whose handlers never attach. Serving each build under
``/static/b/<build-id>/`` gives every upgrade (or edited file) fresh URLs, and the relative imports inherit the prefix.
"""

from __future__ import annotations

import hashlib
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

__all__ = ["plugin_build_id", "static_build_id"]

_ID_LENGTH = 10


def _netstead_version() -> str:
    try:
        return version("netstead")
    except PackageNotFoundError:  # running from a source tree without an install record
        return "unknown"


def static_build_id(directory: Path, salt: str = "") -> str:
    """Short hash of ``salt``, the netstead version, and every file's relative path, size and mtime under ``directory``.

    A missing directory hashes as empty (the caller reports that failure itself).
    """
    digest = hashlib.sha256(f"{_netstead_version()}\0{salt}".encode())
    if directory.is_dir():
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            stat = path.stat()
            digest.update(f"\0{path.relative_to(directory).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:_ID_LENGTH]


@cache
def plugin_build_id(directory: Path, plugin_id: str, plugin_version: str) -> str:
    """:func:`static_build_id` for a plugin's static dir, once per process so the session and the mount agree."""
    return static_build_id(directory, f"{plugin_id}\0{plugin_version}")
