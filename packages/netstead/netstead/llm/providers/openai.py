"""OpenAI Chat Completions adapter; any OpenAI-compatible endpoint works through ``base_url``.

Chat Completions (not the Responses API) is the surface every "OpenAI-compatible" server
speaks: vLLM, LM Studio, OpenRouter, Groq, and both Gemini's and Ollama's compatibility layers.
The helpers here are shared with the Ollama adapter, which uses the same tool shape.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import BadResponse
from ..types import Completion, CompletionRequest, ToolCall
from ._base import HTTPProvider

__all__ = ["OpenAIProvider", "chat_messages", "function_tools", "parse_function_calls"]

#: A reasoning model's token cap includes its hidden reasoning tokens, so the default 1024-token
#: budget (:class:`~netstead.llm.types.CompletionRequest`) often leaves nothing for the visible
#: reply: HTTP 200, empty content, ``finish_reason="length"``. Raised to this floor for those
#: models only; a caller asking for more already gets what it asked for (see ``complete``).
_REASONING_MIN_MAX_TOKENS = 8192

#: Reasoning-tier model id prefixes that reject ``max_tokens`` (use ``max_completion_tokens`` instead)
#: and reject a non-default ``temperature``. All confirmed as reasoning models on their respective
#: pages under https://developers.openai.com/api/docs/models/ (checked 2026-10-05): o1, o3, o4-mini,
#: gpt-5, and the GPT-6 generation (gpt-6-luna, gpt-6.1-sol, gpt-6-astra all say "Is this a reasoning
#: model? Yes" / list reasoning-token support).
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6")

#: Reasoning model id prefixes whose docs confirm Chat Completions' function/tool calling only works
#: with ``reasoning_effort`` (Chat Completions key) / ``reasoning.effort`` (Responses key) set to
#: ``"none"``. Checked 2026-10-05 against the model pages at
#: https://developers.openai.com/api/docs/models/<id>:
#:   - gpt-6-luna: "reasoning.effort supports none, low, medium (default), high, xhigh, and max." and
#:     "Chat Completions supports function calling only with reasoning_effort set to none." -> send it.
#:   - gpt-6.1-sol: "reasoning.effort supports low, medium (default), high, xhigh, and max." (no
#:     "none") and "Chat Completions is supported without tool calling." -> tool calls aren't available
#:     on this model via Chat Completions at all, with or without reasoning_effort, so "none" would just
#:     be rejected; don't send it.
#:   - gpt-6-astra: "reasoning.effort supports low, medium, high, xhigh, and max." (no "none");
#:     function_calling is listed as a supported Chat Completions feature with no reasoning_effort
#:     caveat, so tool calls work at the default effort -> don't send it.
#:   - gpt-5: "reasoning.effort supports minimal, low, medium, and high." (no "none"); function_calling
#:     is listed with no reasoning_effort caveat -> don't send it.
#:   - o1, o3, o4-mini: no reasoning_effort values documented on the model page at all (these predate
#:     GPT-5/6's effort tiers); nothing suggests "none" is accepted -> don't send it.
#: Only gpt-6-luna is covered today; add a prefix here only once a model's own docs state the same
#: "none"-for-tool-calling requirement.
_FORCE_NONE_REASONING_EFFORT_PREFIXES = ("gpt-6-luna",)


def _is_reasoning(model: str) -> bool:
    """Whether ``model`` is one of OpenAI's reasoning-tier models (see ``_REASONING_PREFIXES``)."""
    return model.startswith(_REASONING_PREFIXES)


def _max_tokens_key(model: str) -> str:
    """Which body key caps output length for ``model``: reasoning-tier models use the newer name."""
    return "max_completion_tokens" if _is_reasoning(model) else "max_tokens"


def _max_tokens_value(model: str, requested: int) -> int:
    """``requested``, raised to ``_REASONING_MIN_MAX_TOKENS`` for a reasoning-tier model."""
    return max(requested, _REASONING_MIN_MAX_TOKENS) if _is_reasoning(model) else requested


def _reasoning_effort(model: str, *, tools: bool) -> str | None:
    """The ``reasoning_effort`` to send for ``model``, or ``None`` to leave the provider's own default.

    A reasoning-tier model defaults to effort "medium", which spends part of the output budget on
    hidden reasoning before any visible reply; "low" is a documented value for the o-series/gpt-5/
    gpt-6 families that leaves more of the budget for the reply. gpt-6-luna is the one documented
    exception: Chat Completions' function calling only works there at effort "none" (see
    ``_FORCE_NONE_REASONING_EFFORT_PREFIXES``), so a forced tool call gets "none" instead of "low";
    without tools nothing requires a specific value for it, so its own default stands.
    """
    if model.startswith(_FORCE_NONE_REASONING_EFFORT_PREFIXES):
        return "none" if tools else None
    return "low" if _is_reasoning(model) else None


def chat_messages(request: CompletionRequest) -> list[dict[str, str]]:
    """``messages`` with the system prompt (context + system) as the leading ``system`` turn."""
    text = request.full_system()
    system = [{"role": "system", "content": text}] if text else []
    return system + [{"role": m.role, "content": m.content} for m in request.messages]


def function_tools(request: CompletionRequest) -> list[dict[str, Any]]:
    """``tools`` in the OpenAI function shape (Ollama accepts the same)."""
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in request.tools
    ]


def parse_function_calls(
    raw_calls: list[dict[str, Any]] | None,
) -> tuple[tuple[ToolCall, ...], str, tuple[tuple[int, str], ...]]:
    """Tool calls from an OpenAI/Ollama message; any arguments that weren't a JSON object, as text; and which.

    Unparseable arguments are handed back as text instead of raising, so the structured-output repair loop can show
    the model what went wrong. The third value names them, ``(position in the reply, 1-based, tool name)``, so a
    reply with several calls can't lose one quietly.

    >>> parse_function_calls([{"function": {"name": "a", "arguments": '{"n": 1}'}},
    ...                       {"function": {"name": "b", "arguments": '{"n": '}}])
    ((ToolCall(name='a', arguments={'n': 1}),), '{"n": ', ((2, 'b'),))
    """
    calls: list[ToolCall] = []
    leftovers: list[str] = []
    bad: list[tuple[int, str]] = []
    for position, call in enumerate(raw_calls or [], start=1):
        function = call["function"]
        raw = function.get("arguments")
        arguments = raw
        if isinstance(raw, str):
            try:
                arguments = json.loads(raw or "{}")
            except json.JSONDecodeError:
                arguments = None
        if isinstance(arguments, dict):
            calls.append(ToolCall(function["name"], arguments))
        else:
            leftovers.append(str(raw))
            bad.append((position, str(function.get("name", ""))))
    return tuple(calls), "\n".join(leftovers), tuple(bad)


class OpenAIProvider(HTTPProvider):
    """``POST {base_url}/chat/completions`` with function tools and a forced ``tool_choice``.

    Sends the output-length cap under ``max_tokens``, or ``max_completion_tokens`` for
    reasoning-tier model ids (``o1``/``o3``/``o4``/``gpt-5``/``gpt-6``), which reject ``max_tokens``;
    for those models the cap is also raised to ``_REASONING_MIN_MAX_TOKENS`` so hidden reasoning
    tokens can't crowd out the whole reply. Those same models also reject a non-default
    ``temperature``, so it's omitted for them, and get ``reasoning_effort="low"`` to leave more of
    that budget for the reply -- except gpt-6-luna, whose docs require ``"none"`` instead, and only
    when a tool is offered (see ``_reasoning_effort``). An empty reply with ``finish_reason="length"``
    raises a clear error naming the output-budget cause, instead of looking like a malformed reply.
    """

    name = "openai"
    label = "OpenAI"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._key}"}

    def complete(self, request: CompletionRequest) -> Completion:
        """Run one chat completion; function calls become :class:`~netstead.llm.types.ToolCall`."""
        body: dict[str, Any] = {"model": request.model, "messages": chat_messages(request)}
        if request.max_tokens:
            body[_max_tokens_key(request.model)] = _max_tokens_value(request.model, request.max_tokens)
        if request.tools:
            body["tools"] = function_tools(request)
            if request.force_tool:
                body["tool_choice"] = {"type": "function", "function": {"name": request.force_tool}}
        effort = _reasoning_effort(request.model, tools=bool(request.tools))
        if effort is not None:
            body["reasoning_effort"] = effort
        if request.temperature is not None and not _is_reasoning(request.model):
            body["temperature"] = request.temperature
        data = self._call("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            calls, leftover, bad = parse_function_calls(message.get("tool_calls"))
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise self._bad_shape(exc) from None
        text = "\n".join(part for part in (message.get("content") or "", leftover) if part)
        if not text and not calls and choice.get("finish_reason") == "length":
            raise BadResponse(
                self.name,
                f"{self.label}: {request.model} ran out of output tokens before it could reply "
                "(finish_reason=length with no content); raise max_tokens and try again.",
            )
        return Completion(
            tool_calls=calls,
            text=text,
            stop_reason=choice.get("finish_reason"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            bad_tool_calls=bad,
        )

    def list_models(self) -> list[str]:
        """Model ids this key can use (``GET /models``)."""
        data = self._call("GET", "/models")
        try:
            return [m["id"] for m in data["data"]]
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise self._bad_shape(exc) from None
