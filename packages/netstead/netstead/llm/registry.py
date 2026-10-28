"""ProviderRegistry: adapters built from settings + stored keys, and what is usable right now.

The Workbench routes, the session's parser and the ``netstead llm`` CLI all go through this.
It hands out key *status* (configured / source), never key values. The only code that reads
a key is :meth:`ProviderRegistry.provider`, which hands it straight to an adapter.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Literal

from .catalog import Catalog, ProviderInfo, load_catalog
from .errors import LLMError
from .providers import ADAPTERS, OllamaProvider
from .providers.ollama import PULL_CHUNK_TIMEOUT_S, PullProgress
from .secrets import KeyringLike, KeySlot, SecretStore, origin_of
from .types import LLMProvider

#: Local models as Ollama lists them: id -> capabilities (``None`` when the server doesn't report them).
Installed = dict[str, frozenset[str] | None]

if TYPE_CHECKING:
    # netstead.config imports netstead.llm.secrets, so this module (reached from netstead.llm's own
    # __init__) cannot import netstead.config at module scope without a circular import; the
    # functions below that need it (is_local_url, load_settings, user_config_path) import it
    # locally instead, by which point netstead.config has always finished loading.
    from netstead.config import LLMSettings, Settings

__all__ = [
    "PROBE_CACHE_TTL_S",
    "PROBE_TIMEOUT_S",
    "ProviderRegistry",
    "build_registry",
    "default_registry",
    "is_local_url",
]

#: Status probes of a local server (Ollama) must not stall the UI.
PROBE_TIMEOUT_S = 1.5
#: How long a local-provider probe (``list_models``, success or failure) is reused before
#: re-probing. A down Ollama is otherwise re-probed, at ``PROBE_TIMEOUT_S`` each, on every
#: ``status()``/``models()`` call made while rendering the picker or the Settings panel.
PROBE_CACHE_TTL_S = 5.0


def is_local_url(url: str) -> bool:
    """Whether ``url`` points at this machine (re-exported from :mod:`netstead.config`, the one implementation)."""
    from netstead.config import is_local_url as _is_local_url  # deferred: see the TYPE_CHECKING import note above

    return _is_local_url(url)


class ProviderRegistry:
    """Build provider adapters with their keys, and report status, models and connection tests.

    Local-provider probes (:meth:`status`, :meth:`models`) are cached per instance for
    :data:`PROBE_CACHE_TTL_S`; there is no cross-instance or module-level cache, so rebuilding
    the registry from fresh settings (as every settings change does -- see
    :func:`build_registry`) starts with an empty one. :meth:`test` always bypasses and clears it.
    """

    def __init__(self, settings: LLMSettings, secrets: SecretStore, catalog: Catalog, *, transport: Any = None) -> None:
        """Bind endpoint settings, the key store, the catalog, and an optional ``httpx`` transport (tests)."""
        self.settings = settings
        self.secrets = secrets
        self.catalog = catalog
        self._transport = transport
        self._probe_lock = threading.Lock()
        #: ``(provider, base_url) -> (monotonic timestamp, installed models -> capabilities, or the LLMError raised)``.
        self._probe_cache: dict[tuple[str, str], tuple[float, Installed | LLMError]] = {}

    def names(self) -> list[str]:
        """Providers with both a catalog entry and an adapter, in catalog order."""
        return [name for name in self.catalog.names() if name in ADAPTERS]

    def base_url(self, name: str) -> str:
        """The effective endpoint for ``name``: the settings override, else the catalog's official URL."""
        override = getattr(self.settings, name).base_url
        return (override or self.catalog[name].base_url).rstrip("/")

    def slot(self, name: str) -> KeySlot:
        """The key slot for ``name``'s *effective* endpoint; a non-official origin gets its own slot."""
        here = origin_of(self.base_url(name))
        return KeySlot(name, None if here == origin_of(self.catalog[name].base_url) else here)

    def is_local(self, name: str) -> bool:
        """Whether ``name``'s effective endpoint is on this machine (nothing it is sent leaves it)."""
        return self.settings.is_local(name)

    def grounding_on(self, name: str) -> bool:
        """Whether network vocabulary (street names, route numbers) is sent to ``name``."""
        return self.settings.grounding_on(name)

    def project_context_on(self, name: str) -> bool:
        """Whether the project's notes (``NETSTEAD.md`` or a ``## netstead`` section) are sent to ``name``."""
        return self.settings.project_context_on(name)

    def match_retry_on(self, name: str) -> bool:
        """Whether a miss re-prompts ``name`` with the closest real names (``auto``: local endpoints only)."""
        return self.settings.match_retry_on(name)

    def disclosure(self, name: str) -> list[str]:
        """What a selection sends to ``name``, in words, for the privacy note (follows the quality settings)."""
        quality = self.settings.quality
        items = ["your utterance", "the selection tool's schema (GMNS field names such as lanes)"]
        if quality.assistant_context:
            items.append("the GMNS assistant guide that ships with netstead")
        if self.grounding_on(name):
            items.append(f"up to {quality.grounding_max_names} street names and route numbers from the active network")
        if self.project_context_on(name):
            items.append(
                "your project notes (NETSTEAD.md, or the ## netstead section of AGENTS.md/CLAUDE.md, "
                f"up to {quality.project_context_max_chars} characters)"
            )
        if quality.few_shot:
            items.append(
                f"up to {quality.few_shot_max} earlier selections on this network from this session "
                "(utterance and result, which may include street names from the network)"
            )
        return items

    def _probe(self, name: str) -> list[str]:
        """Installed model ids for local provider ``name`` (see :meth:`_probe_installed`)."""
        return list(self._probe_installed(name))

    def _probe_installed(self, name: str) -> Installed:
        """Installed models and their capabilities for local ``name``, cached while fresh (success or failure).

        The whole check-call-store sequence runs under :attr:`_probe_lock`, so concurrent
        callers for the same (or a different) provider never both pay for a live probe at once;
        the second simply finds the first's result already cached.
        """
        key = (name, self.base_url(name))
        with self._probe_lock:
            cached = self._probe_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < PROBE_CACHE_TTL_S:
                outcome = cached[1]
                if isinstance(outcome, LLMError):
                    raise outcome
                return outcome
            try:
                installed = self.provider(name, timeout_s=PROBE_TIMEOUT_S).installed_models()
            except LLMError as exc:
                self._probe_cache[key] = (time.monotonic(), exc)
                raise
            self._probe_cache[key] = (time.monotonic(), installed)
            return installed

    def invalidate_probe_cache(self, name: str | None = None) -> None:
        """Forget cached local-provider probes for ``name`` (or every provider), so the next call re-probes."""
        with self._probe_lock:
            if name is None:
                self._probe_cache.clear()
            else:
                for key in [k for k in self._probe_cache if k[0] == name]:
                    del self._probe_cache[key]

    def resolve_model(self, name: str, model: str | None = None) -> str:
        """The model a selection on ``name`` really uses: ``model`` if given, else a default.

        An explicit ``model`` is never replaced. A remote provider's default is the catalog's. For a
        local provider (Ollama) whose catalog default isn't installed, the default is the first
        installed catalog model with ``tools = true`` (catalog order), else the first installed model
        whose Ollama capabilities include ``"tools"``. With none of those (or Ollama unreachable) it
        stays the catalog default, so the call fails with the usual "pull a model" hint.
        """
        if model:
            return model
        info = self.catalog[name]
        if info.kind != "local":
            return info.default_model
        try:
            installed = self._probe_installed(name)
        except LLMError:
            return info.default_model
        return _local_default(info, installed)

    def provider(self, name: str, *, timeout_s: float | None = None) -> LLMProvider:
        """An adapter for ``name`` holding its key; raises :class:`~netstead.llm.errors.MissingKey` if there is none."""
        info = self.catalog[name]
        key = "" if info.kind == "local" else self.secrets.get(self.slot(name), info.label)
        kwargs: dict[str, Any] = dict(
            api_key=key,
            base_url=self.base_url(name),
            timeout_s=timeout_s or getattr(self.settings, name).timeout_s,
            transport=self._transport,
        )
        if name == "ollama":
            # Catalog-driven, like make_parser's tools lookup: the adapter never imports the
            # catalog itself, so the set of thinking-capable model ids is resolved here and
            # threaded in, to decide whether "think": false is sent (see OllamaProvider.complete).
            kwargs["thinking_models"] = frozenset(m.id for m in info.models if m.thinking)
        elif name == "anthropic":
            # Likewise: which catalog models reject temperature or a forced tool, and the effort each gets.
            kwargs["models"] = {m.id: m for m in info.models}
        return ADAPTERS[name](**kwargs)

    def status(self) -> list[dict[str, Any]]:
        """One row per provider: configured / source / usable / error / models. Never a key value."""
        return [self._status(name) for name in self.names()]

    def _status(self, name: str) -> dict[str, Any]:
        info = self.catalog[name]
        base = self.base_url(name)
        row: dict[str, Any] = {
            "provider": name,
            "label": info.label,
            "kind": info.kind,
            "base_url": base,
            "local": is_local_url(base),
            "default_model": info.default_model,
            # The model used when select.model is unset: the catalog default, or for Ollama an installed
            # stand-in when that default isn't installed (see resolve_model). None until a probe succeeds.
            "model": info.default_model if info.kind == "remote" else None,
            "key_env": list(info.key_env),
            "sends": self.disclosure(name),
            "configured": False,
            "source": None,
            "usable": False,
            "error": None,
            "models": None,
        }
        if info.kind == "local":
            try:
                installed = self._probe_installed(name)
            except LLMError as exc:
                row["error"] = str(exc)
                return row
            row.update(configured=True, usable=bool(installed), models=len(installed))
            if not installed:
                row["error"] = f"{info.label} is running but has no models; run: ollama pull {info.default_model}"
                return row
            row["model"] = _local_default(info, installed)
            if row["model"] not in installed:
                row["error"] = (
                    f"no installed model supports tool calling and {info.default_model} is not installed; "
                    f"run: ollama pull {info.default_model} (or pick an installed model)"
                )
            return row
        row.update(self.secrets.status(self.slot(name)))
        row["usable"] = row["configured"]
        return row

    def models(self, name: str) -> list[dict[str, Any]]:
        """Models to offer: the catalog for remote providers; the installed models for local ones."""
        info = self.catalog[name]
        if info.kind == "remote":
            return [m.to_dict() for m in info.models]
        installed = self._probe(name)
        return [{**_describe(info, model_id), "installed": True} for model_id in installed]

    def test(self, name: str, model: str | None = None) -> dict[str, Any]:
        """An authenticated, token-free call (list models). Failures are reported in the result, not raised.

        A "Test connection" click means "check right now": it always re-probes, bypassing
        (and clearing) the local-provider cache so the next :meth:`status`/:meth:`models` call
        reflects what this call just found, not a stale probe from seconds before.
        """
        info = self.catalog[name]
        self.invalidate_probe_cache(name)
        started = time.perf_counter()
        try:
            adapter = self.provider(name)
            installed = adapter.installed_models() if info.kind == "local" else None
            served = list(installed) if installed is not None else adapter.list_models()
        except LLMError as exc:
            return {"provider": name, "ok": False, "error_type": type(exc).__name__, "message": str(exc)}
        result: dict[str, Any] = {
            "provider": name,
            "ok": True,
            "error_type": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "models_served": len(served),
            "catalog_missing": [m.id for m in info.models if m.id not in served] if info.kind == "remote" else [],
            "message": f"{info.label}: connected ({len(served)} models available).",
        }
        if model and model not in served:
            result.update(
                ok=False,
                error_type="ModelNotFound",
                message=f"{info.label}: connected, but model {model!r} is not available here.",
            )
        elif installed is not None and not model:
            # No model named: check the one a selection would use (see resolve_model).
            used = result["model"] = _local_default(info, installed)
            if used in served:
                stand_in = f" ({info.default_model} is not installed)" if used != info.default_model else ""
                result["message"] += f" Selections use {used}{stand_in}."
            else:
                result.update(
                    ok=False,
                    error_type="ModelNotFound",
                    message=(
                        f"{info.label}: connected, but {used} is not installed and no installed model supports "
                        f"tool calling. Run: netstead llm pull {used} (or choose an installed model with --model)."
                    ),
                )
        return result

    def pull_choices(self) -> list[dict[str, Any]]:
        """The catalog's Ollama models, with their approximate download size, for a Pull picker."""
        info = self.catalog["ollama"]
        return [{**m.to_dict(), "size_gb": m.size_gb} for m in info.models]

    def pull(self, model: str, *, chunk_timeout_s: float = PULL_CHUNK_TIMEOUT_S) -> Iterator[PullProgress]:
        """Stream an Ollama pull of ``model`` (see :meth:`OllamaProvider.pull`).

        However it ends (success, error, or the caller closing the generator to cancel), the
        Ollama probe cache is dropped, so the next :meth:`status`/:meth:`models` sees the new model.
        """
        provider = OllamaProvider(base_url=self.base_url("ollama"), transport=self._transport)
        try:
            yield from provider.pull(model, chunk_timeout_s=chunk_timeout_s)
        finally:
            self.invalidate_probe_cache("ollama")


def _local_default(info: ProviderInfo, installed: Installed) -> str:
    """The default for a local provider: its catalog default, or an installed tool-capable stand-in.

    The stand-in (only when the catalog default isn't installed) is the first installed catalog model
    with ``tools = true``, in catalog order, else the first installed model whose capabilities include
    ``"tools"``. With neither, the catalog default (so the caller reports the usual "pull" hint).
    """
    if info.default_model in installed:
        return info.default_model
    known = next((m.id for m in info.models if m.tools and m.id in installed), None)
    reported = next((m for m, caps in installed.items() if caps and "tools" in caps), None)
    return known or reported or info.default_model


def _describe(info: ProviderInfo, model_id: str) -> dict[str, Any]:
    known = info.model(model_id)
    return known.to_dict() if known else {"id": model_id, "label": model_id, "tier": None, "tools": None}


def build_registry(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    keyring: KeyringLike | Literal["auto"] | None = "auto",
    transport: Any = None,
) -> ProviderRegistry:
    """A registry for ``settings``: the catalog (plus user overlay) and key store live in the user config dir."""
    from netstead.config import user_config_path  # deferred: see the TYPE_CHECKING import note above

    env = os.environ if environ is None else environ
    user_dir = user_config_path(env).parent
    catalog = load_catalog(user_dir)
    secrets = SecretStore(
        environ=env,
        env_names={name: info.key_env for name, info in catalog.providers.items()},
        keyring=keyring,
    )
    return ProviderRegistry(settings.llm, secrets, catalog, transport=transport)


def default_registry() -> ProviderRegistry:
    """A registry from the current process's layered settings and environment."""
    from netstead.config import load_settings  # deferred: see the TYPE_CHECKING import note above

    return build_registry(load_settings().settings)
