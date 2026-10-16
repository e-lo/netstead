"""Get one validated tool call out of any provider, with a bounded repair loop.

PRD §15(e): when the model's output fails validation, re-prompt with the error instead of
surfacing a raw failure. Models without tool calling get the same tool as a "reply with
JSON only" instruction (JSON mode) on the *same* provider and model, never another provider.
Provider failures (:class:`~netstead.llm.errors.LLMError`: keys, rate limits, timeouts) are
never retried here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from .errors import ToolsUnsupported
from .types import Completion, CompletionRequest, LLMProvider, Message, Tool, ToolCall

__all__ = [
    "ENVELOPE_SCHEMA",
    "JSON_MODE_INSTRUCTIONS",
    "REPAIR_PROMPT",
    "StructuredOutputError",
    "ToolCallsResult",
    "ToolResult",
    "request_tool_call",
    "request_tool_calls",
]

Mode = Literal["tools", "json"]

#: Appended to the system prompt in JSON mode.
JSON_MODE_INSTRUCTIONS = (
    "Reply with ONLY one JSON object, with no prose and no code fences, that is valid input for the tool "
    "{name!r} ({description}). Its JSON schema:\n{schema}"
)
#: The user turn that asks the model to fix its previous reply.
REPAIR_PROMPT = "Your previous reply was not usable: {error}\nTry again: {how}"


class StructuredOutputError(ValueError):
    """The model never produced a valid tool call within the repair budget."""


@dataclass(frozen=True)
class ToolResult:
    """Validated tool arguments, the mode that produced them, and how many calls it took."""

    arguments: dict[str, Any]
    mode: Mode
    attempts: int


def request_tool_call(
    provider: LLMProvider,
    *,
    model: str,
    tool: Tool,
    user: str,
    system: str = "",
    context: str = "",
    validate: Callable[[dict[str, Any]], object] | None = None,
    json_mode: bool = False,
    max_repairs: int = 1,
    max_tokens: int = 1024,
    temperature: float | None = None,
) -> ToolResult:
    """Ask ``provider`` to call ``tool`` for ``user``; validate, and repair until valid or out of budget.

    Args:
        provider: Any :class:`~netstead.llm.types.LLMProvider`.
        model: Model id.
        tool: The tool the model must call. Its ``input_schema`` is enforced with jsonschema.
        user: The user's text.
        system: The per-call part of the system prompt.
        context: The stable, cacheable part of the system prompt (sent first).
        validate: Extra check on the arguments; raise ``ValueError`` to trigger a repair.
        json_mode: Start in JSON mode (for models known to lack tool calling).
        max_repairs: Extra calls allowed after an invalid reply.
        max_tokens: Output token cap per call.
        temperature: Sampling temperature; ``None`` keeps the provider's default.

    Returns:
        The validated arguments, the mode used, and the number of calls made.

    Raises:
        StructuredOutputError: Still invalid after ``max_repairs`` repairs.
    """
    base = CompletionRequest(
        model=model, messages=(), system=system, context=context, max_tokens=max_tokens, temperature=temperature
    )
    mode: Mode = "json" if json_mode else "tools"
    messages = [Message("user", user)]
    error = ""
    for attempt in range(1, max_repairs + 2):
        completion, mode = _complete(provider, replace(base, messages=tuple(messages)), tool, mode)
        try:
            arguments = _arguments(completion, tool, mode)
            _check_schema(arguments, tool.input_schema)
            if validate is not None:
                validate(arguments)
        except ValueError as exc:
            error = str(exc)
            how = (
                f"call {tool.name} with corrected arguments."
                if mode == "tools"
                else "reply with only the corrected JSON object."
            )
            messages += [
                Message("assistant", _echo(completion)),
                Message("user", REPAIR_PROMPT.format(error=error, how=how)),
            ]
            continue
        return ToolResult(arguments, mode, attempt)
    raise StructuredOutputError(f"the model's reply was still invalid after {max_repairs + 1} attempts: {error}")


def _complete(provider: LLMProvider, base: CompletionRequest, tool: Tool, mode: Mode) -> tuple[Completion, Mode]:
    if mode == "tools":
        try:
            return provider.complete(replace(base, tools=(tool,), force_tool=tool.name)), "tools"
        except ToolsUnsupported:
            pass  # same provider, same model, JSON mode instead: a change of mode, never of provider
    instructions = JSON_MODE_INSTRUCTIONS.format(
        name=tool.name, description=tool.description, schema=json.dumps(tool.input_schema)
    )
    # The JSON instructions go in the per-call part, so the cacheable ``context`` prefix stays identical.
    system = f"{base.system}\n\n{instructions}".strip()
    return provider.complete(replace(base, system=system, json_schema=tool.input_schema)), "json"


def _arguments(completion: Completion, tool: Tool, mode: Mode) -> dict[str, Any]:
    for call in completion.tool_calls:
        if call.name == tool.name:
            return _drop_nulls(dict(call.arguments))
    if completion.tool_calls:
        raise ValueError(f"the model called {completion.tool_calls[0].name!r}, not {tool.name!r}")
    parsed = _json_object(completion.text)
    if parsed is None:
        raise ValueError(
            f"the model did not call {tool.name!r}" if mode == "tools" else "the reply was not a JSON object"
        )
    return _drop_nulls(parsed)


def _json_object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _drop_nulls(value: Any) -> Any:
    """Treat ``null`` as "absent": several providers emit explicit nulls for unused optional fields."""
    if isinstance(value, dict):
        return {key: _drop_nulls(item) for key, item in value.items() if item is not None}
    return value


def _check_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator  # the [nl] extra; lazy so netstead.llm imports without it
    from jsonschema.exceptions import best_match

    error = best_match(Draft202012Validator(schema).iter_errors(arguments))
    if error is not None:
        where = "/".join(str(part) for part in error.absolute_path) or "(top level)"
        raise ValueError(f"{where}: {error.message}")


def _echo(completion: Completion) -> str:
    if completion.tool_calls:
        call = completion.tool_calls[0]
        return f"(called {call.name} with {json.dumps(call.arguments)})"
    return completion.text[:2000] or "(empty reply)"


#: JSON mode's reply shape for several tools: a sentence for the user and the calls. ``arguments`` stays an open object
#: (each call is checked against its own tool afterwards), so a native JSON constraint (Ollama's ``format``) needs no
#: ``anyOf``.
ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
                "required": ["tool", "arguments"],
            },
        },
    },
    "required": ["calls"],
}
#: Appended to the system prompt in JSON mode for several tools.
ENVELOPE_INSTRUCTIONS = (
    "Reply with ONLY one JSON object, with no prose and no code fences: "
    '{{"reply": "<one short sentence for the user>", '
    '"calls": [{{"tool": "<tool name>", "arguments": {{...}}}}]}}. Use an empty calls list to only answer. '
    "The tools, with their JSON schemas:\n{tools}"
)


@dataclass(frozen=True)
class ToolCallsResult:
    """Validated tool calls (in order), the model's text, the mode, the number of calls made, and token use.

    ``input_tokens`` / ``output_tokens`` sum every call, or are ``None`` when any call didn't report them.
    """

    calls: tuple[ToolCall, ...]
    text: str
    mode: Mode
    attempts: int
    input_tokens: int | None
    output_tokens: int | None


def request_tool_calls(
    provider: LLMProvider,
    *,
    model: str,
    tools: tuple[Tool, ...],
    messages: tuple[Message, ...],
    system: str = "",
    context: str = "",
    validate: Callable[[tuple[ToolCall, ...]], object] | None = None,
    json_mode: bool = False,
    max_repairs: int = 1,
    max_tokens: int = 1024,
    max_calls: int = 8,
    temperature: float | None = None,
) -> ToolCallsResult:
    """Offer ``tools`` (none forced) for the conversation ``messages``; validate every call; repair until valid.

    A reply may be text only (no calls). Each call must name one of ``tools`` and match its schema; then
    ``validate(calls)`` (raise ``ValueError`` to repair) sees the whole list. At most ``max_calls`` calls per reply.
    A model without tool calling gets the JSON envelope (:data:`ENVELOPE_SCHEMA`) on the same provider and model.
    Provider failures (:class:`~netstead.llm.errors.LLMError`) are never retried.

    Args:
        provider: Any :class:`~netstead.llm.types.LLMProvider`.
        model: Model id.
        tools: The tools offered; the model may call any of them, several times, or none.
        messages: The conversation so far, ending with the user's turn.
        system: The per-call part of the system prompt.
        context: The stable, cacheable part of the system prompt (sent first).
        validate: Extra check on the whole list of calls; raise ``ValueError`` to trigger a repair.
        json_mode: Start in JSON mode (for models known to lack tool calling).
        max_repairs: Extra calls allowed after an invalid reply.
        max_tokens: Output token cap per call.
        max_calls: The most tool calls one reply may carry.
        temperature: Sampling temperature; ``None`` keeps the provider's default.

    Returns:
        The validated calls, the reply text, the mode used, the number of calls made, and the summed token use.

    Raises:
        StructuredOutputError: Still invalid after ``max_repairs`` repairs.
    """
    by_name = {tool.name: tool for tool in tools}
    base = CompletionRequest(
        model=model, messages=(), system=system, context=context, max_tokens=max_tokens, temperature=temperature
    )
    mode: Mode = "json" if json_mode else "tools"
    history = list(messages)
    usage: list[tuple[int | None, int | None]] = []
    error = ""
    for attempt in range(1, max_repairs + 2):
        completion, mode = _complete_many(provider, replace(base, messages=tuple(history)), tools, mode)
        usage.append((completion.input_tokens, completion.output_tokens))
        try:
            calls, text = _many(completion, mode)
            _check_calls(calls, text, by_name, max_calls, completion.bad_tool_calls if mode == "tools" else ())
            if validate is not None:
                validate(calls)
        except ValueError as exc:
            error = str(exc)
            how = (
                "call the tools again with corrected arguments."
                if mode == "tools"
                else "reply with only the corrected JSON object."
            )
            history += [
                Message("assistant", _echo_many(completion)),
                Message("user", REPAIR_PROMPT.format(error=error, how=how)),
            ]
            continue
        return ToolCallsResult(calls, text, mode, attempt, *_sum_usage(usage))
    raise StructuredOutputError(f"the model's reply was still invalid after {max_repairs + 1} attempts: {error}")


def _check_calls(
    calls: tuple[ToolCall, ...],
    text: str,
    by_name: dict[str, Tool],
    max_calls: int,
    bad: tuple[tuple[int, str], ...] = (),
) -> None:
    """Raise ``ValueError`` (naming the call) for anything a repair should fix.

    That is: a call whose arguments weren't a JSON object, an empty reply, too many calls, an unknown tool, or
    arguments that don't match the tool's schema.
    """
    for position, name in bad:  # a truncated call must never become the reply text, nor vanish from a plan
        raise ValueError(f"call {position} ({name}): arguments were not a JSON object")
    if not calls and not text.strip():
        raise ValueError("the reply was empty: call a tool or answer in one sentence")
    if len(calls) > max_calls:
        raise ValueError(f"at most {max_calls} tool calls per reply")
    for i, call in enumerate(calls, start=1):
        tool = by_name.get(call.name)
        if tool is None:
            raise ValueError(f"call {i}: there is no tool named {call.name!r}; use one of {sorted(by_name)}")
        try:
            _check_schema(call.arguments, tool.input_schema)
        except ValueError as exc:
            raise ValueError(f"call {i} ({call.name}): {exc}") from None


def _complete_many(
    provider: LLMProvider, base: CompletionRequest, tools: tuple[Tool, ...], mode: Mode
) -> tuple[Completion, Mode]:
    if mode == "tools":
        try:
            return provider.complete(replace(base, tools=tools, force_tool=None)), "tools"
        except ToolsUnsupported:
            pass  # same provider, same model: the JSON envelope instead
    listing = json.dumps([{"name": t.name, "description": t.description, "schema": t.input_schema} for t in tools])
    # As in request_tool_call, the instructions go in the per-call part so the cacheable ``context`` stays identical.
    system = f"{base.system}\n\n{ENVELOPE_INSTRUCTIONS.format(tools=listing)}".strip()
    return provider.complete(replace(base, system=system, json_schema=ENVELOPE_SCHEMA)), "json"


def _many(completion: Completion, mode: Mode) -> tuple[tuple[ToolCall, ...], str]:
    if mode == "tools":
        calls = tuple(ToolCall(c.name, _drop_nulls(dict(c.arguments))) for c in completion.tool_calls)
        return calls, completion.text
    parsed = _json_object(completion.text)
    if parsed is None:
        raise ValueError("the reply was not a JSON object")
    raw = parsed.get("calls")
    if not isinstance(raw, list) or not all(
        isinstance(c, dict) and isinstance(c.get("tool"), str) and isinstance(c.get("arguments"), dict) for c in raw
    ):
        raise ValueError('"calls" must be a list of {"tool": name, "arguments": {...}}')
    reply = parsed.get("reply")
    calls = tuple(ToolCall(c["tool"], _drop_nulls(c["arguments"])) for c in raw)
    return calls, reply if isinstance(reply, str) else ""


def _echo_many(completion: Completion) -> str:
    if completion.tool_calls or completion.bad_tool_calls:
        echoed = [f"(called {c.name} with {json.dumps(c.arguments)})" for c in completion.tool_calls]
        echoed += [
            f"(called {name} with arguments that were not a JSON object)" for _, name in completion.bad_tool_calls
        ]
        return "; ".join(echoed)[:2000]
    return completion.text[:2000] or "(empty reply)"


def _sum_usage(usage: list[tuple[int | None, int | None]]) -> tuple[int | None, int | None]:
    """Total input and output tokens; ``None`` for a side when any call didn't report it.

    >>> _sum_usage([(10, 5), (3, 2)]), _sum_usage([(10, 5), (None, 2)])
    ((13, 7), (None, 7))
    """
    ins, outs = [u[0] for u in usage], [u[1] for u in usage]
    total_in = None if any(n is None for n in ins) else sum(n for n in ins if n is not None)
    total_out = None if any(n is None for n in outs) else sum(n for n in outs if n is not None)
    return total_in, total_out
