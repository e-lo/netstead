"""Workbench session: the one place state changes, via :meth:`Session.dispatch`.

UI clicks (``POST /api/actions``), Python (``session.dispatch(...)``), and, in
P3, the NL assistant all funnel through ``dispatch``. Each call is recorded as a
:class:`HistoryEntry` carrying its ``to_python`` replay snippet, and publishes
``history`` + ``state`` events for the browser. Network *edits* are not actions
here yet: in P2 they become ProjectCard-shaped ``NetworkChange`` objects.

Actions marked ``runs_as_job`` (``OpenNetwork``, ``BuildNetwork``) do their slow
work on a background job thread *without* the session lock. :meth:`Session.submit`
starts one and returns immediately (the HTTP path); :meth:`Session.dispatch` starts
one and waits (Python, the CLI), so callers see the same blocking behaviour as before.

A successful job ends in :meth:`Session._commit`, which registers the loaded network
*and* records its history entry in one critical section. History order therefore
always matches registry order, even when two jobs overlap, so replaying the history
reproduces the same network ids. Failed and cancelled jobs are recorded by
:meth:`Session._finish`.

``Select`` with an utterance may call a language model, which can take seconds. Its parse
(:meth:`Session._prepare_select`) runs *before* the lock is taken; the handler then applies the
outcome and records it under the lock, failing if the network was closed or changed meanwhile.
Provider failures (keys, quotas, timeouts) are recorded :class:`ActionError` results, never a
silent fallback to another provider.
"""

from __future__ import annotations

import copy
import functools
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from corral.editing import Edit, EditResult
from corral.editing.apply import apply_edit
from corral.engines.ibis_engine import IbisEngine

from netstead import Network
from netstead.config import (
    LoadedSettings,
    Settings,
    SettingsError,
    get_value,
    is_local_url,
    load_settings,
    save_setting,
)
from netstead.llm import LLMError, MissingKey, ProviderRegistry, build_registry
from netstead.llm.context import assistant_context, find_project_context, read_capped
from netstead.llm.secrets import redact_settings
from netstead.select.intent import SelectionIntent
from netstead.select.parse import LLMParser, make_parser, payload_from_intent
from netstead.select.prompt import PromptContext, close_match_hint, vocabulary_from_links
from netstead.select.resolve import resolve_frames
from netstead.viz.styling import styleable_columns

from . import build
from .actions import (
    ActionRegistry,
    BaseAction,
    BuildNetwork,
    ClearSelection,
    CloseNetwork,
    Navigate,
    OpenNetwork,
    Select,
    SetActiveNetwork,
    SetSetting,
    Style,
    import_line,
    is_secret_name,
    to_python,
)
from .errors import ActionError, ApprovalRequired, JobCancelled, NotSupportedYet, PathNotAllowed
from .estimate import Estimate, needs_approval
from .events import EventBus
from .jobs import Job, JobContext, JobRunner
from .paths import allowed_roots, open_locator
from .plugins.discovery import PluginStatus, discover
from .plugins.host import Host, PluginSettingsError, validate_plugin_settings
from .plugins.spec import HOST_API, WorkbenchPlugin, api_compatible, problems
from .redact import describe_error, scrub
from .registry import NetworkHandle, NetworkRegistry, as_pandas, derived_copy
from .selection import selection_payload, unparsed_payload

__all__ = [
    "DEFAULT_STYLE",
    "ActionError",
    "ApprovalRequired",
    "HistoryEntry",
    "NotSupportedYet",
    "Session",
    "refusal_reason",
]

logger = logging.getLogger(__name__)

DEFAULT_STYLE: dict[str, Any] = {
    "color_by": "none",
    "ramp": "YlOrRd",
    "offset": True,
    "show_direction": False,
    "show_legend": True,
    "show": {"links": True, "nodes": True, "labels": True, "selection": True},
    "colors": {"links": [46, 64, 110], "nodes": [70, 90, 120], "selection": [255, 140, 59]},
}


@dataclass
class _Loaded:
    """A job's loaded network, waiting for :meth:`Session._commit` to register it."""

    net: Network
    frames: dict[str, Any]
    source: str
    label: str | None
    net_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # merged into the job result after ``net_id``


@dataclass
class HistoryEntry:
    """One dispatched action, successful or not."""

    seq: int
    action: dict[str, Any]
    python: str
    imports: str  # the ``from ... import ...`` line ``python`` needs (a plugin's Action lives in its own package)
    ok: bool
    error: str | None
    error_type: str | None
    result: Any
    ts: float
    #: Set on an Action dispatched from inside another Action's handler (``host.dispatch``): the ``seq``
    #: of the Action that dispatched it. Replaying the parent repeats it, so scripts show it as a comment.
    parent_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy."""
        return asdict(self)


@dataclass
class _Nested:
    """An Action dispatched inside another, recorded after the outermost one finishes."""

    entry: HistoryEntry
    children: list[_Nested]


@dataclass
class _Running:
    """An Action whose handler is running (innermost last in ``Session._running``)."""

    action: BaseAction
    undo_mark: int  # ``len(Session._undo)`` when it started: a failure undoes everything after it
    view: tuple[str | None, dict[str, Any] | None, dict[str, Any]]  # active, selection, style before it ran
    children: list[_Nested] = field(default_factory=list)


@dataclass
class _Mutated:
    """Undo record for one :meth:`Session.mutate` inside an Action."""

    handle: NetworkHandle
    tables: dict[str, tuple[Any, bool]]  # name -> (expr, dirty) before
    stamps: dict[str, Any]  # name -> its sync stamp before (``None``: unstamped)
    version: int
    lineage: int  # lineage length before

    def undo(self, session: Session) -> None:
        self.handle.restore(self.tables, self.version)
        del self.handle.lineage[self.lineage :]
        _restore_stamps(self.handle, self.stamps)


@dataclass
class _Derived:
    """Undo record for one :meth:`Session.derive` inside an Action."""

    net_id: str

    def undo(self, session: Session) -> None:
        if self.net_id in session.registry.ids():
            session.registry.remove(self.net_id)


class Session:
    """Live workbench state: open networks, selection, style, settings, history."""

    def __init__(
        self,
        *,
        project_dir: str | Path | None = None,
        overrides: Mapping[str, Any] | None = None,
        parser: Any = None,
        environ: Mapping[str, str] | None = None,
        http: Any = None,
        llm_transport: Any = None,
        keyring: Any = "auto",
        plugins: Iterable[WorkbenchPlugin] | None = None,
    ) -> None:
        """Load settings (raises :class:`~netstead.config.SettingsError` on bad config) and start empty.

        ``http`` is the HTTP session for Overpass/Nominatim (anything with ``get``/``post`` like
        :mod:`requests`); ``None`` means ``requests`` itself. Tests inject a fake. ``llm_transport``
        (an ``httpx`` transport) and ``keyring`` (``"auto"``, ``None`` or a keyring-like object) do
        the same for the LLM providers; the defaults use the network and the OS keyring.
        ``plugins``: plugins to install (default ``None``: discover the installed
        ``netstead.workbench.plugins`` entry points). A plugin that can't install is recorded in
        :attr:`plugin_status`, never raised.
        """
        self.project_dir = project_dir
        self._environ = environ
        self._overrides: dict[str, Any] = dict(overrides or {})
        self.loaded: LoadedSettings = load_settings(project_dir=project_dir, overrides=self._overrides, environ=environ)
        self._llm_transport = llm_transport
        self._keyring = keyring
        self.llm: ProviderRegistry = self._build_llm()
        #: Resolved ``(net_id, utterance, tool arguments)``: few-shot examples (same network only) when
        #: ``llm.quality.few_shot`` is on. Session-only by design: never persisted, so one project's
        #: requests never reach another.
        self._examples: deque[tuple[str, str, dict[str, Any]]] = deque(maxlen=_MAX_EXAMPLES)
        #: ``(registry, monotonic time, MissingKey)``: a recent "no key" for the current registry, so a
        #: missing key doesn't re-read the OS keyring on every Select (see :data:`_MISSING_KEY_TTL_S`).
        self._parser_failure: tuple[ProviderRegistry, float, MissingKey] | None = None
        self.registry = NetworkRegistry()
        self.events = EventBus()
        self.active: str | None = None
        self.selection: dict[str, Any] | None = None
        self.style: dict[str, Any] = copy.deepcopy(DEFAULT_STYLE)
        self.history: list[HistoryEntry] = []
        self._injected_parser = parser
        self._parser = parser
        #: The registry ``_parser`` was built from (an injected parser is paired with the current one):
        #: its decisions about what to send must be the ones for the endpoint the parser calls.
        self._parser_registry: ProviderRegistry | None = self.llm if parser is not None else None
        self.http = http
        self.jobs = JobRunner(lambda event: self.events.publish(event))  # late-bound, like every other publish
        self._lock = threading.RLock()
        self._committed: dict[str, int] = {}  # job id -> history seq its ``_commit`` recorded
        #: Actions whose handlers are running (more than one: nested ``host.dispatch``). Only the thread
        #: holding the lock touches it, so "empty" means "not inside any Action" for that thread.
        self._running: list[_Running] = []
        #: What ``mutate``/``derive`` changed inside the running Actions, undone if one of them fails.
        self._undo: list[_Mutated | _Derived] = []
        #: The Actions this session understands: the core ones, plus any its plugins register.
        self.actions = ActionRegistry()
        #: ``type`` -> handler run under the lock. Core handlers are this class's ``_do_<type>`` methods
        #: (job actions use ``_job_<type>``); plugins add theirs when installed.
        self._handlers: dict[str, Callable[..., Any]] = {
            t: self._core_handler(t) for t in self.actions.types() if not self.actions.model(t).runs_as_job
        }
        #: Installed plugins by id, and the :class:`Host` each one was given.
        self.plugins: dict[str, WorkbenchPlugin] = {}
        self._hosts: dict[str, Host] = {}
        #: Each installed plugin's ``(router, static_dir)``, built and checked at install (see :meth:`plugin_mounts`).
        self._plugin_mounts: dict[str, tuple[Any, Path | None]] = {}
        #: One status per plugin seen at startup: loaded, disabled, incompatible or error (with the reason).
        self.plugin_status: list[PluginStatus] = []
        self._install_plugins(plugins)

    # ------------------------------------------------------------------ public API

    @property
    def settings(self) -> Settings:
        """The currently resolved settings."""
        return self.loaded.settings

    def parser(self) -> Any:
        """The NL parser for ``select.provider``/``select.model`` (built once and cached; an injected parser wins).

        The cache is dropped by :meth:`reset_llm`, which every ``select.*``/``llm.*`` setting change
        (and every key write through the Workbench) calls. Raises :class:`~netstead.llm.errors.MissingKey`
        when the chosen provider has no key; that answer is reused for :data:`_MISSING_KEY_TTL_S`
        seconds, so a key written outside this session (the ``netstead llm`` CLI, another process) may
        go unseen for that long. The Workbench's key routes call :meth:`reset_llm`, so the UI never waits.

        Building reads the API key, which may block on the OS keyring (an unlock prompt), so it
        happens *outside* the session lock; the lock only guards the snapshot and the store.
        """
        return self._parser_snapshot()[0]

    def _parser_snapshot(self) -> tuple[Any, ProviderRegistry]:
        """``(parser, the registry it was built from)``: see :meth:`parser`.

        Callers that decide what to send (grounding, notes) must use *this* registry, not
        ``self.llm`` read later: a setting change in between could pair a remote parser with a
        registry that thinks the endpoint is local.
        """
        with self._lock:
            if self._parser is not None:
                return self._parser, self._parser_registry or self.llm
            registry, select = self.llm, self.settings.select
            failure = self._parser_failure
            if failure is not None and failure[0] is registry and time.monotonic() - failure[1] < _MISSING_KEY_TTL_S:
                cached = failure[2]
                raise MissingKey(cached.provider, str(cached))  # fresh: re-raising one instance grows its traceback
        try:
            built = make_parser(select, registry)
        except MissingKey as exc:
            with self._lock:
                if self.llm is registry:
                    self._parser_failure = (registry, time.monotonic(), exc)
            raise
        with self._lock:
            if self._parser is None and self.llm is registry:
                self._parser, self._parser_registry = built, registry  # nobody reset or built one: cache ours
            if self._parser is not None:
                return self._parser, self._parser_registry or self.llm
            return built, registry

    def reset_llm(self) -> None:
        """Rebuild the provider registry and drop the cached parser (after a setting, key or endpoint change).

        A selection already parsing keeps the parser it started with and records that one as ``parsed_by``.
        """
        with self._lock:
            self.llm = self._build_llm()
            self._parser_failure = None
            if self._injected_parser is None:
                self._parser, self._parser_registry = None, None
            else:
                self._parser_registry = self.llm

    def _build_llm(self) -> ProviderRegistry:
        return build_registry(
            self.settings, environ=self._environ, keyring=self._keyring, transport=self._llm_transport
        )

    def _install_plugins(self, plugins: Iterable[WorkbenchPlugin] | None) -> None:
        """Install ``plugins`` (default: discover installed entry points); record a status for each."""
        disabled = set(self.settings.app.disabled_plugins)
        if plugins is None:
            plugins, self.plugin_status = discover(disabled)
        for plugin in plugins:
            self.plugin_status.append(self._install(plugin, disabled))

    def _install(self, plugin: WorkbenchPlugin, disabled: set[str]) -> PluginStatus:
        """Check ``plugin``, run its ``on_load``, build its router, then register its Actions (all or none).

        Never raises. A router factory that fails (or returns something other than an ``APIRouter``)
        or a ``static_dir`` that isn't a directory is an install failure like any other: no Actions,
        no state, status ``error``.
        """
        frontend = f"/plugins/{plugin.id}/{plugin.frontend}" if plugin.static_dir is not None else None
        status = PluginStatus(plugin.id, plugin.name, plugin.version, plugin.requires_api, "loaded", frontend=frontend)
        if plugin.id in disabled:
            return replace(status, state="disabled", frontend=None)
        if not api_compatible(plugin.requires_api):
            reason = f"needs plugin API {plugin.requires_api}; this netstead provides {HOST_API}"
            return replace(status, state="incompatible", error=reason, frontend=None)
        errors = problems(plugin, self.actions, self.plugins)
        host = Host(self, plugin)
        if not errors:
            try:
                host.settings  # noqa: B018  (validates [plugins.<id>] now, so a bad file is reported at startup)
                if plugin.on_load is not None:
                    plugin.on_load(host)
            except PluginSettingsError as exc:
                errors.append(str(exc))
            # boundary: third-party code must not stop the session, not even with ``sys.exit``
            # (KeyboardInterrupt still propagates: the user asked to stop).
            except (Exception, SystemExit) as exc:
                logger.exception("workbench plugin %r failed to load", plugin.id)
                errors.append(describe_error(exc))
        mount: tuple[Any, Path | None] = (None, None)
        if not errors:
            try:
                mount = _build_mount(plugin, host)
            except (Exception, SystemExit) as exc:  # boundary: third-party code, as for ``on_load``
                logger.exception("workbench plugin %r failed to build its routes", plugin.id)
                errors.append(describe_error(exc))
        if not errors:
            errors = self._register_actions(plugin, host)
        if errors:
            return replace(status, state="error", error="; ".join(errors), frontend=None)
        self.plugins[plugin.id] = plugin
        self._hosts[plugin.id] = host
        self._plugin_mounts[plugin.id] = mount
        return status

    def plugin_mounts(self) -> dict[str, tuple[Any, Path | None]]:
        """``plugin id -> (APIRouter or None, static dir or None)`` for every installed plugin, to attach to the app."""
        with self._lock:
            return dict(self._plugin_mounts)

    def unload_plugin(self, plugin_id: str, error: str) -> None:
        """Take an installed plugin out (Actions, state, mounts) and mark it ``error``: its mount failed to attach."""
        with self._lock:
            plugin = self.plugins.pop(plugin_id)
            self._hosts.pop(plugin_id, None)
            self._plugin_mounts.pop(plugin_id, None)
            for spec in plugin.actions:
                action_type = spec.model.action_type()
                if action_type is not None and self.actions.has(action_type):
                    self.actions.unregister(action_type)
                    self._handlers.pop(action_type, None)
            failed = {"state": "error", "error": error, "frontend": None}
            self.plugin_status = [
                replace(s, **failed) if s.id == plugin_id and s.state == "loaded" else s for s in self.plugin_status
            ]

    def _register_actions(self, plugin: WorkbenchPlugin, host: Host) -> list[str]:
        """Register every one of ``plugin``'s Actions and handlers, or (on any failure) none; return the errors."""
        registered: list[str] = []
        try:
            for spec in plugin.actions:
                action_type = spec.model.action_type()
                self.actions.register(spec.model)
                registered.append(action_type)  # type: ignore[arg-type]  # register() refused a None type
                self._handlers[action_type] = functools.partial(spec.handler, host)  # type: ignore[index]
        except (TypeError, ValueError) as exc:  # ``problems`` should have caught it; never half-install
            for action_type in registered:
                self.actions.unregister(action_type)
                self._handlers.pop(action_type, None)
            return [str(exc)]
        return []

    def _plugin_state(self) -> dict[str, Any]:
        """Each plugin's contributed state; a failing ``state()`` is logged and shown, never raised."""
        out: dict[str, Any] = {}
        for plugin_id, plugin in self.plugins.items():
            if plugin.state is None:
                continue
            try:
                out[plugin_id] = copy.deepcopy(plugin.state(self._hosts[plugin_id]))
            except Exception as exc:  # boundary: third-party code
                logger.exception("workbench plugin %r state() failed", plugin_id)
                out[plugin_id] = {"error": describe_error(exc)}
        return out

    def _check_plugin_settings(self, settings: Settings) -> None:
        """Raise :class:`ActionError` if ``settings`` breaks any installed plugin's settings model."""
        for plugin_id, plugin in self.plugins.items():
            if plugin.settings_model is not None:
                try:
                    validate_plugin_settings(plugin.settings_model, settings.plugins.get(plugin_id, {}), plugin_id)
                except PluginSettingsError as exc:
                    raise ActionError(str(exc)) from None

    def dispatch(self, action: BaseAction | dict[str, Any]) -> Any:
        """Apply ``action`` (waiting for a job action to finish) and return its result.

        Raises the recorded failure's type: :class:`~netstead.workbench.errors.ApprovalRequired` (with
        ``.estimate``), :class:`NotSupportedYet`, :class:`~netstead.workbench.errors.PathNotAllowed`,
        :class:`~netstead.workbench.errors.JobCancelled`, else :class:`ActionError`.
        """
        entry = self.dispatch_recorded(action)
        if entry.ok:
            return entry.result
        if entry.error_type == "ApprovalRequired":
            raise ApprovalRequired(Estimate(**entry.result["estimate"]), entry.result["threshold_s"])
        raise _ERROR_TYPES.get(entry.error_type or "", ActionError)(entry.error)

    do = dispatch

    def dispatch_recorded(self, action: BaseAction | dict[str, Any]) -> HistoryEntry:
        """Apply ``action``, record and publish it, and return the entry (never raises ``ActionError``).

        A ``runs_as_job`` action is submitted as a background job and this call waits for it.
        """
        action = self._coerce(action)
        if action.runs_as_job:
            # The job thread needs the lock (``_commit``/``_finish``) and we wait for it below, so a
            # caller already holding the lock (e.g. a handler) would deadlock: fail loudly instead.
            if self._lock._is_owned():  # type: ignore[attr-defined]  # RLock's owner check
                raise RuntimeError(
                    f"{action.type} runs as a job, so it can't be dispatched from inside an Action handler "
                    "(it would deadlock waiting for the session lock); a plugin does slow work with "
                    "host.submit_job instead"
                )
            job = self.submit(action)
            # Wait OUTSIDE the session lock: the job thread takes it in ``_commit`` and ``_finish``,
            # so waiting while holding it would deadlock. Never call this with the lock held.
            job.wait()
            if job.history_seq is None:  # on_finish itself failed (already logged by the runner)
                raise RuntimeError(f"job {job.id} finished without a history entry")
            return self.history[job.history_seq - 1]
        handler = self._handlers[action.type]
        # An optional ``_prepare_<type>`` does the slow, read-only part (an LLM call) WITHOUT the lock,
        # so other actions and state reads proceed meanwhile; the handler then applies its result and
        # the entry is recorded in one critical section, as before. Its failure is recorded like the
        # handler's (re-raised inside the ``try`` below so it is classified the same way).
        prepare = getattr(self, f"_prepare_{action.type}", None)
        prepared: Any = None
        failure: Exception | None = None
        if prepare is not None:
            try:
                prepared = prepare(action)
            except Exception as exc:  # boundary: recorded below, under the lock
                failure = exc
        with self._lock:
            running = _Running(action, len(self._undo), (self.active, self.selection, dict(self.style)))
            self._running.append(running)
            try:
                if failure is not None:
                    raise failure
                result = handler(action, prepared) if prepare is not None else handler(action)
                ok, error, error_type = True, None, None
            except ActionError as exc:  # includes NotSupportedYet
                result, ok, error, error_type = exc.payload, False, str(exc), type(exc).__name__
            except Exception as exc:  # boundary: an unexpected handler failure is still a recorded, user-facing error
                logger.exception("workbench action %s failed", action.type)
                if self._is_plugin_action(action.type):
                    # A plugin's exception message (or a pydantic input value) must not reach the
                    # browser verbatim: it can carry secrets the plugin was handling.
                    error = f"internal error: {describe_error(exc)}"
                else:
                    error = f"internal error: {type(exc).__name__}: {exc}"
                result, ok, error_type = None, False, "InternalError"
            except BaseException:  # SystemExit, KeyboardInterrupt: propagate, but never half-applied
                self._running.pop()
                self._roll_back(running)
                raise
            self._running.pop()
            changed = len(self._undo) > running.undo_mark or bool(running.children)
            if not ok:
                self._roll_back(running)
            elif not self._running:
                self._undo.clear()  # the outermost Action succeeded: nothing left to undo
            entry = self._entry(action, ok=ok, result=result, error=error, error_type=error_type)
            if self._running:  # nested: recorded when the outermost Action is, pointing at its parent
                self._running[-1].children.append(_Nested(entry, running.children))
                return entry
            self._record_tree(entry, running.children, state=ok or changed)
            return entry

    def _roll_back(self, running: _Running) -> None:
        """Undo what ``running`` (and every Action nested in it) changed; mark its nested entries rolled back.

        Restores the tables, version, lineage and sync stamps of each mutated network, unregisters
        the networks it derived, and puts back the active network, selection and style. A plugin's
        own state (its closure, files it wrote) is the plugin's to keep consistent.
        """
        while len(self._undo) > running.undo_mark:
            self._undo.pop().undo(self)
        active, selection, style = running.view
        ids = self.registry.ids()
        self.active = active if active in ids else (ids[0] if ids else None)
        self.selection = selection if selection is None or selection.get("net_id") in ids else None
        self.style = style
        _mark_rolled_back(running.children, running.action.type)

    def _is_plugin_action(self, action_type: str) -> bool:
        """Whether ``action_type`` belongs to an installed plugin (``"<plugin id>.<name>"``), not core."""
        plugin_id, _, rest = action_type.partition(".")
        return bool(rest) and plugin_id in self.plugins

    def _core_handler(self, action_type: str) -> Callable[..., Any]:
        """The handler-table entry for a core Action: calls ``_do_<type>``, looked up at call time.

        Late-bound so an instance-level override of the method (a test's ``monkeypatch``) still takes effect.
        """
        name = f"_do_{action_type}"
        return lambda *args: getattr(self, name)(*args)

    def _coerce(self, action: BaseAction | dict[str, Any]) -> BaseAction:
        """Parse a dict with this session's registry; refuse an instance of a class it doesn't know."""
        if isinstance(action, dict):
            return self.actions.parse(action)
        if not self.actions.has(action.type) or self.actions.model(action.type) is not type(action):
            raise ValueError(f"action type {action.type!r} is not registered in this session")
        return action

    def submit(self, action: BaseAction | dict[str, Any]) -> Job:
        """Start a ``runs_as_job`` action on a background job and return the :class:`Job` at once.

        The history entry is recorded when the job finishes (so ``seq`` follows completion order).
        """
        action = self._coerce(action)
        if not action.runs_as_job:
            raise ValueError(f"{action.type} is not a job action; use dispatch()")
        run = getattr(self, f"_job_{action.type}")
        return self.jobs.submit(
            action.type,
            action.job_label(),
            lambda ctx: self._commit(action, ctx, run(action, ctx)),
            on_finish=lambda job: self._finish(action, job),
        )

    def _commit(self, action: BaseAction, ctx: JobContext, loaded: _Loaded) -> dict[str, Any]:
        """Register ``loaded`` and record the success entry in ONE critical section; return the job result.

        Doing both under one lock acquisition is what keeps history order equal to registry order:
        with separate acquisitions two overlapping jobs could register A, B but record B, A, and a
        replay would then hand out swapped ids. The caller's last ``ctx.stage`` was the final
        cancellation checkpoint, so nothing can cancel the job once this runs.
        """
        with self._lock:
            handle = self.registry.add(loaded.net, source=loaded.source, label=loaded.label, net_id=loaded.net_id)
            handle.prime(**loaded.frames)
            self.active = handle.id
            result = {"net_id": handle.id, **loaded.extra}
            entry = self._record(action, ok=True, result=result, error=None, error_type=None)
            self._committed[ctx.job_id] = entry.seq
        return result

    def _finish(self, action: BaseAction, job: Job) -> None:
        """Job callback: link the committed entry to the job, or record a failure/cancellation entry."""
        with self._lock:
            seq = self._committed.pop(job.id, None)
            if seq is None:
                ok = job.status == "done"  # only a job whose commit never ran gets here: normally a failure
                seq = self._record(
                    action,
                    ok=ok,
                    result=job.result if ok else job.payload,
                    error=job.error,
                    error_type=job.error_type,
                ).seq
        self.jobs.update(job, history_seq=seq)

    def _record(
        self, action: BaseAction, *, ok: bool, result: Any, error: str | None, error_type: str | None
    ) -> HistoryEntry:
        """Append and publish a history entry (then a ``state`` event on success). Call with the lock held."""
        entry = self._entry(action, ok=ok, result=result, error=error, error_type=error_type)
        entry.seq = len(self.history) + 1
        self._append(entry, state=ok)
        return entry

    @staticmethod
    def _entry(action: BaseAction, *, ok: bool, result: Any, error: str | None, error_type: str | None) -> HistoryEntry:
        """A history entry for ``action``, not yet numbered or appended."""
        return HistoryEntry(
            seq=0,
            action=action.model_dump(mode="json"),
            python=to_python(action),
            imports=import_line(action),
            ok=ok,
            error=error,
            error_type=error_type,
            result=copy.deepcopy(result),
            ts=time.time(),
        )

    def _record_tree(self, entry: HistoryEntry, children: list[_Nested], *, state: bool) -> None:
        """Record an outermost Action after the Actions nested in it (innermost first), linked by ``parent_seq``."""
        ordered: list[HistoryEntry] = []

        def walk(node: HistoryEntry, kids: list[_Nested]) -> None:
            for kid in kids:
                walk(kid.entry, kid.children)
            ordered.append(node)

        walk(entry, children)
        for seq, item in enumerate(ordered, start=len(self.history) + 1):
            item.seq = seq

        def link(node: HistoryEntry, kids: list[_Nested]) -> None:
            for kid in kids:
                kid.entry.parent_seq = node.seq
                link(kid.entry, kid.children)

        link(entry, children)
        for item in ordered:
            self._append(item, state=state and item is entry)

    def _append(self, entry: HistoryEntry, *, state: bool) -> None:
        """Append a numbered entry and publish it (then a ``state`` event if asked). Call with the lock held.

        Publishing is best-effort: once the entry is appended the action *happened*, so a failing
        publish (or ``state()``) is logged, never raised. Raising here would make a job's
        ``_finish`` record the same action a second time.
        """
        self.history.append(entry)
        try:
            self.events.publish({"type": "history", "entry": entry.to_dict()})
            if state:
                self.events.publish({"type": "state", "state": self.state()})
        except Exception:  # boundary: the browser misses one update; the history stays correct
            logger.exception("publishing history entry %d failed", entry.seq)

    def add_network(
        self, net: Network, *, source: str = "<python>", label: str | None = None, net_id: str | None = None
    ) -> NetworkHandle:
        """Register an already-loaded ``Network`` (the Python path; not an Action because it isn't JSON)."""
        with self._lock:
            handle = self.registry.add(net, source=source, label=label, net_id=net_id)
            self.active = self.active or handle.id
            self.events.publish({"type": "state", "state": self.state()})
        return handle

    def mutate(
        self, net_id: str | None, edits: Sequence[Edit], *, note: str, origin: str | None = None
    ) -> list[EditResult]:
        """Apply corral ``edits`` to a network's roadway, in order and all-or-nothing; return their results.

        The edits run on a copy-on-write draft of the roadway, so the network itself never holds a
        half-applied state: a failing edit discards the draft and re-raises, leaving the tables, their
        sync stamps and the caches as they were. On success the changed tables are swapped in at once,
        the ``version`` is bumped (dropping caches), ``note`` is appended to the lineage, and ``state``
        is published. Inside an Action, the change is undone if that Action (or one it is nested in)
        fails. Outside any Action (a job, a plugin route, Python) nothing would record it, so it gets a
        history entry of its own, ``<origin>.mutate``, whose ``python`` is a comment: it is visible but
        not replayable. ``origin`` (a plugin id; default ``"python"``) also prefixes the lineage note.
        The returned :class:`~corral.editing.EditResult` objects carry the rollback data
        :func:`corral.editing.apply.reverse_edit` needs, so a caller can undo them later.
        """
        with self._lock:
            handle = self._handle(net_id)
            roadway = handle.roadway
            draft = derived_copy(roadway)  # shares the immutable table expressions; copies the wrappers
            applied = [apply_edit(draft, edit) for edit in edits]
            changed = {name: (draft.tables[name].expr, draft.tables[name].dirty) for name in {e.table for e in edits}}
            if self._running:
                tracker = roadway.dirty_tracker
                self._undo.append(
                    _Mutated(
                        handle,
                        tables={name: (roadway.tables[name].expr, roadway.tables[name].dirty) for name in changed},
                        stamps={name: tracker.get_table_stamp(name) for name in changed} if tracker else {},
                        version=handle.version,
                        lineage=len(handle.lineage),
                    )
                )
            handle.replace_tables(changed)
            handle.lineage.append(f"{origin}: {note}" if origin else note)
            if self._running:
                self.events.publish({"type": "state", "state": self.state()})
            else:
                self._record_outside_action(handle.id, note, origin or "python")
        return applied

    def _record_outside_action(self, net_id: str, note: str, origin: str) -> None:
        """Record a ``mutate`` no Action covers, so nothing changes silently (not replayable: a comment)."""
        text = " ".join(note.split())  # one line: it becomes a comment in a script
        entry = HistoryEntry(
            seq=len(self.history) + 1,
            action={"type": f"{origin}.mutate", "net_id": net_id, "note": text},
            python=f"# {origin} changed {net_id} outside an Action: {text} (not replayable)",
            imports="",
            ok=True,
            error=None,
            error_type=None,
            result=None,
            ts=time.time(),
        )
        self._append(entry, state=True)

    def derive(self, net_id: str | None, *, label: str, note: str) -> str:
        """Register a copy-on-write copy of a network as a new one (a preview, a scenario); return its id.

        The base is never touched by edits to the copy. The copy records ``derived_from`` and inherits
        the base's lineage plus ``note``. The active network does not change.
        """
        with self._lock:
            base = self._handle(net_id)
            handle = self.registry.add(derived_copy(base.roadway), source=base.source, label=label)
            handle.derived_from = base.id
            handle.lineage = [*base.lineage, note]
            if self._running:
                self._undo.append(_Derived(handle.id))
            self.events.publish({"type": "state", "state": self.state()})
        return handle.id

    def network(self, net_id: str | None = None) -> NetworkHandle:
        """The handle for ``net_id`` (default: the active network); :class:`ActionError` if there is none."""
        with self._lock:
            return self._handle(net_id)

    def selection_copy(self) -> dict[str, Any] | None:
        """A deep copy of the shared selection payload (``None`` when nothing is selected)."""
        with self._lock:
            return copy.deepcopy(self.selection)

    def state(self) -> dict[str, Any]:
        """JSON-safe snapshot pushed to the browser."""
        with self._lock:
            return {
                "networks": [h.summary() for h in self.registry],
                "active": self.active,
                "selection": copy.deepcopy(self.selection),
                "style": copy.deepcopy(self.style),
                "plugins": self._plugin_state(),
            }

    def action_catalog(self) -> list[dict[str, Any]]:
        """Each registered Action, for the browser's generated forms and ``wb.hasAction``.

        Each entry has ``type``, class ``name``, ``description`` (its docstring's first line), owning
        ``plugin`` (``None`` for core), ``mutates`` and its JSON ``schema``. A schema that can't be generated
        (install refuses such a plugin; this is defence in depth) is logged and listed as ``None``: one bad
        model must not fail the catalog for every caller.
        """
        with self._lock:
            owner = {spec.model.action_type(): pid for pid, plugin in self.plugins.items() for spec in plugin.actions}
            models = [(t, self.actions.model(t)) for t in self.actions.types()]
        catalog = []
        for action_type, model in models:
            doc = (model.__doc__ or "").strip().splitlines()
            catalog.append(
                {
                    "type": action_type,
                    "name": model.__name__,
                    "description": doc[0] if doc else "",
                    "plugin": owner.get(action_type),
                    "mutates": model.mutates,
                    "schema": _json_schema_or_none(model),
                }
            )
        return catalog

    def settings_payload(self) -> dict[str, Any]:
        """Settings values, per-key sources, JSON schema, and file paths, for the Settings UI.

        ``readonly`` maps each key the action API refuses to the reason shown beside it; ``restart``
        lists keys read only at launch; ``notes`` explains sections nothing reads yet.
        """
        sources = dict(self.loaded.sources)
        values = self.settings.model_dump(mode="json")
        values["plugins"] = redact_settings(values["plugins"])  # validated secret-free; belt and braces
        return {
            "values": values,
            "sources": sources,
            "schema": Settings.model_json_schema(),
            "paths": {"user": str(self.loaded.user_path), "project": str(self.loaded.project_path)},
            "readonly": {k: reason for k in sources if (reason := refusal_reason(k))},
            "restart": list(_RESTART_KEYS),
            "notes": dict(_SECTION_NOTES),
        }

    # ------------------------------------------------------------------ handlers (run under the lock)

    def _handle(self, net_id: str | None) -> NetworkHandle:
        target = net_id or self.active
        if target is None:
            raise ActionError("no network is open")
        try:
            return self.registry.get(target)
        except KeyError as exc:
            raise ActionError(exc.args[0]) from exc

    # -------------------------------------------------- job actions (run on a job thread, lock only to register)

    def _load(self, source: str, spec_version: str) -> tuple[Network, dict[str, Any]]:
        """Load ``source`` and materialise its link/node frames, all without the session lock."""
        try:
            net = Network.from_source(source, spec_version=spec_version)
            frames = {"links_df": as_pandas(net.links), "nodes_df": as_pandas(net.nodes)}
        except Exception as exc:  # boundary: any load failure is a user-facing error, not a crash
            # Both may carry a presigned query string or userinfo: never put either in a message.
            raise ActionError(f"could not open {scrub(source, limit=None)}: {scrub(str(exc), limit=None)}") from exc
        return net, frames

    def _job_open_network(self, action: OpenNetwork, ctx: JobContext) -> _Loaded:
        settings = self.settings
        source = open_locator(action.source, settings)  # only remote URLs skip io.allowed_roots
        ctx.stage("open", progress=0.1)
        net, frames = self._load(source, settings.io.spec_version)
        ctx.stage("register", progress=0.9)  # last cancellation checkpoint before ``_commit``
        return _Loaded(net, frames, source=source, label=action.label, net_id=action.net_id)

    def _job_build_network(self, action: BuildNetwork, ctx: JobContext) -> _Loaded:
        settings = self.settings
        plan = build.plan_build(action, settings)
        build.remove_stale_partials(plan.dest.parent)  # here, not in plan_build: an estimate must not delete
        threshold = settings.app.approve_above_s
        with build.staging(plan.dest) as tmp:  # removes the partial output on any failure, cancel included
            engine = IbisEngine()  # private connection: never shared with the networks the browser reads
            try:
                ctx.stage("estimate", progress=0.0)
                estimate = build.estimate_for(action, plan, settings, http=self.http, engine=engine)
                over = needs_approval(estimate, threshold)
                if over and not action.approved:
                    raise ApprovalRequired(estimate, threshold)
                try:
                    net = build.fetch_and_convert(
                        action, plan, settings, ctx, estimate=estimate, http=self.http, engine=engine
                    )
                    build.write_output(net, tmp, action.output_format, ctx)
                    # Last cancellation checkpoint: once the output is promoted the build is done, so a
                    # later cancel cannot leave a finished output that the history calls cancelled.
                    ctx.stage("open", progress=0.9)
                    build.promote(tmp, plan.dest)
                except ActionError as exc:  # includes JobCancelled: keep the estimate for the history entry
                    exc.payload = {**(exc.payload or {}), "estimate": estimate.to_dict()}
                    raise
                except Exception as exc:  # boundary: an unexpected failure keeps its estimate too
                    logger.exception("workbench build %s failed", action.name)
                    failed = ActionError(f"build failed: {type(exc).__name__}: {scrub(str(exc), limit=None)}")
                    failed.payload = {"estimate": estimate.to_dict()}
                    raise failed from exc
            finally:
                engine.close()
        output = str(plan.dest)
        try:
            loaded, frames = self._load(output, action.spec_version or settings.io.spec_version)
        except ActionError as exc:
            failed = ActionError(
                f"the network was written to {output} but could not be opened ({exc}); open it with OpenNetwork"
            )
            failed.payload = {"output": output, "estimate": estimate.to_dict()}
            raise failed from exc
        extra = {
            "output": output,
            "estimate": estimate.to_dict(),
            "threshold_s": threshold,
            "approval": "given" if over else "not_needed",
        }
        return _Loaded(loaded, frames, source=output, label=action.label or action.name, extra=extra)

    def _do_close_network(self, action: CloseNetwork) -> None:
        self._handle(action.net_id)
        self.registry.remove(action.net_id)
        for handle in self.registry:  # a later network may reuse the id: never leave a copy pointing at it
            if handle.derived_from == action.net_id:
                handle.derived_from = f"{action.net_id} (closed)"
        # Its few-shot examples go too: a later network may reuse the id.
        self._examples = deque((e for e in self._examples if e[0] != action.net_id), maxlen=_MAX_EXAMPLES)
        if self.selection and self.selection["net_id"] == action.net_id:
            self.selection = None
        if self.active == action.net_id:
            ids = self.registry.ids()
            self.active = ids[0] if ids else None

    def _do_set_active_network(self, action: SetActiveNetwork) -> None:
        self.active = self._handle(action.net_id).id

    def _prepare_select(self, action: Select) -> _ParsedSelect | None:
        """Parse and resolve an utterance WITHOUT the session lock (an LLM call can take seconds).

        Takes the lock only to snapshot the network, parser, provider registry, settings and
        examples. ``None`` (nothing to prepare) for link-id and non-roadway selections, which
        :meth:`_do_select` handles under the lock. Provider failures raise :class:`ActionError`.
        """
        if action.component != "roadway" or action.utterance is None:
            return None
        with self._lock:
            self._handle(action.net_id)  # fail fast (no network open) before touching the keyring
        try:
            parser, registry = self._parser_snapshot()  # takes the lock itself, never while reading the keyring
        except LLMError as exc:  # e.g. no key for the chosen provider: the user must act
            raise ActionError(str(exc)) from None
        with self._lock:
            if self.llm is not registry:  # settings moved since the parser was built: its endpoint is stale
                raise ActionError("LLM settings changed while preparing; try again")
            handle = self._handle(action.net_id)
            settings, version = self.settings, handle.version
            examples = tuple((u, p) for net_id, u, p in self._examples if net_id == handle.id)
        try:
            intent, result, parsed_by = self._select_utterance(
                handle, action.utterance, parser, registry, settings, examples
            )
        except _Unparsed as exc:  # carries the parser's own message
            return _ParsedSelect(handle, version, None, None, {**_parser_info(parser), "mode": None}, exc)
        return _ParsedSelect(handle, version, intent, result, parsed_by)

    def _do_select(self, action: Select, prepared: _ParsedSelect | None = None) -> dict[str, Any]:
        if action.component != "roadway":
            raise NotSupportedYet("transit selection arrives with the transit component (phase P6)")
        if action.utterance is None:
            handle = self._handle(action.net_id)
            intent = SelectionIntent(link_ids=list(action.link_ids or []))
            result = resolve_frames(intent, handle.links_df(), handle.nodes_df())
            self.selection = selection_payload(handle, result)
            return self.selection
        if prepared is None:  # called directly rather than through dispatch: parse here, under the lock
            prepared = self._prepare_select(action)
            assert prepared is not None  # a roadway utterance always prepares
        handle = prepared.handle
        # Re-resolve the target the way the action names it: with ``net_id=None`` that is the active
        # network *now*. If it is another network (or this one was closed or edited) meanwhile, a
        # replay of this action would select on a different network, so refuse rather than diverge.
        if self._handle(action.net_id) is not handle or handle.version != prepared.version:
            raise ActionError(
                f"the target network changed while the request was being parsed (was {handle.id!r}); try again"
            )
        if prepared.error is not None:
            self.selection = unparsed_payload(handle, action.utterance, prepared.error, parsed_by=prepared.parsed_by)
            return self.selection
        if prepared.result.status == "resolved":
            self._examples.append((handle.id, action.utterance, payload_from_intent(prepared.intent)))
        self.selection = selection_payload(
            handle, prepared.result, utterance=action.utterance, parsed_by=prepared.parsed_by
        )
        return self.selection

    def _select_utterance(
        self,
        handle: NetworkHandle,
        utterance: str,
        parser: Any,
        registry: ProviderRegistry,
        settings: Settings,
        examples: tuple[tuple[str, dict[str, Any]], ...],
    ) -> tuple[SelectionIntent, Any, dict[str, Any] | None]:
        """Parse (an LLM parser gets the prompt context), resolve, and retry once with close matches if none.

        Runs without the session lock: everything it reads is a snapshot or the handle's own
        thread-safe cache.
        """
        if not isinstance(parser, LLMParser):
            intent, mode = _parse(parser, utterance, None)
            parsed_by = {**_parser_info(parser), "mode": mode}
            return intent, resolve_frames(intent, handle.links_df(), handle.nodes_df()), parsed_by
        provider = parser.provider.name
        endpoint_local = is_local_url(getattr(parser.provider, "base_url", "") or "")
        context = self._prompt_context(handle, provider, registry, settings, examples, endpoint_local=endpoint_local)
        intent, mode = _parse(parser, utterance, context)
        result = resolve_frames(intent, handle.links_df(), handle.nodes_df())
        parsed_by = {**_parser_info(parser), "mode": mode}
        quality = settings.llm.quality
        # ``match_retry`` is auto/on/off: always resolve it through the registry, never by truthiness.
        # Checked on the *current* registry, so turning it off while the first call ran still counts.
        current = self.llm
        retry_on = current.match_retry_on(provider) and _follows_endpoint(
            current.settings.quality.match_retry, endpoint_local
        )
        if result.status == "not_found" and retry_on:
            # With grounding off the vocabulary is built here but NOT sent: only the few close names
            # in the hint leave the machine, which is what makes the retry a narrow opt-in.
            vocabulary = context.vocabulary or _vocabulary(handle, quality.grounding_max_names)
            hint = close_match_hint(intent, vocabulary, quality.match_candidates)
            if hint:
                try:
                    retry, mode = _parse(parser, utterance, replace(context, hint=hint))
                except _Unparsed:
                    retry = None  # keep the first, honest "not found"
                if retry is not None:
                    intent, result = retry, resolve_frames(retry, handle.links_df(), handle.nodes_df())
                    parsed_by = {**_parser_info(parser), "mode": mode, "match_retry": True}
        return intent, result, parsed_by

    def _prompt_context(
        self,
        handle: NetworkHandle,
        provider: str,
        registry: ProviderRegistry,
        settings: Settings,
        examples: tuple[tuple[str, dict[str, Any]], ...],
        *,
        endpoint_local: bool,
    ) -> PromptContext:
        """The optional prompt parts ``llm.quality`` turns on for ``provider`` (see :mod:`netstead.select.prompt`).

        ``endpoint_local`` is whether the endpoint the parser will *actually* call is on this
        machine; an ``auto`` setting needs it as well as the registry's say-so (belt and braces).
        """
        quality = settings.llm.quality
        assistant = assistant_context(quality.assistant_context_max_chars) if quality.assistant_context else ""
        project = ""
        if registry.project_context_on(provider) and _follows_endpoint(quality.project_context, endpoint_local):
            roots = allowed_roots(settings)  # notes are only read from inside io.allowed_roots
            path = find_project_context(handle.source, self.project_dir, roots)
            project = read_capped(path, quality.project_context_max_chars, roots) if path else ""
        grounding = registry.grounding_on(provider) and _follows_endpoint(quality.grounding, endpoint_local)
        vocabulary = _vocabulary(handle, quality.grounding_max_names) if grounding else ()
        shots = examples[-quality.few_shot_max :] if quality.few_shot else ()
        return PromptContext(assistant=assistant, project=project, vocabulary=vocabulary, examples=shots)

    def _do_clear_selection(self, action: ClearSelection) -> None:
        self.selection = None

    def _do_style(self, action: Style) -> dict[str, Any]:
        patch = action.model_dump(exclude_none=True, exclude={"type"})
        color_by = patch.get("color_by")
        if color_by not in (None, "none"):
            names = {c["name"] for c in styleable_columns(self._handle(None).links_df())}
            if color_by not in names:
                raise ActionError(f"cannot color by {color_by!r}; choose one of {sorted(names)}")
        for key, value in patch.items():
            self.style[key] = {**self.style[key], **value} if isinstance(value, dict) else value
        return copy.deepcopy(self.style)

    def _do_navigate(self, action: Navigate) -> None:
        if action.to_selection and not self.selection:
            raise ActionError("nothing is selected")
        self.events.publish({"type": "navigate", **action.model_dump(mode="json", exclude={"type"})})

    def _do_set_setting(self, action: SetSetting) -> dict[str, Any]:
        key = action.key.strip().lower()
        if _is_config_only(key):  # "io" would replace io.allowed_roots too
            raise ActionError(
                f"{action.key} cannot be changed here: io.allowed_roots can only be set in config files, env, "
                "or on the command line"
            )
        try:
            if action.scope == "session":
                overrides = {**self._overrides, action.key: action.value}
                loaded = load_settings(project_dir=self.project_dir, overrides=overrides, environ=self._environ)
                self._check_plugin_settings(loaded.settings)
                self._overrides = overrides
            else:
                if key.split(".", 1)[0] == "plugins":  # check first: a saved file a plugin rejects breaks startup
                    preview = load_settings(
                        project_dir=self.project_dir,
                        overrides={**self._overrides, action.key: action.value},
                        environ=self._environ,
                    )
                    self._check_plugin_settings(preview.settings)
                save_setting(
                    action.key, action.value, scope=action.scope, project_dir=self.project_dir, environ=self._environ
                )
                loaded = load_settings(project_dir=self.project_dir, overrides=self._overrides, environ=self._environ)
            value = _value_or_unset(loaded.settings, action.key)
        except SettingsError as exc:
            raise ActionError(str(exc)) from exc
        self.loaded = loaded
        if key.split(".", 1)[0] in ("select", "llm"):
            self.reset_llm()  # parser and adapters rebuild from the new provider/model/endpoint on next use
        return {"key": action.key, "value": value, "source": loaded.sources.get(action.key)}


#: Settings the action API refuses at every scope. ``io.allowed_roots`` is the sandbox for every local
#: read and write; if an action (a browser click, a replayed script, the NL assistant) could widen it,
#: the sandbox would protect nothing.
_CONFIG_ONLY_KEYS = ("io.allowed_roots",)
#: Shown beside a key :func:`refusal_reason` refuses as config-only.
_CONFIG_ONLY_REASON = (
    "The folders the app may read and write. Set them in a config file, a NETSTEAD_IO__ALLOWED_ROOTS "
    "env var, or on the command line: if an action could widen them, the sandbox would protect nothing."
)
#: Shown beside a secret-named key. Config validation refuses them (keys live in the OS keyring), so this
#: only appears if one slips into the sources some other way.
_SECRET_REASON = "Credentials are never settings. Set API keys in Settings → Language models."
#: Keys the server reads only at launch: a change applies the next time `netstead app` starts.
_RESTART_KEYS = ("app.host", "app.port", "app.console", "app.disabled_plugins")
#: Sections in the schema that nothing reads yet (see the P1b plan, open question 8).
_SECTION_NOTES = {
    "engine": "Not used by the workbench yet: networks open with DuckDB's own defaults.",
    "validation": "Not used by the workbench yet: validation in the app arrives in phase P2.",
    "credentials": "Not used by the workbench yet: credential sources are shown by the URL check only.",
}


def _is_config_only(key: str) -> bool:
    k = key.strip().lower()
    return any(k == c or c.startswith(f"{k}.") or k.startswith(f"{c}.") for c in _CONFIG_ONLY_KEYS)


def refusal_reason(key: str) -> str | None:
    """Why the action API refuses to change setting ``key`` at every scope, or ``None`` if it may.

    >>> refusal_reason("io.allowed_roots") is not None, refusal_reason("viz.basemap")
    (True, None)
    """
    if _is_config_only(key):
        return _CONFIG_ONLY_REASON
    if is_secret_name(key.strip().rsplit(".", 1)[-1]):
        return _SECRET_REASON
    return None


#: Recorded ``error_type`` -> the exception :meth:`Session.dispatch` re-raises (anything else: ActionError).
_ERROR_TYPES: dict[str, type[ActionError]] = {
    cls.__name__: cls for cls in (NotSupportedYet, JobCancelled, PathNotAllowed)
}

#: How many resolved selections the few-shot memory keeps (``llm.quality.few_shot_max`` picks from these).
_MAX_EXAMPLES = 50
#: How long a "no API key" answer is reused before the key store (env, OS keyring) is asked again.
#: Any key write or ``select.*``/``llm.*`` change resets it at once (:meth:`Session.reset_llm`).
_MISSING_KEY_TTL_S = 5.0


@dataclass(frozen=True)
class _ParsedSelect:
    """What :meth:`Session._prepare_select` hands :meth:`Session._do_select`: a parse outcome, made off the lock."""

    handle: NetworkHandle
    version: int  # the handle's version when parsing started: the result is stale if it moved
    intent: SelectionIntent | None
    result: Any
    parsed_by: dict[str, Any] | None
    error: Exception | None = None  # the parser could not read the utterance (a "could not parse" selection)


def _build_mount(plugin: WorkbenchPlugin, host: Host) -> tuple[Any, Path | None]:
    """``(router, static_dir)`` for ``plugin``, checked: raises if either can't be served."""
    static_dir = Path(plugin.static_dir) if plugin.static_dir is not None else None
    if static_dir is not None and not static_dir.is_dir():
        raise ValueError(f"static_dir {static_dir.name!r} is not a directory")
    if plugin.router is None:
        return None, static_dir
    from fastapi import APIRouter  # the server's dependency: only needed when a plugin brings routes

    router = plugin.router(host)
    if not isinstance(router, APIRouter):
        raise TypeError(f"router(host) returned {type(router).__name__}, not a fastapi APIRouter")
    return router, static_dir


def _json_schema_or_none(model: type[BaseAction]) -> dict[str, Any] | None:
    """``model``'s JSON schema, or ``None`` (logged) when pydantic can't describe one of its fields."""
    try:
        return model.model_json_schema()
    except Exception:  # boundary: a plugin's model may raise anything; the catalog must still answer
        logger.exception("no JSON schema for workbench action %r", model.action_type())
        return None


def _mark_rolled_back(children: list[_Nested], parent_type: str) -> None:
    """Nested entries that succeeded but were undone with their parent: history must not call them applied."""
    for child in children:
        if child.entry.ok:
            child.entry.ok, child.entry.error_type = False, "RolledBack"
            child.entry.error = f"rolled back: {parent_type} failed"
        _mark_rolled_back(child.children, parent_type)


def _restore_stamps(handle: NetworkHandle, stamps: Mapping[str, Any]) -> None:
    """Put back the sync stamps a rolled-back mutate dropped (the tables are back to what they described).

    corral's ``DirtyTracker`` has no public "restore a stamp" method, and re-stamping can't stand in
    for one (``hash_table`` is not stable across reads of some sources), so the saved stamp object is
    written back into the tracker's table map. A tracker without that map leaves the table unstamped,
    which corral treats as "unknown".
    """
    tracker = handle.roadway.dirty_tracker
    if tracker is None:
        return
    stamped = getattr(tracker, "_tables", None)
    for name, stamp in stamps.items():
        if stamp is not None and isinstance(stamped, dict):
            stamped[name] = stamp
        else:
            tracker.mark_dirty(name)


def _value_or_unset(settings: Settings, key: str) -> Any:
    """``get_value`` for ``key``; a removed plugin setting (no model default at this layer) reads as ``None``."""
    try:
        return get_value(settings, key)
    except SettingsError:
        if key.strip().lower().split(".", 1)[0] == "plugins":
            return None
        raise


def _follows_endpoint(mode: str, endpoint_local: bool) -> bool:
    """An ``auto`` quality setting also needs the called endpoint to be local; ``on`` is the user's explicit opt-in."""
    return mode == "on" or endpoint_local


def _vocabulary(handle: NetworkHandle, limit: int) -> tuple[str, ...]:
    """The network's ``limit`` most common names and route numbers (cached per network version)."""
    return handle.cached(f"vocabulary:{limit}", lambda: vocabulary_from_links(handle.links_df(), limit))


def _parser_info(parser: Any) -> dict[str, Any]:
    """``parser.describe()`` when it has one: provider/model/mode, never secrets."""
    if hasattr(parser, "describe"):
        return dict(parser.describe())
    return {"provider": type(parser).__name__, "model": None, "mode": None}


class _Unparsed(Exception):
    """The parser could not read the utterance (not a provider failure): a normal "could not parse" selection."""


def _parse(parser: Any, utterance: str, context: PromptContext | None) -> tuple[SelectionIntent, str | None]:
    """Run ``parser`` and return ``(intent, mode)``: the mode this call used (an LLM parser's tools/JSON).

    Provider failures become :class:`ActionError`, anything else :class:`_Unparsed`. No fallback
    to another provider: a key, quota or connection problem is the user's to fix.
    """
    try:
        if isinstance(parser, LLMParser):
            return parser.parse_detailed(utterance, context=context)
        return parser.parse(utterance), _parser_info(parser)["mode"]
    except LLMError as exc:  # missing/invalid key, rate limit, timeout: the user must act, so it's an error
        raise ActionError(str(exc)) from None
    except Exception as exc:  # any other parse failure is a normal "could not parse" selection
        raise _Unparsed(str(exc)) from exc
