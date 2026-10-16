"""Provider-neutral request/response types for tool-calling LLMs.

Every adapter in :mod:`netstead.llm.providers` maps a :class:`CompletionRequest` onto its
provider's wire format and maps the reply back to a :class:`Completion`. Nothing here
knows about any one provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = ["Completion", "CompletionRequest", "LLMProvider", "Message", "Tool", "ToolCall"]


@dataclass(frozen=True)
class Tool:
    """A tool the model may call: a name, a description, and a plain JSON-schema input."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One plain-text conversation turn."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ToolCall:
    """A tool call the model made: the tool name and its parsed JSON-object arguments."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class CompletionRequest:
    """One provider-neutral completion request.

    ``context`` is the stable, cacheable part of the system prompt (shipped guide, project notes,
    vocabulary); ``system`` is the per-call rest. Anthropic marks ``context`` for prompt caching;
    the other adapters send :meth:`full_system`. ``force_tool`` names a tool the model must call
    (providers that cannot force one ignore it). ``json_schema`` asks for a bare JSON reply instead
    of a tool call: adapters with a native JSON mode (Ollama's ``format``) use it, the others rely on
    the instructions in ``system``. ``temperature=None`` leaves the provider's default.
    """

    model: str
    messages: tuple[Message, ...]
    system: str = ""
    context: str = ""
    tools: tuple[Tool, ...] = ()
    force_tool: str | None = None
    json_schema: dict[str, Any] | None = None
    max_tokens: int = 1024
    temperature: float | None = None

    def full_system(self) -> str:
        """``context`` then ``system``, as one system prompt (for adapters without prompt caching)."""
        return "\n\n".join(part for part in (self.context, self.system) if part)


@dataclass(frozen=True)
class Completion:
    """A provider-neutral reply: tool calls and/or text, plus token counts when reported."""

    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Tool calls whose arguments were not a JSON object, as ``(position in the reply, 1-based, tool name)``. They
    #: are left out of ``tool_calls`` (and their raw arguments are in ``text``, for the single-tool path to salvage).
    bad_tool_calls: tuple[tuple[int, str], ...] = ()


@runtime_checkable
class LLMProvider(Protocol):
    """The one interface every adapter implements."""

    name: str
    label: str

    def complete(self, request: CompletionRequest) -> Completion:
        """Run one completion; raise :class:`~netstead.llm.errors.LLMError` on any provider failure."""
        ...

    def list_models(self) -> list[str]:
        """Model ids this endpoint serves for this key (an authenticated call that spends no tokens)."""
        ...
