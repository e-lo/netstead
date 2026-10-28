"""The maintained LLM provider/model catalog (``models.toml``), plus an optional user overlay.

The overlay (``<user config dir>/llm_models.toml``, same shape) may add or relabel models
and change a provider's ``label``/``default_model``. It cannot change ``base_url``,
``key_env`` or ``kind``: those decide where keys are sent, so they stay in packaged data.
A provider without an adapter in :mod:`netstead.llm.providers` is ignored.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

__all__ = ["CATALOG_OVERLAY", "Catalog", "ModelInfo", "ProviderInfo", "load_catalog"]

#: File name of the user overlay, next to the user ``config.toml``.
CATALOG_OVERLAY = "llm_models.toml"
#: Provider keys an overlay may set; anything else in the overlay is ignored.
_OVERLAY_KEYS = frozenset({"label", "default_model"})
#: Providers the packaged catalog may define (one adapter each).
_KNOWN = ("anthropic", "openai", "gemini", "ollama")

Tier = Literal["fast", "balanced", "best"]
_TIERS = frozenset({"fast", "balanced", "best"})


@dataclass(frozen=True)
class ModelInfo:
    """One catalog model."""

    id: str
    label: str
    tier: Tier | None = None
    tools: bool = True
    #: Whether the model emits hidden "thinking" output (Ollama only); the Ollama adapter uses this
    #: to send ``"think": false`` so reasoning can't exhaust the output budget.
    thinking: bool = False
    #: Approximate download size in GB (Ollama models only, for the pull confirmation); ``None`` = unknown.
    size_gb: float | None = None
    #: Whether the model accepts ``temperature`` (Anthropic only; the newest models reject it).
    sampling: bool = True
    #: Whether the model accepts a forced tool choice (Anthropic only).
    forced_tools: bool = True
    #: ``output_config.effort`` to send (Anthropic only); ``None`` leaves the model's default.
    effort: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy for the API."""
        return {"id": self.id, "label": self.label, "tier": self.tier, "tools": self.tools}


@dataclass(frozen=True)
class ProviderInfo:
    """One catalog provider."""

    name: str
    label: str
    kind: Literal["remote", "local"]
    base_url: str
    key_env: tuple[str, ...]
    default_model: str
    models: tuple[ModelInfo, ...]

    def model(self, model_id: str) -> ModelInfo | None:
        """The catalog entry for ``model_id``, if there is one."""
        return next((m for m in self.models if m.id == model_id), None)


@dataclass(frozen=True)
class Catalog:
    """Providers keyed by name, in file order."""

    providers: dict[str, ProviderInfo]

    def __getitem__(self, name: str) -> ProviderInfo:
        """The provider called ``name`` (the ``KeyError`` lists the known ones)."""
        try:
            return self.providers[name]
        except KeyError:
            raise KeyError(f"unknown LLM provider {name!r}; known: {', '.join(self.providers)}") from None

    def names(self) -> list[str]:
        """Provider names in catalog order."""
        return list(self.providers)


def load_catalog(user_dir: str | Path | None = None) -> Catalog:
    """Load the packaged catalog, overlaid with ``<user_dir>/llm_models.toml`` when that file exists.

    A malformed overlay (bad TOML, a provider body or model entry of the wrong shape, a model
    with no ``id``) raises :class:`ValueError` naming the overlay path rather than being skipped:
    the overlay is the one place a user edits this catalog, so a mistake there should surface
    where it was made (``netstead llm`` / the Settings panel) instead of silently not applying.
    """
    raw = tomllib.loads(resources.files("netstead.llm").joinpath("models.toml").read_text(encoding="utf-8"))
    if user_dir is not None and (overlay_path := Path(user_dir) / CATALOG_OVERLAY).is_file():
        raw = _apply_overlay(raw, _load_overlay(overlay_path), overlay_path)
    return Catalog({name: _provider(name, body) for name, body in raw.items() if name in _KNOWN})


def _load_overlay(path: Path) -> dict[str, Any]:
    """Parse the overlay TOML file, wrapping a parse error as ``ValueError`` naming ``path``."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"LLM catalog overlay {path}: invalid TOML: {exc}") from None


def _apply_overlay(base: dict[str, Any], overlay: dict[str, Any], path: Path) -> dict[str, Any]:
    out = {name: dict(body) for name, body in base.items()}
    for name, body in overlay.items():
        if name not in out:
            continue  # no adapter could serve a provider the packaged catalog doesn't define
        if not isinstance(body, dict):
            raise ValueError(
                f"LLM catalog overlay {path}: provider {name!r} must be a table, not {type(body).__name__}"
            )
        merged = out[name]
        merged.update({key: value for key, value in body.items() if key in _OVERLAY_KEYS})
        models = {m["id"]: dict(m) for m in merged.get("models", [])}
        overlay_models = body.get("models", [])
        if not isinstance(overlay_models, list):
            raise ValueError(
                f"LLM catalog overlay {path}: provider {name!r} models must be a list, "
                f"not {type(overlay_models).__name__}"
            )
        for model in overlay_models:
            if not isinstance(model, dict):
                raise ValueError(f"LLM catalog overlay {path}: provider {name!r} has a model entry that isn't a table")
            if "id" not in model:
                raise ValueError(f"LLM catalog overlay {path}: every model needs an id (provider {name!r})")
            models[model["id"]] = {**models.get(model["id"], {}), **model}
        merged["models"] = list(models.values())
    return out


def _provider(name: str, body: dict[str, Any]) -> ProviderInfo:
    try:
        kind = body["kind"]
        if kind not in ("remote", "local"):
            raise ValueError(f"kind must be 'remote' or 'local', not {kind!r}")
        return ProviderInfo(
            name=name,
            label=str(body["label"]),
            kind=kind,
            base_url=str(body["base_url"]).rstrip("/"),
            key_env=tuple(body.get("key_env", ())),
            default_model=str(body["default_model"]),
            models=tuple(
                ModelInfo(
                    id=str(m["id"]),
                    label=str(m.get("label", m["id"])),
                    tier=m.get("tier") if m.get("tier") in _TIERS else None,
                    tools=bool(m.get("tools", True)),
                    thinking=bool(m.get("thinking", False)),
                    size_gb=float(m["size_gb"]) if m.get("size_gb") is not None else None,
                    sampling=bool(m.get("sampling", True)),
                    forced_tools=bool(m.get("forced_tools", True)),
                    effort=str(m["effort"]) if m.get("effort") is not None else None,
                )
                for m in body.get("models", [])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"LLM catalog: provider {name!r} has a missing or bad field: {exc}") from None
