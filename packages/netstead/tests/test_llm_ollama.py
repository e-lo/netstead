"""Tests for the Ollama /api/chat adapter."""

from dataclasses import replace

import httpx
import pytest
from netstead.llm.errors import ModelNotFound, ProviderUnavailable, ToolsUnsupported
from netstead.llm.providers.ollama import OllamaProvider
from netstead.llm.types import CompletionRequest, Message, Tool

pytestmark = pytest.mark.usefixtures("no_network")

TOOL = Tool("emit", "Emit a thing.", {"type": "object", "properties": {"x": {"type": "integer"}}})
REQUEST = CompletionRequest(
    model="qwen3:8b", messages=(Message("user", "hi"),), system="sys", tools=(TOOL,), force_tool="emit", max_tokens=300
)
REPLY = {
    "model": "qwen3:8b",
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "emit", "arguments": {"x": 3}}}],
    },
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 40,
    "eval_count": 9,
}


def _provider(fake_api, **kw):
    return OllamaProvider(transport=fake_api.transport(), **kw)


def test_request_shape_no_auth_and_parsing(fake_api):
    fake_api.add("POST", "/api/chat", body=REPLY)
    done = _provider(fake_api).complete(REQUEST)
    req = fake_api.requests[0]
    assert str(req.url) == "http://localhost:11434/api/chat" and "authorization" not in req.headers
    assert fake_api.body() == {
        "model": "qwen3:8b",
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        "stream": False,
        "options": {"num_predict": 300},
        "tools": [
            {
                "type": "function",
                "function": {"name": "emit", "description": "Emit a thing.", "parameters": TOOL.input_schema},
            }
        ],
    }
    assert done.tool_calls[0].arguments == {"x": 3}
    assert (done.stop_reason, done.input_tokens, done.output_tokens) == ("stop", 40, 9)


def test_json_mode_uses_native_format(fake_api):
    fake_api.add("POST", "/api/chat", body={"message": {"role": "assistant", "content": '{"x": 1}'}, "done": True})
    json_request = CompletionRequest(model="gemma2", messages=(Message("user", "hi"),), json_schema=TOOL.input_schema)
    done = _provider(fake_api).complete(json_request)
    assert fake_api.body()["format"] == TOOL.input_schema and "tools" not in fake_api.body()
    assert done.text == '{"x": 1}'


def test_string_arguments_are_parsed(fake_api):
    reply = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "emit", "arguments": '{"x": 2}'}}],
        }
    }
    fake_api.add("POST", "/api/chat", body=reply)
    assert _provider(fake_api).complete(REQUEST).tool_calls[0].arguments == {"x": 2}


def test_model_without_tools_raises_tools_unsupported(fake_api):
    body = {"error": "registry.ollama.ai/library/gemma2:latest does not support tools"}
    fake_api.add("POST", "/api/chat", status=400, body=body)
    with pytest.raises(ToolsUnsupported, match="does not support tool calling"):
        _provider(fake_api).complete(REQUEST)


def test_missing_model_is_model_not_found(fake_api):
    fake_api.add("POST", "/api/chat", status=404, body={"error": 'model "qwen3:8b" not found, try pulling it first'})
    with pytest.raises(ModelNotFound, match="try pulling it first"):
        _provider(fake_api).complete(REQUEST)


def test_list_models_then_server_down(fake_api):
    fake_api.add("GET", "/api/tags", body={"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:3b"}]})
    fake_api.add("GET", "/api/tags", raises=httpx.ConnectError("refused"))
    provider = _provider(fake_api)
    assert provider.list_models() == ["qwen3:8b", "llama3.2:3b"]
    with pytest.raises(ProviderUnavailable, match="could not reach Ollama at http://localhost:11434"):
        provider.list_models()


def test_thinking_model_sends_think_false(fake_api):
    fake_api.add("POST", "/api/chat", body=REPLY)
    _provider(fake_api, thinking_models=frozenset({"qwen3:8b"})).complete(REQUEST)
    assert fake_api.body()["think"] is False


def test_unknown_model_sends_no_think_key(fake_api):
    """A model not in thinking_models (the default: none) gets no "think" key at all -- Ollama
    errors with "does not support thinking" if sent one for a non-thinking model."""
    fake_api.add("POST", "/api/chat", body=REPLY)
    _provider(fake_api).complete(REQUEST)
    assert "think" not in fake_api.body()
    fake_api.add("POST", "/api/chat", body=REPLY)
    _provider(fake_api, thinking_models=frozenset({"qwen3:4b"})).complete(REQUEST)  # REQUEST.model is qwen3:8b
    assert "think" not in fake_api.body()


def test_thinking_field_in_reply_is_ignored(fake_api):
    """Defence in depth: only content and tool_calls count, even if the model emits "thinking"."""
    reply = {
        "message": {"role": "assistant", "content": "the answer", "thinking": "let me ponder this at length..."},
        "done": True,
    }
    fake_api.add("POST", "/api/chat", body=reply)
    done = _provider(fake_api, thinking_models=frozenset({"qwen3:8b"})).complete(REQUEST)
    assert done.text == "the answer"
    assert "ponder" not in done.text


def test_context_joins_the_system_turn_and_temperature_goes_in_options(fake_api):
    fake_api.add("POST", "/api/chat", body=REPLY)
    _provider(fake_api).complete(replace(REQUEST, context="GUIDE", temperature=0.0))
    body = fake_api.body()
    assert body["messages"][0] == {"role": "system", "content": "GUIDE\n\nsys"}
    assert body["options"] == {"num_predict": 300, "temperature": 0.0}


def test_the_multi_tool_json_envelope_uses_native_format(fake_api):
    from netstead.llm.structured import ENVELOPE_SCHEMA, request_tool_calls

    content = '{"reply": "ok", "calls": [{"tool": "emit", "arguments": {"x": 2}}]}'
    fake_api.add("POST", "/api/chat", body={**REPLY, "message": {"role": "assistant", "content": content}})
    out = request_tool_calls(
        _provider(fake_api), model="qwen3:8b", tools=(TOOL,), messages=(Message("user", "hi"),), json_mode=True
    )
    body = fake_api.body()
    assert body["format"] == ENVELOPE_SCHEMA and "tools" not in body
    assert out.mode == "json" and out.calls[0].arguments == {"x": 2} and out.text == "ok"
