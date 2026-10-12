"""Typed workbench Actions: the one vocabulary for UI clicks, Python, and the NL assistant.

Every state change in a :class:`~netstead.workbench.session.Session` is one of these
pydantic models, discriminated on ``type``. The same JSON schema is what the LLM
sees as tools (P3), so natural language yields validated Actions, never ids or
code. ``mutates`` marks actions the assistant must draft-before-apply;
``runs_as_job`` marks actions whose slow work runs on a background job thread
(see :mod:`netstead.workbench.jobs`); ``replay_overrides`` are fields forced in
the ``to_python`` replay snippet.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Iterable, Iterator, Mapping
from pathlib import PurePath
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from netstead.llm.secrets import has_url_userinfo, is_secret_name, looks_like_secret

from .area import Area
from .registry import Component, default_label

__all__ = [
    "CORE_ACTIONS",
    "MAX_EDIT_IDS",
    "Action",
    "ActionRegistry",
    "AddRows",
    "BaseAction",
    "BuildNetwork",
    "ClearSelection",
    "CloseNetwork",
    "DeleteRows",
    "EditCells",
    "Navigate",
    "OpenNetwork",
    "RunValidation",
    "Select",
    "SetActiveNetwork",
    "SetSetting",
    "Style",
    "UndoEdit",
    "action_json_schema",
    "import_line",
    "is_secret_name",
    "parse_action",
    "script_imports",
    "to_python",
]

RGB = Annotated[list[Annotated[int, Field(ge=0, le=255)]], Field(min_length=3, max_length=3)]


class BaseAction(BaseModel):
    """Base class for every Workbench Action, core and plugin.

    A subclass declares ``type: Literal["<type>"] = "<type>"`` (plugins: ``"<plugin id>.<name>"``)
    and its fields. ``mutates`` marks actions the assistant must draft before applying;
    ``runs_as_job`` (core only) runs the slow work on a background job thread; ``replay_overrides``
    are fields forced in the ``to_python`` replay snippet.

    Field values must round-trip through ``repr`` (str, numbers, bools, None, lists/dicts/tuples of
    those, nested pydantic models): "copy session as Python" writes each field as ``name=<repr(value)>``,
    so a value whose ``repr`` isn't valid Python makes the copied script fail.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)  # errors never echo a value (keys)
    mutates: ClassVar[bool] = False
    runs_as_job: ClassVar[bool] = False
    replay_overrides: ClassVar[dict[str, Any]] = {}
    if TYPE_CHECKING:  # every concrete Action declares its own ``type`` field; not a field of the base
        type: str

    @classmethod
    def action_type(cls) -> str | None:
        """The ``type`` discriminator this class carries: ``x`` for exactly ``type: Literal["x"] = "x"``.

        ``None`` for anything else (no ``type``, ``type: str``, a multi-value ``Literal``, a default that
        isn't its one value): such a class can't be a member of the discriminated Action union.
        """
        field_info = cls.model_fields.get("type")
        if field_info is None or get_origin(field_info.annotation) is not Literal:
            return None
        default = field_info.default
        valid = isinstance(default, str) and default and get_args(field_info.annotation) == (default,)
        return default if valid else None

    def job_label(self) -> str:
        """The label a ``runs_as_job`` action's background job shows in the jobs panel."""
        return self.type.replace("_", " ")


class OpenNetwork(BaseAction):
    """Load a GMNS network from a local path (inside ``io.allowed_roots``) or URL and make it active."""

    type: Literal["open_network"] = "open_network"
    runs_as_job: ClassVar[bool] = True
    source: str
    label: str | None = None
    net_id: str | None = None

    def job_label(self) -> str:
        """``open <name>``, named after the source."""
        return f"open {default_label(self.source)}"


class CloseNetwork(BaseAction):
    """Close an open network."""

    type: Literal["close_network"] = "close_network"
    net_id: str


class SetActiveNetwork(BaseAction):
    """Switch which open network the map and tables show."""

    type: Literal["set_active_network"] = "set_active_network"
    net_id: str


class Select(BaseAction):
    """Select features by natural-language utterance or explicit ids (``net_id`` defaults to active)."""

    type: Literal["select"] = "select"
    net_id: str | None = None
    component: Component = "roadway"
    utterance: str | None = None
    link_ids: list[int | str] | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Select:
        if (self.utterance is None) == (self.link_ids is None):
            raise ValueError("give exactly one of utterance or link_ids")
        return self


class ClearSelection(BaseAction):
    """Clear the current selection."""

    type: Literal["clear_selection"] = "clear_selection"


class Style(BaseAction):
    """Partially update the map style; omitted fields are unchanged."""

    type: Literal["style"] = "style"
    color_by: str | None = None
    ramp: Literal["YlOrRd", "Blues", "Viridis"] | None = None
    show: dict[Literal["links", "nodes", "labels", "selection"], bool] | None = None
    colors: dict[Literal["links", "nodes", "selection"], RGB] | None = None
    offset: bool | None = None
    show_direction: bool | None = None
    show_legend: bool | None = None


class Navigate(BaseAction):
    """Move the map camera to a bbox, the active network, or the selection."""

    type: Literal["navigate"] = "navigate"
    bbox: tuple[float, float, float, float] | None = None
    to_network: bool = False
    to_selection: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> Navigate:
        if sum([self.bbox is not None, self.to_network, self.to_selection]) != 1:
            raise ValueError("give exactly one of bbox, to_network, to_selection")
        return self


class RunValidation(BaseAction):
    """Validate a network (spec, keys, structure, and the quality rules in Settings → validation) as a job."""

    type: Literal["run_validation"] = "run_validation"
    runs_as_job: ClassVar[bool] = True
    net_id: str | None = None
    quality: bool = True

    def job_label(self) -> str:
        """``validate <network>``."""
        return f"validate {self.net_id or 'network'}"


class SetSetting(BaseAction):
    """Change a setting (dotted key) for this session, or persist it to the user/project file.

    Refuses API-key-shaped values *at validation*, before anything is recorded: keys are set
    through the write-only ``/api/llm/keys`` route (or ``netstead llm set-key``), never as settings.
    (``io.allowed_roots`` is refused later, by the session, at every scope.)
    """

    type: Literal["set_setting"] = "set_setting"
    mutates: ClassVar[bool] = True
    key: str
    value: Any = None
    scope: Literal["session", "user", "project"] = "session"

    @model_validator(mode="after")
    def _no_secrets(self) -> SetSetting:
        # Messages never echo the key or value; ``hide_input_in_errors`` keeps pydantic's own text clean too.
        if looks_like_secret(self.key) or looks_like_secret(self.value):
            raise ValueError(
                "that value looks like an API key; set keys in Settings → Language models (they are never settings)"
            )
        names = [self.key.rsplit(".", 1)[-1], *_nested_keys(self.value)]
        if any(is_secret_name(n) for n in names):
            raise ValueError(
                "keys, tokens and passwords are never settings; set API keys in Settings → Language models"
            )
        if any(has_url_userinfo(text) for text in (self.key, *_strings(self.value))):
            raise ValueError("URLs in settings must not contain a username, password or token")
        base_urls = [self.value] if names[0] == "base_url" else []
        base_urls += _values_under(self.value, "base_url")
        if any(isinstance(u, str) and ("?" in u or "#" in u) for u in base_urls):
            raise ValueError("base_url must not contain a query string or fragment")
        return self


def _nested_keys(value: Any) -> Iterator[str]:
    """Every dict key nested anywhere in ``value``."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            yield str(k)
            yield from _nested_keys(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _nested_keys(v)


def _strings(value: Any) -> Iterator[str]:
    """Every string in ``value``, dict keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _strings(v)


def _values_under(value: Any, name: str) -> Iterator[Any]:
    """Every value stored under a dict key ``name`` anywhere in ``value``."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            if k == name:
                yield v
            yield from _values_under(v, name)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _values_under(v, name)


#: Name suffixes that imply an output format; a ``BuildNetwork.name`` ending in one must match it.
_OUTPUT_SUFFIXES = frozenset({".zip", ".duckdb", ".csv", ".parquet"})


class BuildNetwork(BaseAction):
    """Build a GMNS network from OSM or Overture, write it to ``output_dir``, then open it from disk.

    Give exactly one of ``area`` (fetch from the service) or ``input_file`` (a local ``.osm`` /
    Overpass ``.json`` for OSM, or a local snapshot folder for Overture). Without ``approved``, a
    build whose estimate is over ``app.approve_above_s``, or cannot be estimated, fails with
    :class:`~netstead.workbench.errors.ApprovalRequired` (carrying the estimate). A replayed snippet
    always passes ``approved=True``: re-running a recorded build counts as approval.
    """

    type: Literal["build_network"] = "build_network"
    mutates: ClassVar[bool] = True
    runs_as_job: ClassVar[bool] = True
    replay_overrides: ClassVar[dict[str, Any]] = {"approved": True}
    source: Literal["osm", "overture"]
    area: Area | None = None
    input_file: str | None = None
    output_dir: str
    output_format: Literal["parquet", "csv", "duckdb", "zip"]
    name: str = Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?$", max_length=100)
    network_type: str = "drive"
    extra_tags: list[str] | None = None
    spec_version: str | None = None
    overture_release: str | None = None
    label: str | None = None
    approved: bool = False

    @model_validator(mode="after")
    def _one_input(self) -> BuildNetwork:
        if (self.area is None) == (self.input_file is None):
            raise ValueError("give exactly one of area or input_file")
        if self.overture_release is not None and self.source != "overture":
            raise ValueError("overture_release only applies to source='overture'")
        suffix = PurePath(self.name).suffix.lower()
        if suffix in _OUTPUT_SUFFIXES and suffix != f".{self.output_format}":
            raise ValueError(f"name {self.name!r} ends in {suffix} but output_format is {self.output_format!r}")
        return self

    def job_label(self) -> str:
        """``build <name>``, named after the output."""
        return f"build {self.name}"


#: Ids one ``EditCells`` / ``DeleteRows`` takes, and rows one ``AddRows`` takes.
MAX_EDIT_IDS = 10_000
#: A cell value: ``None`` clears the cell. The session coerces it to the column's storage type.
CellValue = bool | int | float | str | None


class EditCells(BaseAction):
    """Set cells on rows of one table, picked by primary key (``None`` clears a cell).

    Each value is coerced to its column's storage type; one that does not fit is refused, as are key columns
    and unknown columns. What the spec says about the new values (required, type, enum, foreign keys,
    duplicate keys) comes back as ``warnings``: they warn, they never block.
    """

    type: Literal["edit_cells"] = "edit_cells"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
    component: Component = "roadway"
    table: str = "link"
    ids: list[int | str] = Field(min_length=1, max_length=MAX_EDIT_IDS)
    values: dict[str, CellValue] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class DeleteRows(BaseAction):
    """Delete rows of one table by key, with every row that depends on them through the spec's foreign keys.

    A link takes its lanes and time-of-day rows with it. A node that a link still uses is refused.
    ``POST …/table/<table>/delete-plan`` shows what would go, without deleting anything.
    """

    type: Literal["delete_rows"] = "delete_rows"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
    component: Component = "roadway"
    table: str = "link"
    ids: list[int | str] = Field(min_length=1, max_length=MAX_EDIT_IDS)
    note: str | None = Field(default=None, max_length=500)


class AddRows(BaseAction):
    """Append rows to one table (Python and the API; the app has no drawing tool). Each row needs its key."""

    type: Literal["add_rows"] = "add_rows"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
    component: Component = "roadway"
    table: str
    rows: list[dict[str, CellValue]] = Field(min_length=1, max_length=MAX_EDIT_IDS)
    note: str | None = Field(default=None, max_length=500)


class UndoEdit(BaseAction):
    """Revert the network's last edit, when the Workbench made it (a plugin undoes its own changes)."""

    type: Literal["undo_edit"] = "undo_edit"
    mutates: ClassVar[bool] = True
    net_id: str | None = None


Action = Annotated[
    OpenNetwork
    | BuildNetwork
    | CloseNetwork
    | SetActiveNetwork
    | Select
    | ClearSelection
    | Style
    | Navigate
    | SetSetting
    | EditCells
    | DeleteRows
    | AddRows
    | UndoEdit
    | RunValidation,
    Field(discriminator="type"),
]
#: The Actions every session understands, in registry order (``to_python`` imports them from ``netstead.workbench``).
#: Append new core Actions at the end, here and in ``Action``; never reorder (``_SCRIPT_HEADER`` names the first nine).
CORE_ACTIONS: tuple[type[BaseAction], ...] = (
    OpenNetwork,
    BuildNetwork,
    CloseNetwork,
    SetActiveNetwork,
    Select,
    ClearSelection,
    Style,
    Navigate,
    SetSetting,
    EditCells,
    DeleteRows,
    AddRows,
    UndoEdit,
    RunValidation,
)


class ActionRegistry:
    """The Action types one session understands: the core ones plus any its plugins register.

    The parsing adapter is rebuilt on every change, so a model pydantic can't put in the union is
    refused by :meth:`register` itself, never by a later :meth:`parse` (which every request uses).
    """

    def __init__(self, models: Iterable[type[BaseAction]] = CORE_ACTIONS) -> None:
        """Start with ``models`` registered (default: :data:`CORE_ACTIONS`)."""
        self._models: dict[str, type[BaseAction]] = {}
        for model in models:
            self._models[self._check(model)] = model
        self._adapter: TypeAdapter[Any] | None = self._build_adapter()

    def register(self, model: type[BaseAction]) -> None:
        """Add ``model``; raises ``TypeError`` for a non-Action, ``ValueError`` for a bad or taken ``type``."""
        action_type = self._check(model)
        self._models[action_type] = model
        try:
            self._adapter = self._build_adapter()
        except Exception as exc:  # pydantic refuses the union: leave the registry exactly as it was
            del self._models[action_type]
            raise ValueError(f"{model.__name__} can't join the Action union: {exc}") from exc

    def unregister(self, action_type: str) -> None:
        """Remove ``action_type`` (``KeyError`` if it isn't registered); used to undo a failed plugin install."""
        del self._models[action_type]
        self._adapter = self._build_adapter()

    def _check(self, model: type[BaseAction]) -> str:
        """``model``'s ``type``, if it may be registered next to the ones already here."""
        if not (isinstance(model, type) and issubclass(model, BaseAction)):
            raise TypeError(f"{model!r} is not a BaseAction subclass")
        action_type = model.action_type()
        if action_type is None:
            raise ValueError(f'{model.__name__} needs a `type: Literal["<type>"] = "<type>"` field (one value)')
        if action_type in self._models:
            raise ValueError(f"action type {action_type!r} is already registered")
        return action_type

    def has(self, action_type: str) -> bool:
        """Whether ``action_type`` is registered."""
        return action_type in self._models

    def types(self) -> list[str]:
        """Registered types, in registration order."""
        return list(self._models)

    def model(self, action_type: str) -> type[BaseAction]:
        """The class registered for ``action_type`` (``KeyError`` if none)."""
        return self._models[action_type]

    def parse(self, data: Mapping[str, Any]) -> BaseAction:
        """Validate a JSON dict into a registered Action (raises ``pydantic.ValidationError``)."""
        return self._get_adapter().validate_python(data)

    def json_schema(self) -> dict[str, Any]:
        """JSON schema of every registered Action (the assistant's tool vocabulary)."""
        return self._get_adapter().json_schema()

    def _get_adapter(self) -> TypeAdapter[Any]:
        if self._adapter is None:
            raise ValueError("no Action types are registered")
        return self._adapter

    def _build_adapter(self) -> TypeAdapter[Any] | None:
        """The adapter for the current models (``None`` when there are none; one model needs no union)."""
        models = list(self._models.values())
        if not models:
            return None
        if len(models) == 1:  # a discriminator needs a union (BaseAction's own config hides inputs)
            return TypeAdapter(models[0])
        union = functools.reduce(operator.or_, models)
        return TypeAdapter(Annotated[union, Field(discriminator="type")], config=ConfigDict(hide_input_in_errors=True))


#: The core-only registry behind the module-level helpers (a session has its own, see ``Session.actions``).
_CORE_REGISTRY = ActionRegistry()


def parse_action(data: dict[str, Any]) -> Action:
    """Validate a JSON dict into a core Action (raises ``pydantic.ValidationError``).

    Core only: a session that has plugins parses with its own registry, ``session.actions.parse``.
    """
    return _CORE_REGISTRY.parse(data)  # type: ignore[return-value]  # core registry: always a core Action


def action_json_schema() -> dict[str, Any]:
    """JSON schema of the *core* Action union.

    Not the assistant's tool vocabulary once plugins exist: exposing Actions to the NL assistant (P3)
    must use the session's own ``session.actions.json_schema()``, which includes its plugins' Actions.
    """
    return _CORE_REGISTRY.json_schema()


def import_line(action: BaseAction) -> str:
    """The ``from ... import ...`` line a replayed ``to_python`` snippet needs for ``action``."""
    return _import_line(type(action))


def _import_line(cls: type[BaseAction]) -> str:
    module = "netstead.workbench" if cls in CORE_ACTIONS else cls.__module__
    return f"from {module} import {cls.__name__}"


#: The core Actions on a replayed script's first line, exactly as before P2 added core Actions, so a script that
#: uses only these keeps its header byte for byte. A later core Action gets its own line, like a plugin's.
_SCRIPT_HEADER = (
    "OpenNetwork",
    "BuildNetwork",
    "CloseNetwork",
    "SetActiveNetwork",
    "Select",
    "ClearSelection",
    "Style",
    "Navigate",
    "SetSetting",
)


def script_imports(lines: Iterable[str]) -> list[str]:
    """The import lines heading a replayed session script, given its entries' :func:`import_line` values.

    The first line is always ``Session`` plus the nine original core Actions from ``netstead.workbench`` (so
    a script using only those is unchanged from before plugins and P2 existed); each other line (a later core
    Action's, a plugin Action's) follows once, in order.

    >>> script_imports(["from netstead.workbench import Select", "from hello import Greet"])[1:]
    ['from hello import Greet']
    >>> script_imports(["from netstead.workbench import EditCells"])[1:]
    ['from netstead.workbench import EditCells']
    """
    covered = {f"from netstead.workbench import {name}" for name in _SCRIPT_HEADER}
    head = ", ".join(["Session", *_SCRIPT_HEADER])
    return [f"from netstead.workbench import {head}", *dict.fromkeys(ln for ln in lines if ln and ln not in covered)]


def to_python(action: BaseAction) -> str:
    """The Python call that replays ``action`` against a live workbench handle named ``app``.

    Top-level fields equal to their default are omitted; nested models (an ``area``) are written
    in full, so their discriminator survives. ``replay_overrides`` are applied last.
    """
    defaults = {name: f.get_default(call_default_factory=True) for name, f in type(action).model_fields.items()}
    fields = {k: v for k, v in action.model_dump(exclude={"type"}).items() if v != defaults[k]}
    fields.update(type(action).replay_overrides)
    args = ", ".join(f"{k}={v!r}" for k, v in fields.items())
    return f"app.do({type(action).__name__}({args}))"
