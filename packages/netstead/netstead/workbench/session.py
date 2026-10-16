"""Workbench session: the one place state changes, via :meth:`Session.dispatch`.

UI clicks (``POST /api/actions``), Python (``session.dispatch(...)``), and, in
P3, the NL assistant all funnel through ``dispatch``. Each call is recorded as a
:class:`HistoryEntry` carrying its ``to_python`` replay snippet, and publishes
``history`` + ``state`` events for the browser. Network edits are Actions too (``EditCells``,
``DeleteRows``, ``AddRows``, ``UndoEdit``); each one's mutation is a pending edit (see :mod:`.ledger`).

Actions marked ``runs_as_job`` (``OpenNetwork``, ``BuildNetwork``, ``RunValidation``,
``SaveNetwork``) do their slow work on a background job thread *without* the session lock.
:meth:`Session.submit` starts one and returns immediately (the HTTP path); :meth:`Session.dispatch`
starts one and waits (Python, the CLI), so callers see the same blocking behaviour as before.

A successful job ends in :meth:`Session._commit`, which commits the job's outcome (registers the
loaded network, stores the issues, marks the edits saved) *and* records its history entry in one
critical section. History order therefore always matches registry order, even when two jobs overlap,
so replaying the history reproduces the same network ids. Failed and cancelled jobs are recorded by
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
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from corral.editing import Edit, EditingError, EditResult, affected_rows
from corral.editing.apply import apply_edit, reverse_edit
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
from netstead.select.errors import IntentError
from netstead.select.intent import SelectionIntent
from netstead.select.parse import LLMParser, intent_from_payload, make_parser, payload_from_intent
from netstead.select.prompt import PromptContext, close_match_hint, vocabulary_from_links
from netstead.select.resolve import resolve_frames
from netstead.viz.styling import json_scalar, styleable_columns
from netstead.viz.tables import FilterError, filter_frame

from . import build
from .actions import (
    ActionRegistry,
    AddRows,
    BaseAction,
    BuildNetwork,
    ClearSelection,
    CloseNetwork,
    DeleteRows,
    DeleteSelection,
    EditCells,
    Filter,
    Navigate,
    OpenNetwork,
    PinAnchor,
    RunValidation,
    SaveNetwork,
    SaveSelection,
    Select,
    SetActiveNetwork,
    SetSetting,
    Style,
    UndoEdit,
    UseSelection,
    import_line,
    is_secret_name,
    to_python,
)
from .editcheck import CheckResult, check_rows, referencing_keys, touched_keys
from .editing import EditPlan, EditRefused, plan_add, plan_delete, plan_update, table_keys
from .errors import ActionError, ApprovalRequired, JobCancelled, NotSupportedYet, PathNotAllowed
from .estimate import Estimate, needs_approval
from .events import EventBus
from .issues import IssueSet, locate_issues, run_validation
from .jobs import Job, JobContext, JobRunner
from .ledger import CORE_SOURCE, PYTHON_SOURCE, EditLedger, PendingEdit
from .paths import allowed_roots, open_locator, split_source
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
    "width_by": "none",
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

    def commit(self, session: Session) -> dict[str, Any]:
        """Register the network and make it active (called under the session lock)."""
        handle = session.registry.add(self.net, source=self.source, label=self.label, net_id=self.net_id)
        handle.prime(**self.frames)
        session.active = handle.id
        return {"net_id": handle.id, **self.extra}


@dataclass
class _Validated:
    """A validation job's issues, waiting to be stored for the network version they describe."""

    handle: NetworkHandle
    issues: IssueSet

    def commit(self, session: Session) -> dict[str, Any]:
        """Store the issue set (called under the session lock); the network may have been edited meanwhile.

        A run that finishes after a newer one (overlapping validations) is ``superseded``: the newer set is
        kept, so a stale result never replaces a current one.
        """
        h = self.handle
        if h.id not in session.registry.ids() or session.registry.get(h.id) is not h:
            raise ActionError(f"network {h.id!r} was closed while it was being validated")
        held = session.issues.get(h.id)
        superseded = held is not None and held.version > self.issues.version
        if not superseded:
            session.issues[h.id] = self.issues
        return {
            "net_id": h.id,
            "version": self.issues.version,
            "stale": h.version != self.issues.version,
            "superseded": superseded,
            "counts": self.issues.counts(),
            "unknown_rules": list(self.issues.unknown_rules),
        }


@dataclass
class _Saved:
    """A save job's output, reported once the copy is in place."""

    handle: NetworkHandle
    version: int
    output: str
    notes: list[str]
    edits: int
    accepted: int

    def commit(self, session: Session) -> dict[str, Any]:
        """Mark the pending edits saved, if the network did not change while saving (called under the lock).

        A network closed while saving (and perhaps reopened under the same id) is never marked: the copy holds
        the edits of the network that was closed.
        """
        h = self.handle
        closed = h.id not in session.registry.ids() or session.registry.get(h.id) is not h
        edited = h.version != self.version
        ledger = session.edits.get(h.id)
        if ledger is not None and not edited and not closed:
            ledger.mark_saved()
        return {
            "net_id": h.id,
            "output": self.output,
            "version": self.version,
            "closed_while_saving": closed,
            "edited_while_saving": edited,
            "edits_saved": self.edits,
            "accepted_warnings": self.accepted,
            "notes": self.notes,
        }


#: What a job hands :meth:`Session._commit`: each kind commits itself under the session lock.
_Outcome = _Loaded | _Validated | _Saved


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


@dataclass(frozen=True)
class _View:
    """The session's shared view state before an Action ran: what a failed Action puts back."""

    active: str | None
    selection: dict[str, Any] | None
    style: dict[str, Any]
    saved: dict[str, dict[str, Any]]
    filters: dict[str, dict[str, Any]]


@dataclass
class _Running:
    """An Action whose handler is running (innermost last in ``Session._running``)."""

    action: BaseAction
    undo_mark: int  # ``len(Session._undo)`` when it started: a failure undoes everything after it
    view: _View  # the shared view state before it ran
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


@dataclass
class _LedgerMark:
    """Undo record for a change to one network's pending-edit ledger inside an Action: the ledger before it."""

    net_id: str
    before: EditLedger | None  # a snapshot (``EditLedger.copy``); ``None``: the network had no ledger

    def undo(self, session: Session) -> None:
        if self.before is None:
            session.edits.pop(self.net_id, None)
        else:
            session.edits[self.net_id] = self.before


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
        self.saved: dict[str, dict[str, Any]] = {}  # name -> a saved selection payload (SaveSelection)
        self.filters: dict[str, dict[str, Any]] = {}  # net_id -> its Filter: {table, where}
        self.style: dict[str, Any] = copy.deepcopy(DEFAULT_STYLE)
        self.history: list[HistoryEntry] = []
        self.edits: dict[str, EditLedger] = {}  # net_id -> its pending edits
        self.issues: dict[str, IssueSet] = {}  # net_id -> its last validation
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
        self._undo: list[_Mutated | _Derived | _LedgerMark] = []
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
            view = _View(self.active, self.selection, dict(self.style), dict(self.saved), dict(self.filters))
            running = _Running(action, len(self._undo), view)
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
        the networks it derived, and puts back the active network, selection, style, saved selections and
        filters. A plugin's
        own state (its closure, files it wrote) is the plugin's to keep consistent.
        """
        while len(self._undo) > running.undo_mark:
            self._undo.pop().undo(self)
        view = running.view
        ids = self.registry.ids()
        self.active = view.active if view.active in ids else (ids[0] if ids else None)
        self.selection = view.selection if view.selection is None or view.selection.get("net_id") in ids else None
        self.style = view.style
        self.saved = view.saved
        self.filters = {nid: f for nid, f in view.filters.items() if nid in ids}
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

    def _commit(self, action: BaseAction, ctx: JobContext, outcome: _Outcome) -> dict[str, Any]:
        """Commit ``outcome`` and record the success entry in ONE critical section; return the job result.

        The outcome's ``commit`` registers a network, stores issues, or marks edits saved. Doing that and
        recording under one lock acquisition is what keeps history order equal to registry order:
        with separate acquisitions two overlapping jobs could register A, B but record B, A, and a
        replay would then hand out swapped ids. The caller's last ``ctx.stage`` was the final
        cancellation checkpoint, so nothing can cancel the job once this runs.
        """
        with self._lock:
            result = outcome.commit(self)
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
        self,
        net_id: str | None,
        edits: Sequence[Edit],
        *,
        note: str,
        origin: str | None = None,
        recheck: Mapping[str, Iterable[Any]] | None = None,
        expected_version: int | None = None,
    ) -> list[EditResult]:
        """Apply corral ``edits`` to a network's roadway, in order and all-or-nothing; return their results.

        ``expected_version`` (an :class:`~netstead.workbench.editing.EditPlan`'s ``version``) makes it refuse,
        with :class:`ActionError` and nothing changed, when the network is no longer at that version: the
        edits were planned against tables that have changed since.

        The edits run on a copy-on-write draft of the roadway, so the network itself never holds a
        half-applied state: a failing edit discards the draft and re-raises, leaving the tables, their
        sync stamps, the caches and the pending edits as they were. On success the changed tables are
        swapped in at once, the ``version`` is bumped (dropping caches), ``note`` is appended to the
        lineage, and the change becomes a :class:`~netstead.workbench.ledger.PendingEdit` whose source is
        ``origin`` (a plugin id), ``"workbench"`` (inside a core Action) or ``"python"`` (outside any
        Action). The live checks then re-run over every key the network's pending edits touched (``recheck``
        adds keys beyond the rows these edits touched), and ``state`` is published. Inside an Action, all of
        it is undone if that Action (or one it is nested in) fails. Outside any Action (a job, a plugin
        route, Python) nothing would record it, so it gets a history entry of its own, ``<origin>.mutate``,
        whose ``python`` is a comment: it is visible but not replayable. ``origin`` (default ``"python"``
        there) also prefixes the lineage note. The returned :class:`~corral.editing.EditResult` objects are
        the pending edit's rollback data: undo it with :meth:`undo_last` (or :meth:`revert`, given them),
        never by reversing them on the network yourself. The ledger records the version and each changed
        table's expression, so it notices such a change (even one that left the version alone) and undo
        then refuses rather than reverse the edit a second time.
        """
        with self._lock:
            handle = self._handle(net_id)
            _check_version(handle, expected_version)
            if not edits:  # nothing to apply: no version bump, no pending edit, nothing recorded
                return []
            draft = derived_copy(handle.roadway)  # shares the immutable table expressions; copies the wrappers
            applied = [apply_edit(draft, edit) for edit in edits]
            self._swap(handle, draft, {e.table for e in edits}, note, origin)
            touched = touched_keys(applied, table_keys(handle.roadway))
            for table, keys in [*(recheck or {}).items(), *self._referencing(handle, applied).items()]:
                touched.setdefault(table, set()).update(keys)
            tables, removed = {e.table for e in edits}, _removed_links(applied, reverted=False)
            exprs = _exprs(handle, tables)
            self._ledger_for(handle.id).add(self._source(origin), note, handle.version, applied, touched, exprs)
            self._settle(handle, note, origin, kind="mutate", first=touched, tables=tables, removed=removed)
        return applied

    def revert(
        self, net_id: str | None, results: Sequence[EditResult], *, note: str, origin: str | None = None
    ) -> None:
        """Reverse the network's newest pending edit, given its ``results``: :meth:`mutate` run backwards.

        The results are reversed newest first on a copy-on-write draft, which is swapped in the way a
        mutation is: the version is bumped, ``note`` extends the lineage (prefixed with ``origin``), the
        pending edit is dropped, the live checks re-run, and ``state`` is published. Inside an Action a
        failure undoes it; outside one it is recorded as ``<origin>.revert`` (not replayable). Only the
        newest pending edit can be reverted, since corral reverses an edit against the table that edit left
        behind: anything else raises :class:`ActionError`, as does a network changed without the ledger
        (in place, outside :meth:`mutate` and :meth:`revert`): its version moved, or one of the edit's tables
        holds an expression other than the one the ledger recorded.
        """
        with self._lock:
            handle = self._handle(net_id)
            ledger = self.edits.get(handle.id)
            last = ledger.last() if ledger else None
            if last is None or not _same_results(results, last.results):
                raise ActionError(f"only the newest change to {handle.id} can be reverted")
            tables = {r.edit.table for r in last.results}
            if ledger.unseen_change(handle.version, _exprs(handle, tables)):  # type: ignore[union-attr]  # ``last`` is set
                raise ActionError(f"{handle.id} changed outside the Workbench since; undo is unavailable")
            draft = derived_copy(handle.roadway)
            for result in reversed(last.results):
                reverse_edit(draft, result)
            self._swap(handle, draft, tables, note, origin)
            self._ledger_for(handle.id).pop(handle.version, _exprs(handle, tables))
            removed = _removed_links(last.results, reverted=True)
            self._settle(handle, note, origin, kind="revert", first=last.touched, tables=tables, removed=removed)

    def undo_last(self, net_id: str | None, *, source: str = CORE_SOURCE) -> PendingEdit:
        """Revert the network's newest pending edit, if ``source`` made it; return the entry undone.

        Raises :class:`ActionError` when there is nothing to undo, or when the newest change came from
        another source (core never undoes a plugin's change, nor a plugin core's).
        """
        with self._lock:
            handle = self._handle(net_id)
            ledger = self.edits.get(handle.id)
            last = ledger.last() if ledger else None
            if last is None:
                raise ActionError(f"nothing to undo on {handle.id}")
            if last.source != source:
                who = "the Workbench" if last.source == CORE_SOURCE else last.source
                raise ActionError(f"the last change to {handle.id} was made by {who} ({last.note}); undo it there")
            origin = None if source in (CORE_SOURCE, PYTHON_SOURCE) else source
            self.revert(handle.id, last.results, note=f"undo: {last.note}", origin=origin)
        return last

    def edit_check(self, net_id: str | None) -> CheckResult:
        """The live edit warnings on a network now (empty before any edit)."""
        with self._lock:
            ledger = self.edits.get(self._handle(net_id).id)
            return ledger.check if ledger else CheckResult()

    def edits_payload(self, handle: NetworkHandle) -> dict[str, Any]:
        """The Edits panel's data: pending edits (newest first), undo state, dirty state and the live warnings."""
        with self._lock:
            ledger = self.edits.get(handle.id) or EditLedger()
            last = ledger.last()
            return {
                "net_id": handle.id,
                "version": handle.version,
                "entries": [e.view() for e in reversed(ledger.entries)],
                **ledger.summary(),
                "can_undo": bool(last and last.source == CORE_SOURCE),
                "warnings": [w.to_dict() for w in ledger.check.warnings],  # the list, in place of the count
            }

    def delete_preview(self, handle: NetworkHandle, table: str, ids: Sequence[Any]) -> dict[str, Any]:
        """What deleting ``ids`` from ``table`` would remove (rows per table), or why it is refused. Changes nothing.

        Plans on a snapshot taken under the lock, then reads it without the lock, so the cascade's lookups never
        hold up other actions.
        """
        with self._lock:
            version, roadway = handle.version, derived_copy(handle.roadway)
        try:
            plan = plan_delete(roadway, table, ids, version=version)
        except EditRefused as exc:
            return {"table": table, "version": version, "refused": str(exc), "rows": {}, "summary": None}
        return {"table": table, "version": version, "refused": None, "rows": dict(plan.rows), "summary": plan.summary}

    def _swap(self, handle: NetworkHandle, draft: Network, names: Iterable[str], note: str, origin: str | None) -> None:
        """Swap ``draft``'s tables ``names`` into ``handle`` in one step: bump the version, extend the lineage.

        Inside an Action an undo record goes first, so a failing Action puts the tables, version, lineage and
        sync stamps back (Part 1's rollback). Call with the lock held.
        """
        roadway = handle.roadway
        changed = {name: (draft.tables[name].expr, draft.tables[name].dirty) for name in names}
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

    def _ledger_for(self, net_id: str) -> EditLedger:
        """``net_id``'s ledger, about to change: inside an Action its snapshot goes on the undo stack first."""
        ledger = self.edits.get(net_id)
        if self._running:
            self._undo.append(_LedgerMark(net_id, ledger.copy() if ledger is not None else None))
        if ledger is None:
            ledger = self.edits[net_id] = EditLedger()
        return ledger

    def _source(self, origin: str | None) -> str:
        """Who a mutation belongs to: its plugin, a core Action (``"workbench"``), or Python outside any Action."""
        return origin or (CORE_SOURCE if self._running else PYTHON_SOURCE)

    def _referencing(self, handle: NetworkHandle, applied: Sequence[EditResult]) -> dict[str, set[Any]]:
        """Rows still pointing at rows ``applied`` deleted (to re-check); a failure only narrows the live check."""
        if not any(r.edit.op == "delete_rows" for r in applied):
            return {}
        try:
            return referencing_keys(handle.roadway, applied)
        except Exception:  # boundary: a failing lookup must not undo a good edit; Run validation still works
            logger.exception("finding rows that referenced deleted rows on %s failed", handle.id)
            return {}

    def _settle(
        self,
        handle: NetworkHandle,
        note: str,
        origin: str | None,
        *,
        kind: str,
        first: Mapping[str, Iterable[Any]],
        tables: set[str],
        removed: set[Any] | None,
    ) -> None:
        """After a swap: re-run the live checks, move the selection, then publish (in an Action) or record."""
        self._recheck(handle, first)
        self._refresh_selection(handle, tables, removed)
        if self._running:
            self.events.publish({"type": "state", "state": self.state()})
        else:
            self._record_outside_action(handle.id, note, origin or PYTHON_SOURCE, kind=kind)

    def _recheck(self, handle: NetworkHandle, first: Mapping[str, Iterable[Any]] | None = None) -> None:
        """Re-run the live checks over every key the network's ledger touched (call with the lock held).

        ``first`` (the newest change's keys) is checked before older ones, so the per-table cap never skips
        it. It assigns ``ledger.check`` on the live ledger after :meth:`_ledger_for` snapshotted it, so a
        rollback also restores the check that matched the old tables.
        """
        ledger = self.edits.get(handle.id)
        if ledger is None:
            return
        try:
            ledger.check = check_rows(handle.roadway, ledger.touched(), first=first)
        except Exception:  # boundary: a failing check must not undo a good edit; Run validation still works
            logger.exception("live edit check on %s failed", handle.id)
            ledger.check = CheckResult(failed=True)

    def _refresh_selection(self, handle: NetworkHandle, tables: set[str], removed: set[Any] | None) -> None:
        """Keep the shared selection true to an edited network (call with the lock held, after a swap).

        A selection made by a query (its payload keeps the parsed ``intent``) is resolved again; one made
        by ids keeps them, minus the links that no longer exist, and is cleared when none is left. It is
        deterministic, so a replayed session agrees; a failed Action restores the old one (``_roll_back``).
        Only an edit of ``link`` or ``node`` can move it. ``removed`` is the link ids the edit took away
        (``None``: unknown, so the link table is read). A failure here never fails the edit: the query
        falls back to the ids rule, and that to no selection.
        """
        sel = self.selection
        if not sel or sel["net_id"] != handle.id or sel["component"] != "roadway":
            return
        if not tables & {"link", "node"}:  # a lane or time-of-day edit cannot change which links match
            return
        if sel.get("intent") is not None:
            try:
                self.selection = self._resolve_again(handle, sel)
                return
            except Exception:  # boundary: keep the edit; fall back to the ids it selected
                logger.exception("re-resolving the selection's query on %s failed; keeping its ids", handle.id)
                removed = None
        try:
            self._keep_selected_ids(handle, sel, tables, removed)
        except Exception:  # boundary: keep the edit; an unknown selection is no selection
            logger.exception("updating the selection on %s after an edit failed; clearing it", handle.id)
            self.selection = None

    def _keep_selected_ids(
        self, handle: NetworkHandle, sel: dict[str, Any], tables: set[str], removed: set[Any] | None
    ) -> None:
        """The ids rule: keep the selected links that still exist; refresh anchors when nodes moved."""
        if removed is None:
            present = {json_scalar(v) for v in handle.links_df()["link_id"]}
            kept = [i for i in sel["link_ids"] if i in present]
        else:
            kept = [i for i in sel["link_ids"] if i not in removed]
        if kept == sel["link_ids"] and not ("node" in tables and sel["anchors"]):
            return
        if not kept:
            self.selection = None
            return
        result = resolve_frames(SelectionIntent(link_ids=kept), handle.links_df(), handle.nodes_df())
        self.selection = selection_payload(handle, result)

    def _resolve_again(self, handle: NetworkHandle, sel: dict[str, Any]) -> dict[str, Any] | None:
        """``sel`` resolved on ``handle`` as it is now (P2 R4's rule): a query again; ids minus the links now gone."""
        if sel.get("intent") is not None:
            intent = intent_from_payload(sel["intent"], sel["utterance"] or "")
            result = resolve_frames(intent, handle.links_df(), handle.nodes_df())
            return selection_payload(handle, result, utterance=sel["utterance"], parsed_by=sel["parsed_by"])
        present = {json_scalar(v) for v in handle.links_df()["link_id"]}
        kept = [i for i in sel["link_ids"] if i in present]
        if not kept:
            return None
        result = resolve_frames(SelectionIntent(link_ids=kept), handle.links_df(), handle.nodes_df())
        return selection_payload(handle, result)

    def _record_outside_action(self, net_id: str, note: str, origin: str, *, kind: str = "mutate") -> None:
        """Record a ``mutate`` or ``revert`` no Action covers, so nothing changes silently (not replayable)."""
        text = " ".join(note.split())  # one line: it becomes a comment in a script
        entry = HistoryEntry(
            seq=len(self.history) + 1,
            action={"type": f"{origin}.{kind}", "net_id": net_id, "note": text},
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
                "saved_selections": [_saved_summary(n, s) for n, s in self.saved.items()],
                "filters": {nid: {**f, "count": self._filter_count(nid, f)} for nid, f in self.filters.items()},
                "plugins": self._plugin_state(),
                "edits": {nid: ledger.summary() for nid, ledger in self.edits.items()},
                "issues": {
                    nid: {
                        "version": s.version,
                        "stale": s.version != self.registry.get(nid).version,
                        "counts": s.counts(),
                    }
                    for nid, s in self.issues.items()
                },
            }

    def issue_set(self, net_id: str) -> IssueSet | None:
        """The last validation of ``net_id`` (``None`` before any)."""
        with self._lock:
            return self.issues.get(net_id)

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

    def _job_run_validation(self, action: RunValidation, ctx: JobContext) -> _Validated:
        with self._lock:
            handle = self._handle(action.net_id)
            version, rules = handle.version, self.settings.validation
            # A snapshot of the tables at ``version``: an edit meanwhile swaps new expressions into the
            # handle's own table wrappers, which this run must not see half-way through.
            roadway = derived_copy(handle.roadway)
        ctx.stage("validate", progress=0.05)
        try:
            report, unknown = run_validation(roadway, rules, quality=action.quality)
            ctx.stage("locate", progress=0.85)
            issues = locate_issues(report, roadway)
        except ActionError:
            raise
        except Exception as exc:  # boundary: a crashing check is a reported failure, with its reason
            logger.exception("workbench validation of %s failed", handle.id)
            raise ActionError(f"validation failed: {type(exc).__name__}: {scrub(str(exc), limit=None)}") from exc
        ctx.stage("store", progress=0.95)  # last cancellation checkpoint
        return _Validated(handle, IssueSet(handle.id, version, issues, report, unknown, network=roadway))

    def _job_save_network(self, action: SaveNetwork, ctx: JobContext) -> _Saved:
        with self._lock:
            handle = self._handle(action.net_id)
            version, settings = handle.version, self.settings
            ledger = self.edits.get(handle.id)
            pending = len(ledger.entries) if ledger else 0
            open_warnings = ledger.check.warnings if ledger else ()
            crashed, truncated = (ledger.check.failed, ledger.check.truncated) if ledger else (False, False)
            # A snapshot of the tables at ``version`` (an edit meanwhile must not reach this copy half-way).
            roadway = derived_copy(handle.roadway)
        if (crashed or truncated) and not action.accept_warnings:
            # The live check did not cover every edited row, so "no warnings" proves nothing: confirm explicitly.
            why = "failed" if crashed else "stopped early (too many edited rows to check)"
            refused = ActionError(
                f"the live edit check {why}, so the edits may break the network unnoticed; run validation to see "
                "every issue, then save with accept_warnings=True"
            )
            refused.payload = {
                "check_failed": True,
                "truncated": truncated,
                "count": len(open_warnings),
                "warnings": [w.to_dict() for w in open_warnings[:50]],
            }
            raise refused
        if open_warnings and not action.accept_warnings:
            n = len(open_warnings)
            refused = ActionError(
                f"the network has {n} open edit warning{'s' if n != 1 else ''}; review them in the Issues panel, "
                "then save with accept_warnings=True"
            )
            refused.payload = {
                "check_failed": False,
                "truncated": False,
                "count": n,
                "warnings": [w.to_dict() for w in open_warnings[:50]],
            }
            raise refused
        dest = build.output_path(action.output_dir, action.name, action.output_format, settings, what="saves")
        _refuse_inside_source(dest, handle.source)
        build.remove_stale_partials(dest.parent)
        with build.staging(dest) as tmp:
            build.write_output(roadway, tmp, action.output_format, ctx)  # never warns OutOfSyncWarning
            ctx.stage("save", progress=0.95)  # last cancellation checkpoint
            build.promote(tmp, dest)
        # What corral's OutOfSyncWarning would have said, without the source (it may be a presigned URL).
        stale = sorted(n for n, t in roadway.tables.items() if t.dirty)
        notes = [f"{len(stale)} table(s) differ from the source: {', '.join(stale)}"] if stale else []
        return _Saved(handle, version, str(dest), notes, pending, len(open_warnings))

    def _do_close_network(self, action: CloseNetwork) -> None:
        self._handle(action.net_id)
        self.registry.remove(action.net_id)
        for handle in self.registry:  # a later network may reuse the id: never leave a copy pointing at it
            if handle.derived_from == action.net_id:
                handle.derived_from = f"{action.net_id} (closed)"
        # Its few-shot examples go too: a later network may reuse the id.
        self._examples = deque((e for e in self._examples if e[0] != action.net_id), maxlen=_MAX_EXAMPLES)
        self.edits.pop(action.net_id, None)
        self.issues.pop(action.net_id, None)
        if self.selection and self.selection["net_id"] == action.net_id:
            self.selection = None
        self.filters = {nid: f for nid, f in self.filters.items() if nid != action.net_id}
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

    def _do_pin_anchor(self, action: PinAnchor) -> dict[str, Any]:
        sel = self.selection
        if not sel or sel.get("intent") is None:
            raise ActionError("only a selection made by a query has anchors to pin; select with an utterance first")
        if not sel["intent"].get(f"{action.role}_anchor"):
            raise ActionError(f"the selection has no {action.role} anchor to pin")
        args = dict(sel["intent"])
        if action.node_id is None:
            args.pop(f"{action.role}_node", None)
        else:
            args[f"{action.role}_node"] = action.node_id
        handle = self._handle(sel["net_id"])
        try:
            intent = intent_from_payload(args, sel["utterance"] or "")
        except IntentError as exc:
            raise ActionError(str(exc)) from None
        result = resolve_frames(intent, handle.links_df(), handle.nodes_df())
        self.selection = selection_payload(handle, result, utterance=sel["utterance"], parsed_by=sel["parsed_by"])
        return self.selection

    def _saved(self, name: str) -> dict[str, Any]:
        try:
            return self.saved[name]
        except KeyError:
            raise ActionError(f"no saved selection named {name!r}") from None

    def _do_save_selection(self, action: SaveSelection) -> dict[str, Any]:
        if not self.selection:
            raise ActionError("nothing is selected")
        if action.name in self.saved:
            raise ActionError(f"a selection is already saved as {action.name!r}; delete it first")
        self.saved = {**self.saved, action.name: copy.deepcopy(self.selection)}
        return _saved_summary(action.name, self.saved[action.name])

    def _do_use_selection(self, action: UseSelection) -> dict[str, Any]:
        saved = self._saved(action.name)
        if saved["net_id"] not in self.registry.ids():
            raise ActionError(f"the network {saved['net_id']!r} is not open; open it to use {action.name!r}")
        payload = self._resolve_again(self.registry.get(saved["net_id"]), saved)
        if payload is None:
            raise ActionError(f"none of the links saved as {action.name!r} exist any more")
        self.selection = payload
        return payload

    def _do_delete_selection(self, action: DeleteSelection) -> None:
        self._saved(action.name)
        self.saved = {k: v for k, v in self.saved.items() if k != action.name}

    def saved_selections(self) -> list[dict[str, Any]]:
        """A summary of each saved selection (name, network, status, link count, utterance, query or ids)."""
        with self._lock:
            return [_saved_summary(name, sel) for name, sel in self.saved.items()]

    def saved_selection(self, name: str) -> dict[str, Any] | None:
        """A deep copy of the payload saved as ``name`` (``None`` if there is none), as it was when saved."""
        with self._lock:
            sel = self.saved.get(name)
            return copy.deepcopy(sel) if sel is not None else None

    def _do_filter(self, action: Filter) -> dict[str, Any] | None:
        handle = self._handle(action.net_id)
        if not action.where:
            self.filters = {nid: f for nid, f in self.filters.items() if nid != handle.id}
            return None
        entry = {"table": "link", "where": [c.model_dump(mode="json") for c in action.where]}
        ids = self._filter_ids(handle, entry)  # checks the columns and values now, so a bad filter is refused
        self.filters = {**self.filters, handle.id: entry}
        return {**entry, "count": len(ids)}

    def _filter_ids(self, handle: NetworkHandle, entry: dict[str, Any]) -> list[Any]:
        """The link ids ``entry`` matches on ``handle`` (cached per network version)."""
        key = "filter:" + json.dumps(entry["where"], sort_keys=True)

        def run() -> list[Any]:
            try:
                return [json_scalar(v) for v in filter_frame(handle.links_df(), entry["where"])["link_id"]]
            except FilterError as exc:
                raise ActionError(str(exc)) from None
            except (TypeError, ValueError) as exc:  # a value that can't be compared with its column
                raise ActionError(f"a filter value does not fit its column ({type(exc).__name__})") from None

        return handle.cached(key, run)

    def filter_entry(self, net_id: str) -> dict[str, Any] | None:
        """A copy of ``net_id``'s stored ``Filter`` (``{table, where}``), or ``None`` when it has none."""
        with self._lock:
            entry = self.filters.get(net_id)
            return copy.deepcopy(entry) if entry is not None else None

    def filter_ids(self, net_id: str) -> list[Any] | None:
        """The link ids ``net_id``'s filter matches now (``None`` when it has no filter)."""
        with self._lock:
            entry = self.filters.get(net_id)
            handle = self.registry.get(net_id) if entry else None
        return self._filter_ids(handle, entry) if entry and handle else None

    def _filter_count(self, net_id: str, entry: dict[str, Any]) -> int | None:
        """How many links a stored filter matches now; ``None`` when an edit made it unrunnable (a column went)."""
        try:
            return len(self._filter_ids(self.registry.get(net_id), entry))
        except ActionError:
            return None

    def _do_style(self, action: Style) -> dict[str, Any]:
        patch = action.model_dump(exclude_none=True, exclude={"type"})
        wanted = {k: patch[k] for k in ("color_by", "width_by") if patch.get(k) not in (None, "none")}
        if wanted:
            kinds = {c["name"]: c["kind"] for c in styleable_columns(self._handle(None).links_df())}
            if "color_by" in wanted and wanted["color_by"] not in kinds:
                raise ActionError(f"cannot color by {wanted['color_by']!r}; choose one of {sorted(kinds)}")
            numeric = sorted(n for n, k in kinds.items() if k == "continuous")
            if "width_by" in wanted and wanted["width_by"] not in numeric:
                raise ActionError(f"cannot size by {wanted['width_by']!r}; choose a numeric column: {numeric}")
        for key, value in patch.items():
            self.style[key] = {**self.style[key], **value} if isinstance(value, dict) else value
        return copy.deepcopy(self.style)

    def _do_navigate(self, action: Navigate) -> None:
        if action.to_selection and not self.selection:
            raise ActionError("nothing is selected")
        if action.record is not None:
            handle = self._handle(None)
            table, key = action.record.table, f"{action.record.table}_id"
            frame = handle.links_df() if table == "link" else handle.nodes_df()
            if not frame[key].isin([action.record.id]).any():  # a str id on an int key is simply not found
                raise ActionError(f"no {table} {action.record.id!r} in {handle.id}")
        omit = {"type"} if action.record is not None else {"type", "record"}  # an event without one is unchanged
        self.events.publish({"type": "navigate", **action.model_dump(mode="json", exclude=omit)})

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

    def _edit_target(self, action: EditCells | DeleteRows | AddRows) -> NetworkHandle:
        if action.component != "roadway":
            raise NotSupportedYet("transit edits arrive with the transit component (phase P6)")
        return self._handle(action.net_id)

    def _apply_plan(self, handle: NetworkHandle, plan: EditPlan, note: str | None) -> dict[str, Any]:
        """Apply ``plan`` as one pending edit and describe it, with the live warnings on the rows it touched."""
        text = f"{plan.summary} ({note})" if note else plan.summary
        try:
            self.mutate(handle.id, plan.edits, note=text, recheck=plan.recheck, expected_version=plan.version)
        except EditingError as exc:  # corral refused the payload; the draft was discarded, nothing changed
            raise ActionError(f"the edit failed: {exc}") from exc
        ledger = self.edits[handle.id]
        entry = ledger.last()
        assert entry is not None  # mutate just added it
        return {
            "net_id": handle.id,
            "version": handle.version,
            "summary": plan.summary,
            "rows": dict(plan.rows),
            "keys": list(plan.keys),
            "warnings": [w.to_dict() for w in ledger.check.for_rows(entry.touched)],
            "open_warnings": len(ledger.check.warnings),
        }

    def _do_edit_cells(self, action: EditCells) -> dict[str, Any]:
        handle = self._edit_target(action)
        plan = _planned(
            lambda: plan_update(handle.roadway, action.table, action.ids, action.values, version=handle.version)
        )
        return self._apply_plan(handle, plan, action.note)

    def _do_delete_rows(self, action: DeleteRows) -> dict[str, Any]:
        handle = self._edit_target(action)
        # A confirmed delete plan's version: refused here, before planning, if the network changed since the
        # confirmation (so a stale delete of rows that have since gone says so, not "not found"); mutate checks too.
        _check_version(handle, action.expected_version)
        version = handle.version if action.expected_version is None else action.expected_version
        plan = _planned(lambda: plan_delete(handle.roadway, action.table, action.ids, version=version))
        return self._apply_plan(handle, plan, action.note)  # the selection follows in mutate (R4)

    def _do_add_rows(self, action: AddRows) -> dict[str, Any]:
        handle = self._edit_target(action)
        plan = _planned(lambda: plan_add(handle.roadway, action.table, action.rows, version=handle.version))
        return self._apply_plan(handle, plan, action.note)

    def _do_undo_edit(self, action: UndoEdit) -> dict[str, Any]:
        entry = self.undo_last(action.net_id)
        handle = self._handle(action.net_id)
        return {
            "net_id": handle.id,
            "version": handle.version,
            "undone": entry.view(),
            "open_warnings": len(self.edit_check(handle.id).warnings),
        }


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


def _exprs(handle: NetworkHandle, tables: Iterable[str]) -> dict[str, Any]:
    """Each of ``tables``' current expression on ``handle``'s roadway (what the ledger compares by identity)."""
    return {name: handle.roadway.tables[name].expr for name in tables if name in handle.roadway.tables}


def _same_results(given: Sequence[EditResult], held: Sequence[EditResult]) -> bool:
    """Whether ``given`` are exactly the ``EditResult`` objects a pending edit holds (identity, in order)."""
    return len(given) == len(held) and all(a is b for a, b in zip(given, held, strict=True))


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


def _saved_summary(name: str, sel: dict[str, Any]) -> dict[str, Any]:
    """What the state (and the Selection panel's list) shows of a saved selection."""
    return {
        "name": name,
        "net_id": sel["net_id"],
        "status": sel["status"],
        "links": len(sel["link_ids"]),
        "utterance": sel["utterance"],
        "by": "query" if sel.get("intent") is not None else "ids",
    }


def _value_or_unset(settings: Settings, key: str) -> Any:
    """``get_value`` for ``key``; a removed plugin setting (no model default at this layer) reads as ``None``."""
    try:
        return get_value(settings, key)
    except SettingsError:
        if key.strip().lower().split(".", 1)[0] == "plugins":
            return None
        raise


def _removed_links(results: Sequence[EditResult], *, reverted: bool) -> set[Any] | None:
    """The link ids applying (or, ``reverted``, reversing) ``results`` removed; ``None`` when that is unknown.

    Deleting rows (reversing an addition) removes their ids; adding rows (reversing a deletion) and setting
    cells other than ``link_id`` remove none. Anything else (a key change, a whole-table replace) is unknown.
    """
    # e.g. undoing an add_rows deletes the rows it added, so reversed, add_rows is the op that removes links
    removing, keeping = ("add_rows", "delete_rows") if reverted else ("delete_rows", "add_rows")
    out: set[Any] = set()
    for result in results:
        edit = result.edit
        if edit.table != "link" or edit.op == keeping:
            continue
        if edit.op == removing:
            out.update(json_scalar(row.get("link_id")) for row in affected_rows(result))
        elif edit.op != "update_rows" or "link_id" in ((edit.payload or {}).get("set") or {}):
            return None
    return out


def _refuse_inside_source(dest: Path, source: str) -> None:
    """Refuse a save that would land in the network's own (local) source: the source is never written to."""
    try:
        kind, path = split_source(source)
    except PathNotAllowed:
        return  # not a locator this session could have opened from disk
    if kind != "local":
        return
    src = Path(path).expanduser().resolve()
    target = dest.resolve()
    if target == src or target.is_relative_to(src):
        raise ActionError("a saved copy can't go inside the network's own source folder; choose another folder")


def _check_version(handle: NetworkHandle, expected_version: int | None) -> None:
    """Refuse with :class:`ActionError` when ``handle`` is no longer at ``expected_version`` (``None``: no check)."""
    if expected_version is not None and handle.version != expected_version:
        raise ActionError(
            f"{handle.id} changed since the edit was planned (version {expected_version}, now "
            f"{handle.version}); plan it again"
        )


def _planned(planner: Callable[[], EditPlan]) -> EditPlan:
    """Run an edit planner; its refusal becomes a recorded :class:`ActionError` (nothing changed)."""
    try:
        return planner()
    except EditRefused as exc:
        raise ActionError(str(exc)) from exc


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
