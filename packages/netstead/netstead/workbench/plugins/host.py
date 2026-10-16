"""``Host``: everything a plugin may touch in a Workbench session (the plugin API, :data:`HOST_API`).

A plugin never sees the :class:`~netstead.workbench.session.Session`; it gets one ``Host`` bound to
its own id. Handlers run under the session lock, so ``Host`` methods are safe to call from them;
``submit_job`` work runs on its own thread, and the ``mutate``/``derive`` it calls take the lock.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from ..editing import EditPlan, plan_add, plan_delete, plan_update
from ..paths import resolve_allowed
from ..selection import payload_facility_forms

if TYPE_CHECKING:
    from corral.editing import Edit, EditResult

    from ..actions import BaseAction
    from ..jobs import JobContext
    from ..registry import NetworkHandle
    from ..session import Session
    from .spec import WorkbenchPlugin

__all__ = ["Host", "PluginSettingsError", "validate_plugin_settings"]

#: A ``host.publish`` event name: no dot, since ``wb.on("a.b")`` in the browser means plugin ``a``'s event ``b``.
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*")


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
        ``query``), or ``None``: core owns the facility form, so a plugin never builds its own.
        """
        # Not via ``state()``: that calls every plugin's ``state(host)``, which may read this property.
        sel = self._session.selection_copy()
        if sel is not None:
            sel["projectcard"] = payload_facility_forms(sel)
        return sel

    def saved_selections(self) -> list[dict[str, Any]]:
        """A summary of each saved selection: ``name``, ``net_id``, ``status``, ``links``, ``utterance``, ``by``."""
        return self._session.saved_selections()

    def saved_selection(self, name: str) -> dict[str, Any] | None:
        """A copy of the selection saved as ``name``, plus its ``projectcard`` forms; ``None`` if there is none.

        It is the payload as saved; ``UseSelection`` resolves it again on the network as it is now.
        """
        sel = self._session.saved_selection(name)
        if sel is not None:
            sel["projectcard"] = payload_facility_forms(sel)
        return sel

    def network(self, net_id: str | None = None) -> NetworkHandle:
        """The handle for ``net_id`` (default: active). Read it; change it only via :meth:`mutate`."""
        return self._session.network(net_id)

    def mutate(
        self, net_id: str | None, edits: Sequence[Edit], *, note: str, expected_version: int | None = None
    ) -> list[EditResult]:
        """Apply corral edits all-or-nothing and return their results; lineage gets ``"<plugin id>: <note>"``.

        Inside an Action handler, a failing handler undoes it. Anywhere else (a job, a route) it is
        recorded as a non-replayable ``<plugin id>.mutate`` history entry. It becomes a pending edit whose
        source is this plugin: core's Edits panel lists it with the plugin's id, and core's Undo never
        reverses it (use :meth:`undo`). ``expected_version`` (a plan's ``version``) refuses the edits with
        ``ActionError`` if the network changed since: plan again.
        """
        return self._session.mutate(net_id, edits, note=note, origin=self.plugin_id, expected_version=expected_version)

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network (a preview or scenario) and return its id."""
        return self._session.derive(net_id, label=label, note=f"{self.plugin_id}: {note}")

    def undo(self, net_id: str | None = None) -> dict[str, Any]:
        """Revert this plugin's newest mutation of a network, if it is the network's newest change.

        Raises ``ActionError`` when there is nothing to undo, or the newest change came from core, Python or
        another plugin. Returns the undone entry's view (``seq``, ``source``, ``note``, ``version``, ``rows``,
        ``saved``). Inside an Action handler a failure of that Action restores it; elsewhere it is recorded
        as ``<plugin id>.revert``.
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
        deadlock on the session lock. Use :meth:`submit_job` for slow work.
        """
        return self._session.dispatch(action)

    def publish(self, name: str, payload: Any = None) -> None:
        """Send the browser a ``plugin`` event ``{plugin, name, payload}`` over the session's SSE stream.

        ``name`` matches ``[a-z][a-z0-9_]*``: in the browser ``wb.on("a.b")`` means plugin ``a``'s event ``b``, so a
        dotted name could never be listened for. Raises ``ValueError`` otherwise.
        """
        if not isinstance(name, str) or not _EVENT_NAME.fullmatch(name):
            raise ValueError(
                f"{self.plugin_id}: host.publish event names are lowercase letters, digits and underscores, "
                f"starting with a letter ([a-z][a-z0-9_]*); got {name!r}"
            )
        self._session.events.publish({"type": "plugin", "plugin": self.plugin_id, "name": name, "payload": payload})

    def submit_job(self, label: str, fn: Callable[[JobContext], Any]) -> str:
        """Run ``fn(ctx)`` on a background job (progress and cancel in the jobs panel); return the job id."""
        return self._session.jobs.submit(f"{self.plugin_id}.job", label, fn).id

    def writable(self, path: str | Path) -> Path:
        """Resolve ``path`` for writing inside ``io.allowed_roots`` (raises ``PathNotAllowed`` outside it)."""
        return resolve_allowed(path, self._session.settings)
