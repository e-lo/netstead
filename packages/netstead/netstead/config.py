"""Layered, persisted netstead settings.

Precedence, lowest to highest: model defaults < user file
(``~/.config/netstead/config.toml``) < project file (``./netstead.toml``) <
``NETSTEAD_<SECTION>__<FIELD>`` env vars < session overrides (CLI flags, the
workbench's ``set_setting`` action with ``scope="session"``).

Secrets never live here: credentials stay in env/keyring/netrc via
:mod:`corral.io.credentials`, and LLM API keys in :mod:`netstead.llm.secrets`.
``credentials.keyring_hosts`` only names hosts; ``llm.*`` only holds endpoints.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from netstead.llm.secrets import origin_of, settings_secret_problem
from netstead.spec import DEFAULT_SPEC

__all__ = [
    "DEFAULT_ASSISTANT_ACTIONS",
    "PROVIDER_ALIASES",
    "AssistantSettings",
    "LLMEndpointSettings",
    "LLMQualitySettings",
    "LLMSettings",
    "LoadedSettings",
    "OllamaSettings",
    "Settings",
    "SettingsError",
    "dumps_toml",
    "get_value",
    "is_local_url",
    "load_settings",
    "project_config_path",
    "save_setting",
    "user_config_path",
]

Scope = Literal["user", "project"]
ENV_PREFIX = "NETSTEAD_"
PROJECT_FILE = "netstead.toml"

#: The one non-IP hostname that means "this machine".
_LOCAL_HOSTNAME = "localhost"

#: Old ``select.provider`` names, still accepted and stored under the new name, so existing files keep working.
PROVIDER_ALIASES = {"claude": "anthropic"}


class SettingsError(ValueError):
    """A settings file, env var, or override failed to parse or validate."""


def is_local_url(url: str) -> bool:
    """Whether ``url`` points at this machine, so requests to it stay local.

    ``localhost`` and any loopback IP literal count -- the whole ``127.0.0.0/8`` range, not
    just ``127.0.0.1``, and ``::1`` -- since all of them route back to this machine.
    ``0.0.0.0`` is a bind-all address, not a loopback one, so a ``base_url`` naming it is
    treated as remote (and its ``llm.quality`` "auto" settings stay off).

    >>> is_local_url("http://localhost:11434"), is_local_url("http://[::1]:8000/v1")
    (True, True)
    >>> is_local_url("http://127.0.0.2"), is_local_url("http://0.0.0.0"), is_local_url("http://gpu:11434")
    (True, False, False)
    """
    host = (urlsplit(url).hostname or "").lower()
    if host == _LOCAL_HOSTNAME:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)  # a rejected value may be a credential


class IOSettings(_Section):
    """Reading/writing networks."""

    spec_version: str = DEFAULT_SPEC
    default_format: Literal["csv", "parquet", "duckdb", "zip"] = "parquet"
    allowed_roots: list[str] = Field(default_factory=list)

    @field_validator("allowed_roots")
    @classmethod
    def _no_blank_roots(cls, value: list[str]) -> list[str]:
        # "" resolves to the current folder, which is never what an empty-looking entry meant.
        if any(not root.strip() for root in value):
            raise ValueError(
                "allowed_roots has a blank entry; list folder paths, or use [] for the default (your home folder)"
            )
        return value


class EngineSettings(_Section):
    """DuckDB engine tuning (``None`` = DuckDB's own default)."""

    threads: int | None = None
    memory_limit: str | None = None


class OSMSettings(_Section):
    """OpenStreetMap builds (``None`` = the builder's built-in default)."""

    endpoint: str | None = None
    user_agent: str | None = None
    timeout: int = 180
    retries: int = 3
    mapping_path: str | None = None


class OvertureSettings(_Section):
    """Overture builds (``None`` = the builder's built-in default)."""

    release: str | None = None
    data_root: str | None = None


class BuildSettings(_Section):
    """Defaults the Open / Import wizard prefills for OSM and Overture builds."""

    network_type: str = "drive"
    buffer_m: float = Field(default=1000.0, gt=0)
    extra_tags: list[str] = Field(default_factory=list)


class RuleSettings(_Section):
    """One quality rule's config (mirrors :class:`corral.quality.base.RuleConfig`)."""

    enabled: bool = True
    severity_override: Literal["error", "warning", "info"] | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)


class ValidationSettings(_Section):
    """Per-rule quality configuration, keyed by rule code (e.g. ``quality.high_speed_residential``)."""

    rules: dict[str, RuleSettings] = Field(default_factory=dict)


class SelectSettings(_Section):
    """Natural-language selection: which provider parses utterances, and with which model.

    ``model=None`` means the provider's catalog default (:mod:`netstead.llm.catalog`); for Ollama, when
    that isn't installed, an installed tool-capable model (``ProviderRegistry.resolve_model``).
    """

    provider: Literal["stub", "anthropic", "openai", "gemini", "ollama"] = "stub"
    model: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _alias(cls, value: Any) -> Any:
        return PROVIDER_ALIASES.get(value, value) if isinstance(value, str) else value


class LLMEndpointSettings(_Section):
    """One LLM provider's endpoint. ``base_url=None`` is the official endpoint. Never a key."""

    base_url: str | None = None
    timeout_s: float = Field(default=60.0, gt=0, le=600)

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        # Messages never echo ``value``: a URL with userinfo carries a credential.
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("base_url must be an http(s) URL, e.g. https://llm.example.org/v1")
        if parts.query or parts.fragment or "?" in value or "#" in value:
            raise ValueError("base_url must not contain a query string or fragment")
        try:
            origin_of(value)  # refuses userinfo (``https://user:tok@host``) and an empty host
            has_host = True
        except ValueError:
            has_host = False
        if not has_host:  # raised outside ``except``: no __context__
            raise ValueError(
                "base_url must not contain a username, password or token, and must name a host; "
                "API keys go in Settings → Language models"
            )
        return value.rstrip("/")


class OllamaSettings(LLMEndpointSettings):
    """The Ollama server (local by default; a non-local URL means utterances leave this machine)."""

    base_url: str | None = "http://localhost:11434"
    timeout_s: float = Field(default=120.0, gt=0, le=600)


class LLMQualitySettings(_Section):
    """What the natural-language features send to the model, and how hard they try.

    ``"auto"`` means on for a local endpoint (Ollama, or any loopback ``base_url``) and off for a
    remote provider: these settings send network or project content off the machine.
    ``match_retry`` is the narrow one: after a miss it sends only the ``match_candidates`` closest
    names, so it can be opted into for a remote provider without turning on full ``grounding``.
    """

    assistant_context: bool = True
    assistant_context_max_chars: int = Field(default=16000, ge=0, le=100_000)
    project_context: Literal["auto", "on", "off"] = "auto"
    project_context_max_chars: int = Field(default=4000, ge=0, le=50_000)
    grounding: Literal["auto", "on", "off"] = "auto"
    grounding_max_names: int = Field(default=200, ge=1, le=2000)
    few_shot: bool = False
    few_shot_max: int = Field(default=3, ge=1, le=10)
    max_repairs: int = Field(default=1, ge=0, le=5)
    temperature: float | None = Field(default=0.0, ge=0, le=2)
    match_retry: Literal["auto", "on", "off"] = "auto"
    match_candidates: int = Field(default=5, ge=1, le=20)

    @field_validator("match_retry", mode="before")
    @classmethod
    def _bool_alias(cls, value: Any) -> Any:
        # It was a bool once (and a checkbox maps naturally to one): true -> "on", false -> "off".
        return {True: "on", False: "off"}[value] if isinstance(value, bool) else value


class LLMSettings(_Section):
    """Language-model endpoints and quality knobs. API keys never live in settings (see :mod:`netstead.llm.secrets`)."""

    anthropic: LLMEndpointSettings = Field(default_factory=LLMEndpointSettings)
    openai: LLMEndpointSettings = Field(default_factory=LLMEndpointSettings)
    gemini: LLMEndpointSettings = Field(default_factory=LLMEndpointSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    quality: LLMQualitySettings = Field(default_factory=LLMQualitySettings)

    def is_local(self, provider: str) -> bool:
        """Whether ``provider``'s endpoint is on this machine (``base_url=None`` is the remote official one)."""
        endpoint = getattr(self, provider, None)
        base_url = endpoint.base_url if isinstance(endpoint, LLMEndpointSettings) else None
        return base_url is not None and is_local_url(base_url)

    def match_retry_on(self, provider: str) -> bool:
        """Whether a miss re-prompts ``provider`` with the closest real names (``auto``: local endpoints only)."""
        return self._quality_on(self.quality.match_retry, provider)

    def grounding_on(self, provider: str) -> bool:
        """Whether network vocabulary (street names, route numbers) is sent to ``provider``."""
        return self._quality_on(self.quality.grounding, provider)

    def project_context_on(self, provider: str) -> bool:
        """Whether the project's ``AGENTS.md``/``CLAUDE.md`` is sent to ``provider``."""
        return self._quality_on(self.quality.project_context, provider)

    def _quality_on(self, mode: Literal["auto", "on", "off"], provider: str) -> bool:
        """Resolve an ``"auto"``/``"on"``/``"off"`` quality setting: ``auto`` is on only for a local endpoint."""
        return mode == "on" or (mode == "auto" and self.is_local(provider))


class VizSettings(_Section):
    """Map rendering."""

    basemap: Literal["positron", "esri"] = "positron"


class AppSettings(_Section):
    """The workbench web server."""

    host: str = "127.0.0.1"
    port: int = 8850
    console: bool = False
    approve_above_s: float = Field(default=90.0, ge=0)
    disabled_plugins: list[str] = Field(default_factory=list)  # plugin ids not to load (see netstead.workbench.plugins)


class CredentialSettings(_Section):
    """Names of hosts whose secrets live in keyring. Never the secrets themselves."""

    keyring_hosts: list[str] = Field(default_factory=list)


#: The core Actions the assistant may call by default (Q4 of the P3 plan). Opt-ins (``add_rows``, ``set_setting``,
#: ``build_network``, ``delete_selection``) are added to ``assistant.actions``; ``open_network`` and
#: ``close_network`` are never offered.
DEFAULT_ASSISTANT_ACTIONS: tuple[str, ...] = (
    "select",
    "clear_selection",
    "pin_anchor",
    "save_selection",
    "use_selection",
    "style",
    "filter",
    "navigate",
    "set_active_network",
    "run_validation",
    "edit_cells",
    "delete_rows",
    "undo_edit",
    "save_network",
)


class AssistantSettings(_Section):
    """The natural-language assistant: what it may call, what it remembers, what it may spend.

    It uses the selection provider and model (``select.provider`` / ``select.model``) and the same ``llm.quality``
    privacy gates. ``plugins`` lists plugin ids whose Actions it may call (none by default). ``compact_tools`` offers
    a small model fewer tools per question (``auto``: on for a local endpoint): the read-only ones that apply right
    now, plus edit, delete and undo. It never allows more than ``actions`` / ``plugins`` do.
    """

    actions: list[str] = Field(default_factory=lambda: list(DEFAULT_ASSISTANT_ACTIONS))
    plugins: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=6, ge=1, le=20)
    history_turns: int = Field(default=6, ge=0, le=20)
    max_turns: int = Field(default=50, ge=1, le=500)
    output_token_limit: int = Field(default=1024, ge=128, le=8192)  # per call ("…_tokens" reads as a secret name)
    context_max_chars: int = Field(default=12000, ge=1000, le=100_000)
    remote_token_budget: int = Field(default=200_000, ge=0, le=10_000_000)
    compact_tools: Literal["auto", "on", "off"] = "auto"


class Settings(_Section):
    """All user-tunable netstead settings."""

    io: IOSettings = Field(default_factory=IOSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    osm: OSMSettings = Field(default_factory=OSMSettings)
    overture: OvertureSettings = Field(default_factory=OvertureSettings)
    build: BuildSettings = Field(default_factory=BuildSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    select: SelectSettings = Field(default_factory=SelectSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    viz: VizSettings = Field(default_factory=VizSettings)
    app: AppSettings = Field(default_factory=AppSettings)
    credentials: CredentialSettings = Field(default_factory=CredentialSettings)
    #: One free-form table per Workbench plugin (``[plugins.<id>]``), validated by that plugin's own model.
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("plugins")
    @classmethod
    def _no_plugin_secrets(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        # Free-form tables escape every typed field's checks, so apply SetSetting's credential rules here,
        # to every layer (files and env included). The message names the key, never the value.
        problem = settings_secret_problem(value, "plugins")
        if problem is not None:
            raise ValueError(problem)
        return value


@dataclass(frozen=True)
class LoadedSettings:
    """Resolved settings plus where each leaf value came from."""

    settings: Settings
    sources: dict[str, str]
    user_path: Path
    project_path: Path


def user_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the user config file path (``NETSTEAD_CONFIG_DIR`` overrides the platform default)."""
    env = os.environ if environ is None else environ
    if override := env.get("NETSTEAD_CONFIG_DIR"):
        return Path(override) / "config.toml"
    if sys.platform == "win32" and (appdata := env.get("APPDATA")):
        return Path(appdata) / "netstead" / "config.toml"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "netstead" / "config.toml"


def project_config_path(project_dir: str | Path | None = None) -> Path:
    """Return the project config file path (``netstead.toml`` in ``project_dir``, default cwd)."""
    return Path(project_dir or Path.cwd()) / PROJECT_FILE


def get_value(settings: Settings, key: str) -> Any:
    """Return the JSON-mode value at dotted ``key`` (e.g. ``"app.port"``)."""
    node: Any = settings.model_dump(mode="json")
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise SettingsError(f"unknown setting {key!r}")
        node = node[part]
    return node


def load_settings(
    *,
    project_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> LoadedSettings:
    """Resolve settings from every layer; ``overrides`` maps dotted keys to session values."""
    env = os.environ if environ is None else environ
    user_path, project_path = user_config_path(env), project_config_path(project_dir)
    session_layer: dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        _set_dotted(session_layer, key, value)
    layers = (
        ("user", _read_toml(user_path)),
        ("project", _read_toml(project_path)),
        ("env", _env_layer(env)),
        ("session", session_layer),
    )
    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name, layer in layers:
        _validate(layer, f"{name} layer")
        merged = _merge(merged, layer)
        sources.update(dict.fromkeys(_leaves(layer), name))
    settings = _validate(merged, "merged layers")
    for key in _leaves(settings.model_dump(mode="json")):
        sources.setdefault(key, "default")
    return LoadedSettings(settings=settings, sources=sources, user_path=user_path, project_path=project_path)


def save_setting(
    key: str,
    value: Any,
    *,
    scope: Scope,
    project_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Persist dotted ``key`` into the ``scope`` file; ``value=None`` removes it (back to default)."""
    env = os.environ if environ is None else environ
    path = user_config_path(env) if scope == "user" else project_config_path(project_dir)
    data = _read_toml(path)
    _set_dotted(data, key, value)
    validated = _validate(data, f"{scope} file {path}")
    if value is not None:
        _set_dotted(data, key, get_value(validated, key))  # store the coerced value ("9100" -> 9100)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(data), encoding="utf-8")
    return path


def dumps_toml(data: Mapping[str, Any]) -> str:
    """Serialise nested dicts of scalars/lists to TOML: the subset settings files use."""
    lines: list[str] = []
    _emit_table(lines, data, ())
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------- helpers

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: {exc}") from exc


def _validate(data: Mapping[str, Any], origin: str) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        message = f"invalid settings ({origin}): {_describe(exc)}"
    # Raised outside ``except`` and built without ``input_value``: a rejected value may be a credential,
    # so the error must carry neither it nor a __context__ that does.
    raise SettingsError(message)


def _describe(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    lines = [f"{len(errors)} validation error{'s' if len(errors) != 1 else ''}"]
    lines += [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']} [type={e['type']}]" for e in errors]
    return "\n  ".join(lines)


def _env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for name, raw in environ.items():
        if not name.startswith(ENV_PREFIX) or "__" not in name:
            continue
        value: Any = raw
        if raw.lstrip().startswith(("[", "{")):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SettingsError(f"{name}: invalid JSON value: {exc}") from exc
        _set_dotted(layer, name[len(ENV_PREFIX) :].lower().replace("__", "."), value)
    return layer


def _set_dotted(data: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


def _merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _leaves(data: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            yield from _leaves(value, dotted + ".")
        else:
            yield dotted


def _emit_table(lines: list[str], table: Mapping[str, Any], path: tuple[str, ...]) -> None:
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict) and v is not None]
    tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    if path and (scalars or not tables):
        lines.extend(["", "[" + ".".join(_toml_key(p) for p in path) + "]"])
    lines.extend(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in scalars)
    for key, value in tables:
        _emit_table(lines, value, (*path, key))


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # JSON string escapes are valid TOML basic-string escapes
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise SettingsError(f"cannot write {type(value).__name__} to TOML: {value!r}")
