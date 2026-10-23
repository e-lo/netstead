"""Edit the open catalog's files: rename, duplicate, delete (to ``.trash``), restore, and change a card's metadata.

Every handler resolves the files through the Host (``host.readable`` for the card, ``host.writable`` for what it
writes, both inside ``io.allowed_roots``), keeps new files inside the catalog folder (one level of sub-folders,
nothing hidden), validates the card with ``projectcard`` before writing it, and writes through
:mod:`~netstead_project_cards.files` (atomic, never replacing a file unless asked). Nothing on a network changes. Each
change is noted for the session summary (``CardsState.files``), the catalog is rescanned, and a ``host.on_rollback``
callback puts the files, the note and the listing back if an enclosing Action fails. A delete moves the file to
``<catalog>/.trash/`` (hidden, so the scan skips it); ``restore_card`` moves it back.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

import yaml
from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import DeleteCard, DuplicateCard, RenameCard, RestoreCard, UpdateCardMeta
from .card import Dependencies, DraftCard, read_card_file
from .catalog import CARD_SUFFIXES, CatalogEntry
from .errors import CardError
from .files import move_file, undo_move, undo_write, write_file
from .flow import as_action_errors, catalog_entry, load_catalog
from .mapping import FieldMap
from .state import CardsState
from .validate import card_problems

__all__ = ["TRASH", "CatalogEdits", "copy_name"]

logger = logging.getLogger(__name__)

#: The catalog folder's trash: a hidden folder, so the scan (``host.list_dir``) skips it.
TRASH = ".trash"


def copy_name(card_id: str, taken: Callable[[str], bool]) -> str:
    """The default name of a copy of ``card_id``: ``<name>-copy<suffix>``, then ``-copy-2``, … while ``taken``.

    >>> copy_name("sub/main.yml", lambda name: name == "sub/main-copy.yml")
    'sub/main-copy-2.yml'
    """
    path = PurePath(card_id)
    n = 1
    while True:
        name = str(path.with_name(f"{path.stem}-copy{'' if n == 1 else f'-{n}'}{path.suffix}"))
        if not taken(name):
            return name
        n += 1


class CatalogEdits:
    """The handlers that change the open catalog's files (one plugin instance, one Workbench session)."""

    def __init__(self, state: CardsState, mapping: FieldMap) -> None:
        """Bind the handlers to a session's state (the open catalog, the trash and the file notes) and the mapping."""
        self.state, self.mapping = state, mapping

    def specs(self) -> tuple[ActionSpec, ...]:
        """The catalog file Actions, paired with their handlers."""
        return (
            ActionSpec(RenameCard, self.rename_card),
            ActionSpec(DuplicateCard, self.duplicate_card),
            ActionSpec(DeleteCard, self.delete_card),
            ActionSpec(RestoreCard, self.restore_card),
            ActionSpec(UpdateCardMeta, self.update_card_meta),
        )

    # ------------------------------------------------------------------------------------------------ handlers

    def rename_card(self, host: Host, action: RenameCard) -> dict[str, Any]:
        """Move the card's file to ``new_name`` in the catalog folder (validated first; never over another file)."""
        entry, source = self._card(host, action.card_id)
        dest = self._new_path(host, action.new_name, source)
        if dest == source:
            raise ActionError(f"{entry.card_id} is already called {action.new_name}")
        self._validate(source)
        self._refuse_existing(dest, source, overwrite=action.overwrite, verb="rename")
        with as_action_errors():
            moved = move_file(source, dest, overwrite=action.overwrite or dest.exists())
        new_id = self._card_id(dest)
        follow = self.state.follow_file(str(source), {"card_id": new_id, "path": str(dest)})  # open drafts' sources

        def roll_back() -> None:
            follow()
            self._undo(undo_move(moved), dest)

        host.on_rollback(roll_back)
        return self._done(host, {"action": "renamed", "card_id": entry.card_id, "to": new_id, "path": str(dest)})

    def duplicate_card(self, host: Host, action: DuplicateCard) -> dict[str, Any]:
        """Copy the card's file to ``new_name`` (default ``<name>-copy.yml``), byte for byte, after validating it."""
        entry, source = self._card(host, action.card_id)
        folder = self._folder()
        name = action.new_name or copy_name(entry.card_id, lambda n: (folder / n).exists())
        dest = self._new_path(host, name, source)
        if dest == source:
            raise ActionError(f"a copy of {entry.card_id} needs another name")
        self._validate(source)
        self._refuse_existing(dest, source, overwrite=action.overwrite, verb="duplicate")
        with as_action_errors():
            written = write_file(dest, self._bytes(source), overwrite=action.overwrite or dest.exists())
        host.on_rollback(lambda: self._undo(undo_write(written), dest))
        new_id = self._card_id(dest)
        return self._done(host, {"action": "duplicated", "card_id": entry.card_id, "to": new_id, "path": str(dest)})

    def delete_card(self, host: Host, action: DeleteCard) -> dict[str, Any]:
        """Move the card's file to the catalog's ``.trash`` folder (a free name there); never a hard delete."""
        entry, source = self._card(host, action.card_id)
        folder = self._folder()
        trash = host.writable(self._trash_path(folder, entry.card_id))  # inside the catalog folder, so the roots
        with as_action_errors():
            moved = move_file(source, trash, overwrite=False)
        record = {"card_id": entry.card_id, "path": str(source), "trash": str(trash)}
        forget = self.state.trashed(record)
        follow = self.state.follow_file(str(source), None)  # open drafts lose their source: save as a new file

        def roll_back() -> None:
            forget()
            follow()
            self._undo(undo_move(moved), source)

        host.on_rollback(roll_back)
        result = self._done(
            host, {"action": "deleted", "card_id": entry.card_id, "to": f"{TRASH}/", "path": str(trash)}
        )
        return {**result, "trash": str(trash)}

    def restore_card(self, host: Host, action: RestoreCard) -> dict[str, Any]:
        """Move a trashed card back: the newest deletion this session, else ``.trash/<card_id>``."""
        folder = self._folder()
        dest = self._new_path(host, action.card_id, Path(action.card_id))  # refuses a name outside the catalog
        record = self.state.last_trashed(action.card_id) or {"trash": str(folder / TRASH / action.card_id)}
        if not Path(record["trash"]).is_file():
            raise ActionError(f"there is no deleted card {action.card_id!r} in {folder / TRASH}")
        trash = host.readable(record["trash"])
        self._validate(trash)
        self._refuse_existing(dest, trash, overwrite=action.overwrite, verb="restore")
        with as_action_errors():
            moved = move_file(trash, dest, overwrite=action.overwrite or dest.exists())
        remember = self.state.restored(action.card_id)
        new_id = self._card_id(dest)
        follow = self.state.follow_file(str(dest), {"card_id": new_id, "path": str(dest)}, restore=True)

        def roll_back() -> None:
            remember()
            follow()
            self._undo(undo_move(moved), trash)

        host.on_rollback(roll_back)
        return self._done(host, {"action": "restored", "card_id": new_id, "to": new_id, "path": str(dest)})

    def update_card_meta(self, host: Host, action: UpdateCardMeta) -> dict[str, Any]:
        """Change the file's ``project``, ``tags`` and ``dependencies`` keys (and nothing else), validated first."""
        entry, source = self._card(host, action.card_id)
        dest = host.writable(source)
        with as_action_errors():
            card = read_card_file(source, self.mapping)
            update = self._meta(action, card)
            problems = card_problems(card.model_copy(update=update).to_card(self.mapping))
            if problems:
                raise CardError(f"not a valid ProjectCard: {problems[0]}")
            data = self._rewrite(source, update)
            written = write_file(dest, data, overwrite=True)
        host.on_rollback(lambda: self._undo(undo_write(written), dest))
        changed = sorted(k for k in ("project", "tags", "dependencies") if getattr(action, k) is not None)
        record = {"action": "updated", "card_id": entry.card_id, "to": ", ".join(changed), "path": str(dest)}
        return {**self._done(host, record), "project": update["project"], "tags": update["tags"]}

    # ------------------------------------------------------------------------------------------------ steps

    def _folder(self) -> Path:
        """The open catalog's folder (``ActionError`` when none is open)."""
        catalog = self.state.catalog()
        if catalog is None:
            raise ActionError("no catalog is open: open a folder of cards first (project_cards.open_catalog)")
        return Path(catalog.path)

    def _card(self, host: Host, card_id: str) -> tuple[CatalogEntry, Path]:
        """The catalog's card ``card_id`` and its file, which must still be there (inside ``io.allowed_roots``)."""
        entry = catalog_entry(self.state, card_id)
        path = host.readable(entry.path)
        if not path.is_file():
            raise ActionError(f"{entry.card_id} is not a file")
        return entry, path

    def _card_id(self, path: Path) -> str:
        return path.relative_to(self._folder()).as_posix()

    def _new_path(self, host: Host, name: str, like: Path) -> Path:
        """``name`` (a file name, or a path relative to the catalog folder) resolved for writing.

        A name without a suffix takes ``like``'s. Refused: outside ``io.allowed_roots`` (``PathNotAllowed``),
        outside the catalog folder, deeper than one sub-folder, hidden, or not a card suffix.
        """
        folder = self._folder()
        text = name.strip()
        candidate = PurePath(text)
        if not text or not candidate.name:
            raise ActionError("give the card a file name")
        if not candidate.suffix:
            candidate = candidate.with_name(candidate.name + like.suffix)
        if candidate.suffix.lower() not in CARD_SUFFIXES:
            raise ActionError(f"{text}: a card file ends in .yml, .yaml or .json")
        dest = host.writable(candidate if candidate.is_absolute() else folder / candidate)
        try:
            parts = dest.relative_to(folder).parts
        except ValueError:
            raise ActionError(f"{text} is outside the catalog folder {folder}") from None
        if len(parts) > 2 or any(p.startswith(".") for p in parts):
            raise ActionError(f"{text}: a card goes in the catalog folder or one sub-folder of it, and is not hidden")
        return dest

    def _validate(self, path: Path) -> None:
        """Refuse a file that is not a valid ProjectCard (read tolerantly, checked with ``projectcard``)."""
        with as_action_errors():
            card = read_card_file(path, self.mapping)
            problems = card_problems(card.to_card(self.mapping))
            if problems:
                raise CardError(f"{path.name} is not a valid ProjectCard: {problems[0]}")

    def _bytes(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ActionError(f"could not read {path}: {exc.strerror or exc}") from exc

    def _refuse_existing(self, dest: Path, source: Path, *, overwrite: bool, verb: str) -> None:
        """Refuse when another file with other content is at ``dest`` and ``overwrite`` is false."""
        if overwrite or not dest.exists():
            return
        if dest.is_file() and self._bytes(dest) == self._bytes(source):
            return  # the same card: nothing is lost
        raise ActionError(f"{dest.name} already exists in the catalog: {verb} with overwrite=True to replace it")

    def _trash_path(self, folder: Path, card_id: str) -> Path:
        """A free path for ``card_id`` in the trash: ``.trash/<card_id>``, else ``-2``, ``-3``, … before the suffix."""
        path = folder / TRASH / card_id
        n = 2
        while path.exists():
            path = (folder / TRASH / card_id).with_name(f"{PurePath(card_id).stem}-{n}{PurePath(card_id).suffix}")
            n += 1
        return path

    def _meta(self, action: UpdateCardMeta, card: DraftCard) -> dict[str, Any]:
        """The card's new project, tags and dependencies (each unchanged when the Action leaves it ``None``)."""
        tags = card.tags if action.tags is None else [t.strip() for t in action.tags if t.strip()]
        deps = card.dependencies if action.dependencies is None else Dependencies(**action.dependencies)
        project = card.project if action.project is None else action.project.strip()
        if not project:
            raise CardError("a card needs a project name")
        return {"project": project, "tags": tags, "dependencies": deps}

    def _rewrite(self, path: Path, update: dict[str, Any]) -> bytes:
        """The file with its ``project``, ``tags`` and ``dependencies`` keys replaced, in its own format."""
        try:
            text = path.read_text(encoding="utf-8")
            is_json = path.suffix.lower() == ".json"
            raw = json.loads(text) if is_json else yaml.safe_load(text)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            raise CardError(f"could not read {path.name}: {exc}") from exc
        raw["project"] = update["project"]
        for key, value in (("tags", list(update["tags"])), ("dependencies", update["dependencies"].to_card())):
            if value:
                raw[key] = value
            else:
                raw.pop(key, None)
        if is_json:
            return (json.dumps(raw, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        dumped = yaml.safe_dump(raw, sort_keys=False, default_flow_style=False, allow_unicode=True)
        return dumped.encode("utf-8")

    def _undo(self, failed: str | None, path: Path) -> None:
        if failed is not None:
            logger.warning("project_cards: a rolled-back catalog file change at %s could not be %s", path, failed)

    def _done(self, host: Host, record: dict[str, Any]) -> dict[str, Any]:
        """Note the change for the summary, rescan the catalog, and say so; both are undone with the Action."""
        host.on_rollback(self.state.record_file({k: record[k] for k in ("action", "card_id", "to")}))
        folder = self._folder()
        try:
            catalog = load_catalog(host, str(folder), self.mapping)
        except ActionError as exc:  # the folder went away meanwhile: the file change stands
            logger.warning("project_cards: the catalog at %s was not rescanned: %s", folder, exc)
        else:
            host.on_rollback(self.state.swap_catalog(catalog))
            host.publish("catalog", {"path": catalog.path, "cards": len(catalog.cards)})
        return dict(record)
