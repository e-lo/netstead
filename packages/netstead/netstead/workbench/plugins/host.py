"""``Host``: everything a plugin may touch in a Workbench session (the plugin API, :data:`HOST_API`).

A plugin never sees the :class:`~netstead.workbench.session.Session`; it gets one ``Host`` bound to
its own id. Handlers run under the session lock, so ``Host`` methods are safe to call from them;
``submit_job`` work runs on its own thread, and the ``mutate``/``derive`` it calls take the lock.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ..editing import EditPlan, plan_add, plan_delete, plan_update
from ..errors import ActionError
from ..files import list_dir
from ..ledger import PendingEdits
from ..paths import resolve_allowed
from ..selection import payload_facility_forms, selection_warnings

if TYPE_CHECKING:
    from corral.editing import Edit, EditResult

    from ..actions import BaseAction
    from ..jobs import JobContext
    from ..registry import NetworkHandle
    from ..session import Session
    from .spec import WorkbenchPlugin

__all__ = ["Host", "Mutation", "PendingEdits", "PluginSettingsError", "validate_plugin_settings"]

#: A ``host.publish`` event name: no dot, since ``wb.on("a.b")`` in the browser means plugin ``a``'s event ``b``.
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class Mutation:
    """What :meth:`Host.mutate` returns (Host API 1.6): the edits' corral results and the pending edit's id.

    ``results`` are the :class:`~corral.editing.EditResult` objects, in order (the rollback data). ``seq`` is the
    pending edit's id (``None`` when there were no edits, so no pending edit): :meth:`Host.pending_edits` lists it while
    the network holds the edit, and :meth:`Host.undo` names it. A network id's seqs are never handed out twice in a
    session: not after a rolled-back Action spent one, nor after the network is closed and opened again.
    """

    results: tuple[EditResult, ...]
    seq: int | None


class PluginSettingsError(ValueError):
    """A plugin's ``[plugins.<id>]`` settings failed its model (the message never carries a value)."""


def validate_plugin_settings(model: type[BaseModel], data: dict[str, Any], plugin_id: str) -> BaseModel:
    """Validate ``data`` with ``model``; raise :class:`PluginSettingsError` naming keys, never values."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        where = ", ".join(
            f"plugins.{plugin_id}." + ".".join(str(p) for p in e["loc"]) + f" ({e['msg']})" for e in errors
        )
        message = f"invalid settings for plugin {plugin_id!r}: {where}"
    raise PluginSettingsError(message)  # outside ``except``: no __context__ carrying the input


class Host:
    """One plugin's handle on the session (see the cookbook page "Write a Workbench plugin")."""

    def __init__(self, session: Session, plugin: WorkbenchPlugin) -> None:
        """Bind ``plugin`` to ``session``."""
        self._session = session
        self._plugin = plugin

    @property
    def plugin_id(self) -> str:
        """This plugin's id (its namespace)."""
        return self._plugin.id

    @property
    def settings(self) -> BaseModel | None:
        """This plugin's ``[plugins.<id>]`` settings, validated by its ``settings_model`` (``None`` if it has none)."""
        model = self._plugin.settings_model
        if model is None:
            return None
        return validate_plugin_settings(model, self._session.settings.plugins.get(self.plugin_id, {}), self.plugin_id)

    @property
    def active(self) -> str | None:
        """The active network's id."""
        return self._session.active

    @property
    def selection(self) -> dict[str, Any] | None:
        """A copy of the shared selection payload (the dict ``/api/state`` carries), plus its ``projectcard``.

        ``projectcard`` holds the ProjectCard facility forms of a resolved selection (``picked``, ``resolved``,
        ``query``), or ``None``: core owns the facility form, so a plugin never builds its own. Since 1.9 it also
        has the strict forms: ``segment`` and ``link_list`` (each ``{id system: facility}``, ``gmns`` and ``osm``),
        ``segment_problem``, ``id_systems`` (``{system: None or why not}``) and ``id_captions``.
        """
        # Not via ``state()``: that calls every plugin's ``state(host)``, which may read this property.
        sel = self._session.selection_copy()
        if sel is not None:
            sel["projectcard"] = payload_facility_forms(sel, self._selection_network(sel))
        return sel

    def _selection_network(self, payload: dict[str, Any]) -> NetworkHandle | None:
        """The network a selection payload is on (``None`` once it is closed), to look its ids up in."""
        try:
            return self._session.network(payload.get("net_id"))
        except (ActionError, KeyError):
            return None

    def saved_selections(self) -> list[dict[str, Any]]:
        """Each saved selection: ``name``, ``net_id``, ``status``, ``links``, ``utterance``, ``by``, ``stale``."""
        return self._session.saved_selections()

    def saved_selection(self, name: str) -> dict[str, Any] | None:
        """A copy of the selection saved as ``name``, plus its ``projectcard`` forms; ``None`` if there is none.

        It is the payload as saved; ``UseSelection`` resolves it again on the network as it is now. ``stale_pins``
        is non-empty when its pin had already gone stale (an edit took the pinned node off the facility, so the
        links are the ones selected before the edit), and ``warnings`` says so in words.
        """
        sel = self._session.saved_selection(name)
        if sel is not None:
            sel["projectcard"] = payload_facility_forms(sel, self._selection_network(sel))
            sel["warnings"] = selection_warnings(sel)
        return sel

    def network(self, net_id: str | None = None) -> NetworkHandle:
        """The handle for ``net_id`` (default: active). Read it; change it only via :meth:`mutate`."""
        return self._session.network(net_id)

    def mutate(
        self, net_id: str | None, edits: Sequence[Edit], *, note: str, expected_version: int | None = None
    ) -> Mutation:
        """Apply corral edits all-or-nothing and return their results; lineage gets ``"<plugin id>: <note>"``.

        Inside an Action handler, a failing handler undoes it. Anywhere else (a job, a route) it is
        recorded as a non-replayable ``<plugin id>.mutate`` history entry. It becomes a pending edit whose
        source is this plugin: core's Edits panel lists it with the plugin's id, and core's Undo never
        reverses it (use :meth:`undo`). ``expected_version`` (a plan's ``version``) refuses the edits with
        ``ActionError`` if the network changed since: plan again. Storage refusing an edit (corral's
        ``EditingError``, an Arrow, ibis or DuckDB error) is an ``ActionError`` too, ``"the edit failed: ..."``
        with URL credentials and keys scrubbed, and changes nothing; any other exception re-raises as it is.
        The result is a :class:`Mutation`: the corral results, plus the pending edit's ``seq`` (1.6).
        """
        with self._session.consistent():  # the seq read must be this mutation's, not a later one's
            net = self._session.network(net_id).id
            results = self._session.mutate(
                net, edits, note=note, origin=self.plugin_id, expected_version=expected_version
            )
            last = self._session.edits[net].last() if results else None
        return Mutation(tuple(results), last.seq if last is not None else None)

    def pending_edits(self, net_id: str | None = None) -> PendingEdits:
        """The network's pending edits from every source, read in one moment under the session lock (1.6).

        ``edits`` holds each one, oldest first, as the view :meth:`undo` returns: ``seq`` (the id :meth:`mutate`
        returned), ``source`` (a plugin id, ``"workbench"`` or ``"python"``), ``note``, ``version`` (the network's
        version just after it), ``rows`` and ``saved``. ``issued`` is the newest seq handed out on the network at that
        moment: an edit you hold with a seq above it was made after the read, so the read says nothing about it. Check
        your own edits against this, not against the network's lineage. Raises ``ActionError`` when the network is
        not open.
        """
        return self._session.pending_edits(net_id)

    def on_rollback(self, fn: Callable[[], None]) -> None:
        """Call ``fn()`` if the Action now running is rolled back, so this plugin can put its own state back (1.6).

        Core rolls back a failed Action's network changes, derived networks and view state, but not a plugin's state
        or the files it wrote. Register ``fn`` after the change it undoes: it runs if this Action fails, or if it
        succeeds and an Action it is nested in fails later (``host.dispatch``). Rollback runs newest first, so the
        network changes made after ``fn`` was registered are already undone when it runs. It runs once at most, under
        the session lock: restore your own state and files there, but call no ``Host`` method (``mutate``, ``derive``,
        ``undo``, ``dispatch`` and ``on_rollback`` raise ``RuntimeError`` from a rollback callback). Lock order is the
        session lock, then your own: take your lock inside ``fn`` if you need it, and never call ``Host`` while holding
        your lock. An exception in it is logged and the rollback goes on; a ``BaseException`` is re-raised once the
        rollback has finished. Raises ``RuntimeError`` outside an Action handler (nothing rolls back there).
        """
        self._session.on_rollback(self.plugin_id, fn)

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network (a preview or scenario) and return its id."""
        return self._session.derive(net_id, label=label, note=f"{self.plugin_id}: {note}")

    def undo(self, net_id: str | None = None) -> dict[str, Any]:
        """Revert this plugin's newest mutation of a network, if it is the network's newest change.

        Raises ``ActionError`` when there is nothing to undo, or the newest change came from core, Python or
        another plugin. Returns the undone entry's view (``seq``, ``source``, ``note``, ``version``, ``rows``,
        ``saved``): its ``seq`` is the id :meth:`mutate` returned for it. Storage refusing the reverse is an
        ``ActionError`` (``"the undo failed: ..."``, scrubbed; 1.6). Inside an Action handler a failure of
        that Action restores it; elsewhere it is recorded as ``<plugin id>.revert``.
        """
        return self._session.undo_last(net_id, source=self.plugin_id).view()

    def edit_warnings(self, net_id: str | None = None) -> list[dict[str, Any]]:
        """The live edit warnings on a network now, from every source: show them before committing anything."""
        return [w.to_dict() for w in self._session.edit_check(net_id).warnings]

    def plan_update(self, net_id: str | None, table: str, ids: Sequence[Any], values: dict[str, Any]) -> EditPlan:
        """Core's plan for setting cells (raises ``EditRefused`` for what storage cannot hold).

        A plan reads the network as it is now and carries that version (``plan.version``): apply it with
        ``mutate(..., expected_version=plan.version)``, which refuses it if anything changed the network since.
        """
        h = self.network(net_id)
        version = h.version  # read first: an edit while planning makes the plan look stale, never current
        return plan_update(h.roadway, table, ids, values, version=version)

    def plan_delete(self, net_id: str | None, table: str, ids: Sequence[Any]) -> EditPlan:
        """Core's plan for deleting rows and every row that depends on them (``plan.rows`` says what goes)."""
        h = self.network(net_id)
        version = h.version
        return plan_delete(h.roadway, table, ids, version=version)

    def plan_add(self, net_id: str | None, table: str, rows: Sequence[dict[str, Any]]) -> EditPlan:
        """Core's plan for adding rows (each needs its key)."""
        h = self.network(net_id)
        version = h.version
        return plan_add(h.roadway, table, rows, version=version)

    def has_action(self, action_type: str) -> bool:
        """Whether any installed plugin (or core) registered ``action_type``: soft cross-plugin dependencies."""
        return self._session.actions.has(action_type)

    def dispatch(self, action: BaseAction | dict[str, Any]) -> Any:
        """Dispatch another Action (any plugin's or core's); it is recorded in history like any other.

        From inside a handler it is recorded as nested (``parent_seq``) and rolls back with the handler.
        A job Action (``open_network``, ``build_network``) can't be dispatched from a handler: it would
        deadlock on the session lock. Use :meth:`submit_job` for slow work. From a rollback callback
        (:meth:`on_rollback`) it raises ``RuntimeError``: history would record an Action the rollback hides.
        """
        return self._session.dispatch(action)

    def publish(self, name: str, payload: Any = None) -> None:
        """Send the browser a ``plugin`` event ``{plugin, name, payload}`` over the session's SSE stream.

        ``name`` matches ``[a-z][a-z0-9_]*``: in the browser ``wb.on("a.b")`` means plugin ``a``'s event ``b``, so a
        dotted name could never be listened for. Raises ``ValueError`` otherwise.

        Publishing is also how this plugin says its tables changed (1.8): each table it registered
        (:meth:`register_table`) is built again on its next read, and the browser gets a ``core.table_changed`` event
        (``{plugin, tables}``) so a table view showing one reloads it.
        """
        if not isinstance(name, str) or not _EVENT_NAME.fullmatch(name):
            raise ValueError(
                f"{self.plugin_id}: host.publish event names are lowercase letters, digits and underscores, "
                f"starting with a letter ([a-z][a-z0-9_]*); got {name!r}"
            )
        self._session.events.publish({"type": "plugin", "plugin": self.plugin_id, "name": name, "payload": payload})
        if tables := self._session.plugin_tables.changed(self.plugin_id):
            self._session.events.publish(
                {
                    "type": "plugin",
                    "plugin": "core",
                    "name": "table_changed",
                    "payload": {"plugin": self.plugin_id, "tables": tables},
                }
            )

    def register_table(
        self,
        name: str,
        title: str,
        rows_fn: Callable[[], Any],
        *,
        primary_key: str,
        columns: Sequence[str] | None = None,
    ) -> None:
        """Publish a read-only table to the Workbench's table view, listed under "Plugins" in its rail (1.8).

        ``rows_fn()`` returns the rows: an iterable of dicts (or a pandas frame), each with its ``primary_key``
        (unique, never null). Core builds them into a frame when the table is read and keeps it until this plugin
        publishes anything (:meth:`publish`) or the network the view is on changes, then calls ``rows_fn`` again;
        it is called outside the session lock, from a route, so it may call ``Host`` methods but must not block.
        The table is served by the table view's own routes (``/api/n/<net>/roadway/table/<name>/rows``, ``schema``,
        ``distinct``): sorting and the column filters work as on a network table (a comma-separated text column takes
        ``any_of`` / ``all_of`` / ``none_of``). It is never edited and never linked to the map; a row click in the
        browser emits ``core.table_row`` (``{plugin, table, id}``) for the plugin to act on. ``columns`` fixes the
        columns and their order (so an empty table still has them). ``name`` matches ``[a-z][a-z0-9_]*`` and is no
        network table's (``link``, ``node``, ...) nor another plugin's; registering it again replaces this plugin's
        own. Raises ``ValueError`` (``TypeError`` for a ``rows_fn`` that is not callable) otherwise. The table goes
        when the plugin is unloaded.
        """
        self._session.plugin_tables.register(
            self.plugin_id, name, title, rows_fn, primary_key=primary_key, columns=columns
        )

    def unregister_table(self, name: str) -> bool:
        """Take this plugin's table ``name`` out of the table view (1.8); ``False`` when it has none by that name."""
        return self._session.plugin_tables.unregister(self.plugin_id, name)

    def submit_job(self, label: str, fn: Callable[[JobContext], Any]) -> str:
        """Run ``fn(ctx)`` on a background job (progress and cancel in the jobs panel); return the job id."""
        return self._session.jobs.submit(f"{self.plugin_id}.job", label, fn).id

    def writable(self, path: str | Path) -> Path:
        """Resolve ``path`` for writing inside ``io.allowed_roots`` (raises ``PathNotAllowed`` outside it)."""
        return resolve_allowed(path, self._session.settings)

    def readable(self, path: str | Path) -> Path:
        """Resolve ``path`` for reading inside ``io.allowed_roots``: a file or a folder that exists (1.7).

        Raises ``PathNotAllowed`` outside the roots (before touching the disk) and ``ActionError`` when nothing is
        there, or when the disk refuses to say (a folder on the way that cannot be searched).
        """
        resolved = resolve_allowed(path, self._session.settings)
        try:
            exists = resolved.exists()
        except OSError as exc:
            raise ActionError(f"could not read {path}: {exc.strerror or exc}") from exc
        if not exists:
            raise ActionError(f"no such file or folder: {path}")
        return resolved

    def list_dir(self, path: str | Path) -> list[dict[str, Any]]:
        """The entries of a folder inside ``io.allowed_roots``, folders first, hidden entries skipped (1.7).

        Each is ``{name, path, kind, is_dir}``: ``kind`` is what core's file browser can open the entry as
        (``gmns``, ``zip``, ``json``, ...; ``None`` for a plain file or folder). The listing reads no file. Raises
        ``PathNotAllowed`` outside the roots and ``ActionError`` when the folder is missing or is a file.
        """
        try:
            listing = list_dir(str(path), self._session.settings)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ActionError(str(exc)) from exc
        return [{k: e[k] for k in ("name", "path", "kind", "is_dir")} for e in listing["entries"]]

    def history(self, prefix: str | None = None) -> list[dict[str, Any]]:
        """The session's history (``GET /api/history`` entries), oldest first, as JSON-safe copies (1.7).

        With ``prefix`` (say ``"my_plugin."``), only the entries whose Action type starts with it. Read-only: an
        Action now running is not in it yet (it is recorded once it returns).
        """
        entries = list(self._session.history)
        return [e.to_dict() for e in entries if prefix is None or str(e.action.get("type", "")).startswith(prefix)]
