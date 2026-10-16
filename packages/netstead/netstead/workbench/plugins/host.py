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

from ..paths import resolve_allowed

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
        """A copy of the shared selection payload (the same dict ``/api/state`` carries)."""
        # Not via ``state()``: that calls every plugin's ``state(host)``, which may read this property.
        return self._session.selection_copy()

    def network(self, net_id: str | None = None) -> NetworkHandle:
        """The handle for ``net_id`` (default: active). Read it; change it only via :meth:`mutate`."""
        return self._session.network(net_id)

    def mutate(self, net_id: str | None, edits: Sequence[Edit], *, note: str) -> list[EditResult]:
        """Apply corral edits all-or-nothing and return their results; lineage gets ``"<plugin id>: <note>"``.

        Inside an Action handler, a failing handler undoes it. Anywhere else (a job, a route) it is
        recorded as a non-replayable ``<plugin id>.mutate`` history entry.
        """
        return self._session.mutate(net_id, edits, note=note, origin=self.plugin_id)

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network (a preview or scenario) and return its id."""
        return self._session.derive(net_id, label=label, note=f"{self.plugin_id}: {note}")

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
