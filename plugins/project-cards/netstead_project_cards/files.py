"""Write a card file so that a failure never leaves a half-written or lost file, and undo the write.

The bytes go to a temporary file in the destination folder first. It is then moved over the destination
(``os.replace``, atomic) to overwrite, or hard-linked to it (``os.link``, which fails if a file is already there) to
create, so a file that appears after any check is never replaced. A filesystem without hard links gets an exclusive
create instead, which never replaces a file either. The temporary file is removed on every path.

A move (:func:`move_file`, for renaming a catalog card or putting it in the catalog's trash) is that write followed by
removing the source, so it never replaces a file either; :func:`undo_move` puts both back.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import CardError

__all__ = ["Moved", "Written", "move_file", "undo_move", "undo_write", "write_file"]


@dataclass(frozen=True)
class Written:
    """What :func:`write_file` did: the file, the bytes it replaced (``None``: it was new), the folders it made."""

    path: Path
    previous: bytes | None
    made: tuple[Path, ...]  # deepest first


def write_file(path: Path, data: bytes, *, overwrite: bool) -> Written:
    """Write ``data`` to ``path``, making its missing folders; replace an existing file only with ``overwrite``.

    Raises:
        CardError: the file exists and ``overwrite`` is false, or the write failed (nothing is left behind: the
            previous file is intact, and the folders it made are removed).
    """
    made: tuple[Path, ...] = ()
    try:
        made = _missing_folders(path)
        previous = _read(path) if overwrite else None
        path.parent.mkdir(parents=True, exist_ok=True)
        _place(path, data, overwrite=overwrite)
    except _Exists:
        _remove_folders(made)
        raise CardError(f"{path} already exists; commit with overwrite=True to replace it") from None
    except OSError as exc:
        _remove_folders(made)
        raise CardError(f"could not write {path}: {exc.strerror or exc}") from exc
    return Written(path, previous, made)


def undo_write(written: Written) -> str | None:
    """Put back what :func:`write_file` replaced (or remove the file it made) and its folders.

    Returns ``None``, or what could not be done (``"restored"`` or ``"removed"``) for the caller's error message.
    """
    try:
        if written.previous is None:
            written.path.unlink(missing_ok=True)
        else:
            _place(written.path, written.previous, overwrite=True)
    except OSError:
        return "removed" if written.previous is None else "restored"
    _remove_folders(written.made)
    return None


@dataclass(frozen=True)
class Moved:
    """What :func:`move_file` did: the source it removed (and its bytes), and the write that made the destination."""

    source: Path
    data: bytes
    written: Written


def move_file(source: Path, dest: Path, *, overwrite: bool) -> Moved:
    """Move ``source`` to ``dest`` (making its folders); replace an existing ``dest`` only with ``overwrite``.

    Raises:
        CardError: ``source`` cannot be read, ``dest`` exists and ``overwrite`` is false, or a write or the removal
            failed (then nothing has changed: the destination is undone).
    """
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise CardError(f"could not read {source}: {exc.strerror or exc}") from exc
    written = write_file(dest, data, overwrite=overwrite)
    try:
        source.unlink()
    except OSError as exc:
        undo_write(written)
        raise CardError(f"could not remove {source}: {exc.strerror or exc}") from exc
    return Moved(source, data, written)


def undo_move(moved: Moved) -> str | None:
    """Put a moved file back where it was and undo its destination; ``None``, or what could not be done."""
    try:
        _place(moved.source, moved.data, overwrite=False)  # a file that appeared there since is never replaced
    except (_Exists, OSError):
        return "restored"
    return undo_write(moved.written)


def _missing_folders(path: Path) -> tuple[Path, ...]:
    """The folders above ``path`` that do not exist yet, deepest first."""
    missing = []
    folder = path.parent
    while not folder.exists():
        missing.append(folder)
        folder = folder.parent
    return tuple(missing)


def _remove_folders(folders: tuple[Path, ...]) -> None:
    """Remove the folders (deepest first) a failed write made; stop at the first that is not empty or not removable."""
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            return


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _place(path: Path, data: bytes, *, overwrite: bool) -> None:
    """Write a temporary file beside ``path``, then move it over ``path`` or link it to a new ``path``."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        _create(tmp, data)
        if overwrite:
            os.replace(tmp, path)
        else:
            _link(tmp, path, data)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


class _Exists(Exception):
    """The destination exists and the write may not replace it."""


def _link(tmp: Path, path: Path, data: bytes) -> None:
    """Make ``path`` a new file holding ``data`` (a hard link to ``tmp``); raise ``_Exists`` if it is there."""
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise
        except OSError:  # no hard links on this filesystem: an exclusive create never replaces a file either
            _create(path, data)
    except FileExistsError:
        raise _Exists from None


def _create(path: Path, data: bytes) -> None:
    """Create ``path`` (failing if it exists) holding ``data``, flushed to disk; remove it if writing fails."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
