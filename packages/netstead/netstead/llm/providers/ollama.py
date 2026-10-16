"""Ollama ``/api/chat`` adapter: local models (e.g. Qwen), no key.

A model without tool support answers HTTP 400 "... does not support tools", which
:mod:`netstead.llm._http` maps to :class:`~netstead.llm.errors.ToolsUnsupported`. Then
:mod:`netstead.llm.structured` retries the same model in JSON mode, where ``json_schema``
becomes Ollama's native ``format`` constraint.

:meth:`OllamaProvider.pull` streams ``POST /api/pull`` (newline-delimited JSON progress lines:
``status``, and ``digest``/``total``/``completed`` while a layer downloads; an ``error`` line if
it fails mid-stream). :class:`PullTracker` folds those lines into one overall fraction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .._http import _status_error as status_error  # the one status->error mapping
from ..errors import BadResponse, LLMError, ModelNotFound, ProviderTimeout, ProviderUnavailable
from ..secrets import origin_of
from ..types import Completion, CompletionRequest
from ._base import HTTPProvider
from .openai import chat_messages, function_tools, parse_function_calls

__all__ = [
    "MODEL_NAME_MAX",
    "PULL_CHUNK_TIMEOUT_S",
    "SETUP_DOCS_URL",
    "OllamaProvider",
    "PullProgress",
    "PullTracker",
    "setup_hint",
    "valid_model_name",
]

#: The published setup guide (``docs/cookbook/local-llm-ollama.md``).
SETUP_DOCS_URL = "https://e-lo.github.io/netstead/netstead/cookbook/local-llm-ollama/"
#: Longest model name a pull accepts.
MODEL_NAME_MAX = 128
#: ``[namespace/]model[:tag]``. At most one ``/`` and no ``.`` before it: Ollama reads a name with a
#: dotted first segment (``host.example/ns/model``) as a registry *host*, so a pull could be aimed
#: at any server. One plain namespace (``user/model``) stays on Ollama's own library.
_MODEL_NAME = re.compile(r"(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?")
#: A pull streams for minutes; this bounds the wait for each *chunk* (connect, then every read), not the total.
PULL_CHUNK_TIMEOUT_S = 60.0


def valid_model_name(name: str) -> bool:
    """Whether ``name`` is a plain Ollama library name (``qwen3:4b``, ``user/model:tag``), never a URL or host."""
    return isinstance(name, str) and len(name) <= MODEL_NAME_MAX and _MODEL_NAME.fullmatch(name) is not None


def setup_hint(base_url: str) -> str:
    """What to do when Ollama isn't answering at ``base_url``: install, start, then the guide."""
    return (
        f"Ollama is not reachable at {base_url}. Install it from https://ollama.com/download, then start it "
        "(open the Ollama app, or run: ollama serve). "
        f"Guide: {SETUP_DOCS_URL} (docs/cookbook/local-llm-ollama.md)"
    )


@dataclass(frozen=True)
class PullProgress:
    """One line of ``/api/pull`` progress."""

    status: str
    digest: str | None = None
    total: int | None = None
    completed: int | None = None


@dataclass
class PullTracker:
    """Fold per-layer pull progress into overall bytes and a fraction.

    Ollama reports each layer (digest) separately; the overall figure is the sum over the layers
    seen so far. The model weights are by far the largest layer, so the fraction is close to
    honest even though small layers announced later briefly lower it.
    """

    layers: dict[str, tuple[int, int]] = field(default_factory=dict)
    status: str = "starting"

    def update(self, event: PullProgress) -> float | None:
        """Record ``event``; return the overall fraction once any layer's size is known."""
        self.status = event.status
        if event.digest and event.total:
            self.layers[event.digest] = (min(event.completed or 0, event.total), event.total)
        return self.fraction

    @property
    def total(self) -> int:
        """Bytes announced so far."""
        return sum(t for _, t in self.layers.values())

    @property
    def completed(self) -> int:
        """Bytes downloaded so far."""
        return sum(c for c, _ in self.layers.values())

    @property
    def fraction(self) -> float | None:
        """``completed / total`` (``None`` before any size is known; ``1.0`` after ``success``)."""
        if self.status == "success":
            return 1.0
        return self.completed / self.total if self.total else None

    @property
    def stage(self) -> str:
        """A short human label: ``downloading`` for layer lines (their status is a raw digest), else the status."""
        layer = self.status.startswith("pulling ") and self.status != "pulling manifest"
        return "downloading" if layer else self.status


class OllamaProvider(HTTPProvider):
    """``POST {base_url}/api/chat`` (non-streaming). Ollama cannot force a tool; the repair loop covers that."""

    name = "ollama"
    label = "Ollama"
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, *, thinking_models: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        """Like :class:`HTTPProvider`, plus the catalog's thinking-capable model ids (see :mod:`netstead.llm.registry`).

        A model in ``thinking_models`` gets ``"think": false`` sent, so its hidden reasoning can't
        exhaust ``num_predict``. A model not in the set (unknown to the catalog, or non-thinking)
        gets no ``think`` key at all: Ollama errors with "does not support thinking" if sent one.
        """
        super().__init__(**kwargs)
        self._thinking_models = thinking_models

    def complete(self, request: CompletionRequest) -> Completion:
        """Run one chat turn; ``message.tool_calls`` become :class:`~netstead.llm.types.ToolCall`.

        Only ``message.content`` and ``message.tool_calls`` are read; any ``message.thinking`` the
        model emits is ignored (never mixed into ``text``, so JSON-mode parsing never sees it).
        """
        body: dict[str, Any] = {
            "model": request.model,
            "messages": chat_messages(request),
            "stream": False,
            "options": {"num_predict": request.max_tokens},
        }
        if request.model in self._thinking_models:
            body["think"] = False
        if request.tools:
            body["tools"] = function_tools(request)
        if request.json_schema is not None:
            body["format"] = request.json_schema
        if request.temperature is not None:
            body["options"]["temperature"] = request.temperature
        data = self._call("POST", "/api/chat", body)
        try:
            message = data["message"]
            calls, leftover, bad = parse_function_calls(message.get("tool_calls"))
        except (KeyError, TypeError, AttributeError) as exc:
            raise self._bad_shape(exc) from None
        text = "\n".join(part for part in (message.get("content") or "", leftover) if part)
        return Completion(
            tool_calls=calls,
            text=text,
            stop_reason=data.get("done_reason"),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            bad_tool_calls=bad,
        )

    def list_models(self) -> list[str]:
        """Installed model tags (``GET /api/tags``)."""
        return list(self.installed_models())

    def installed_models(self) -> dict[str, frozenset[str] | None]:
        """Installed model tags (``GET /api/tags``), in Ollama's order, each with its capabilities.

        Capabilities (e.g. ``{"completion", "tools"}``) are ``None`` when this Ollama doesn't report
        them (older servers list models without a ``capabilities`` field).
        """
        data = self._call("GET", "/api/tags")
        try:
            return {m["name"]: _capabilities(m.get("capabilities")) for m in data["models"]}
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise self._bad_shape(exc) from None

    def pull(self, model: str, *, chunk_timeout_s: float = PULL_CHUNK_TIMEOUT_S) -> Iterator[PullProgress]:
        """Stream ``POST /api/pull`` for ``model``, yielding each progress line until ``success``.

        ``chunk_timeout_s`` bounds connecting and each read, never the whole download. Closing the
        generator (a cancel) closes the connection. Raises :class:`~netstead.llm.errors.LLMError`
        subclasses: :class:`ProviderUnavailable` if Ollama can't be reached, :class:`BadResponse`
        if a chunk can't be decoded (``httpx.DecodingError``, e.g. a corrupted transfer encoding),
        :class:`ModelNotFound` for an unknown name, and the server's message for any other failure.
        """
        if not valid_model_name(model):
            raise ModelNotFound(self.name, f"{self.label}: {model!r} is not a valid model name (e.g. qwen3:4b)")
        import httpx  # the [nl] extra; request_json already reported its absence on any earlier call

        url = f"{self.base_url}/api/pull"
        timeout = httpx.Timeout(chunk_timeout_s)
        try:
            with (
                httpx.Client(timeout=timeout, transport=self._transport) as client,
                client.stream("POST", url, json={"model": model, "stream": True}) as response,
            ):
                if response.status_code >= 400:
                    response.read()
                    raise status_error(response, provider=self.name, label=self.label, secret="")
                for line in response.iter_lines():
                    if line.strip():
                        event = self._pull_line(line, model)
                        yield event
                        if event.status == "success":
                            return
        except httpx.TimeoutException:
            raise ProviderTimeout(
                self.name, f"{self.label} sent nothing for {chunk_timeout_s:g} s while pulling {model}; try again."
            ) from None
        except httpx.DecodingError as exc:
            raise BadResponse(
                self.name, f"{self.label} sent an undecodable response while pulling {model} ({type(exc).__name__})."
            ) from None
        except httpx.TransportError as exc:
            raise ProviderUnavailable(
                self.name, f"could not reach {self.label} at {origin_of(url)} ({type(exc).__name__})."
            ) from None
        raise BadResponse(self.name, f"{self.label}: the pull of {model} ended before it reported success.")

    def _pull_line(self, line: str, model: str) -> PullProgress:
        try:
            data = json.loads(line)
        except ValueError:
            raise BadResponse(self.name, f"{self.label} sent a pull progress line that is not JSON.") from None
        if not isinstance(data, dict):
            raise BadResponse(self.name, f"{self.label} sent an unexpected pull progress line.")
        if "error" in data:  # mid-stream failure: the HTTP status was already 200
            raise _pull_error(self.name, self.label, model, str(data["error"]))
        return PullProgress(
            status=str(data.get("status", "")),
            digest=data.get("digest") or None,
            total=_int_or_none(data.get("total")),
            completed=_int_or_none(data.get("completed")),
        )


def _pull_error(provider: str, label: str, model: str, detail: str) -> LLMError:
    detail = " ".join(detail.split())[:300]
    if "file does not exist" in detail or "not found" in detail:
        return ModelNotFound(provider, f"{label}: no model called {model!r} in the Ollama library ({detail}).")
    return ProviderUnavailable(provider, f"{label} could not pull {model}: {detail}")


def _capabilities(value: Any) -> frozenset[str] | None:
    return frozenset(str(c) for c in value) if isinstance(value, list) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
