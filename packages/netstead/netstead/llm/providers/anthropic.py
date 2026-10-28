"""Anthropic Messages API adapter (tool use, prompt caching), hand-rolled over httpx."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..types import Completion, CompletionRequest, ToolCall
from ._base import HTTPProvider

if TYPE_CHECKING:
    from ..catalog import ModelInfo

__all__ = ["API_VERSION", "AnthropicProvider"]

#: Messages API version header. Bump deliberately, together with a contract-fixture re-record.
API_VERSION = "2023-06-01"


class AnthropicProvider(HTTPProvider):
    """``POST {base_url}/v1/messages`` with ``tools`` and, where the model allows it, a forced ``tool_choice``."""

    name = "anthropic"
    label = "Anthropic"
    DEFAULT_BASE_URL = "https://api.anthropic.com"

    def __init__(self, *, models: Mapping[str, ModelInfo] | None = None, **kwargs: Any) -> None:
        """``models`` are the catalog entries, to learn which models reject ``temperature`` or a forced tool.

        A model not in it (one the user typed) gets the classic request: temperature and a forced tool as asked.
        """
        super().__init__(**kwargs)
        self._models = dict(models or {})

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": API_VERSION}

    def complete(self, request: CompletionRequest) -> Completion:
        """Run one Messages call; tool-use blocks become :class:`~netstead.llm.types.ToolCall`."""
        info = self._models.get(request.model)
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        system = request.system
        forced = request.force_tool if request.tools else None
        if forced and info is not None and not info.forced_tools:
            # The model rejects a forced tool_choice: offer the tools freely and ask for this one in words.
            system = "\n\n".join(p for p in (system, f"Reply by calling the {forced} tool.") if p)
            forced = None
        if request.context:
            # The stable prefix gets a cache breakpoint (Anthropic prompt caching). Below the model's
            # minimum cacheable length the marker is simply ignored: no error, no saving.
            blocks = [{"type": "text", "text": request.context, "cache_control": {"type": "ephemeral"}}]
            if system:
                blocks.append({"type": "text", "text": system})
            body["system"] = blocks
        elif system:
            body["system"] = system
        if request.temperature is not None and (info is None or info.sampling):
            body["temperature"] = request.temperature
        if info is not None and info.effort:
            body["output_config"] = {"effort": info.effort}
        if request.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools
            ]
            if forced:
                body["tool_choice"] = {"type": "tool", "name": forced}
        data = self._call("POST", "/v1/messages", body)
        try:
            blocks = data["content"]
            calls = tuple(ToolCall(b["name"], dict(b["input"])) for b in blocks if b.get("type") == "tool_use")
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            usage = data.get("usage") or {}
        except (KeyError, TypeError, AttributeError) as exc:
            raise self._bad_shape(exc) from None
        return Completion(
            tool_calls=calls,
            text=text,
            stop_reason=data.get("stop_reason"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    def list_models(self) -> list[str]:
        """Model ids this key can use (``GET /v1/models``)."""
        data = self._call("GET", "/v1/models", params={"limit": 1000})
        try:
            return [m["id"] for m in data["data"]]
        except (KeyError, TypeError) as exc:
            raise self._bad_shape(exc) from None
