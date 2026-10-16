"""Tests for the OpenAI Chat Completions adapter."""

from dataclasses import replace

import pytest
from netstead.llm.errors import BadRequest, BadResponse, InvalidKey, ToolsUnsupported
from netstead.llm.providers.openai import OpenAIProvider
from netstead.llm.types import CompletionRequest, Message, Tool

pytestmark = pytest.mark.usefixtures("no_network")

KEY = "sk-proj-TESTKEYabcdefghijklmnopqrstuvwx"
TOOL = Tool("emit", "Emit a thing.", {"type": "object", "properties": {"x": {"type": "integer"}}})
REQUEST = CompletionRequest(
    model="gpt-4.1-mini",
    messages=(Message("user", "hi"), Message("assistant", "prev"), Message("user", "again")),
    system="sys",
    tools=(TOOL,),
    force_tool="emit",
)


def _reply(arguments: str, content=None):
    call = {"id": "c1", "type": "function", "function": {"name": "emit", "arguments": arguments}}
    message = {"role": "assistant", "content": content, "tool_calls": [call]}
    return {
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
    }


def _provider(fake_api, **kw):
    return OpenAIProvider(api_key=KEY, transport=fake_api.transport(), **kw)


def test_request_shape_and_parsing(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply('{"x": 3}'))
    done = _provider(fake_api).complete(REQUEST)
    req = fake_api.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert fake_api.body() == {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "prev"},
            {"role": "user", "content": "again"},
        ],
        "max_tokens": 1024,
        "tools": [
            {
                "type": "function",
                "function": {"name": "emit", "description": "Emit a thing.", "parameters": TOOL.input_schema},
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "emit"}},
    }
    assert done.tool_calls[0].arguments == {"x": 3}
    assert (done.stop_reason, done.input_tokens, done.output_tokens) == ("tool_calls", 20, 5)


def test_reasoning_tier_model_uses_max_completion_tokens(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, model="o3-mini"))
    body = fake_api.body()
    # Raised from the request's 1024 to the reasoning floor: that cap includes hidden reasoning
    # tokens, so 1024 often leaves nothing for the visible reply.
    assert body["max_completion_tokens"] == 8192 and "max_tokens" not in body


def test_reasoning_tier_model_keeps_a_larger_requested_budget(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, model="o3-mini", max_tokens=20000))
    assert fake_api.body()["max_completion_tokens"] == 20000


def test_reasoning_tier_model_omits_temperature(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, model="o3-mini", temperature=0.2))
    assert "temperature" not in fake_api.body()


def test_gpt6_luna_forced_tool_sends_none_reasoning_effort_and_no_temperature(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply('{"x": 3}'))
    _provider(fake_api).complete(replace(REQUEST, model="gpt-6-luna", temperature=0.2))
    body = fake_api.body()
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == 8192 and "max_tokens" not in body
    assert "temperature" not in body


def test_gpt6_luna_without_tools_sends_no_reasoning_effort(fake_api):
    """Luna's behaviour away from tool calling is unchanged: no tools means nothing forces a
    specific effort, so none is sent and its own default ("medium") applies."""
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, model="gpt-6-luna", tools=()))
    assert "reasoning_effort" not in fake_api.body()


def test_other_reasoning_models_get_low_reasoning_effort(fake_api):
    """gpt-6.1-sol and gpt-6-astra (and the rest of the reasoning tier, other than gpt-6-luna) get
    reasoning_effort="low" so hidden reasoning doesn't crowd out the visible reply, with or
    without tools -- gpt-6.1-sol in particular is only ever called without tools (JSON mode)."""
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, model="gpt-6.1-sol", tools=()))
    assert fake_api.body()["reasoning_effort"] == "low"

    fake_api.add("POST", "/v1/chat/completions", body=_reply('{"x": 3}'))
    _provider(fake_api).complete(replace(REQUEST, model="gpt-6-astra"))
    assert fake_api.body()["reasoning_effort"] == "low"


def test_gpt5_forced_tool_gets_low_reasoning_effort(fake_api):
    """gpt-5's docs list no "none" reasoning_effort value, so "none" is never sent for this model;
    it still gets "low", like the rest of the reasoning tier other than gpt-6-luna."""
    fake_api.add("POST", "/v1/chat/completions", body=_reply('{"x": 3}'))
    _provider(fake_api).complete(replace(REQUEST, model="gpt-5"))
    assert fake_api.body()["reasoning_effort"] == "low"


def test_empty_content_with_finish_reason_length_raises_a_clear_error(fake_api):
    message = {"role": "assistant", "content": None}
    reply = {"choices": [{"index": 0, "finish_reason": "length", "message": message}], "usage": {}}
    fake_api.add("POST", "/v1/chat/completions", body=reply)
    with pytest.raises(BadResponse, match="ran out of output tokens"):
        _provider(fake_api).complete(replace(REQUEST, model="gpt-6-astra", tools=()))


def test_finish_reason_length_with_tool_call_present_does_not_raise(fake_api):
    """finish_reason="length" alongside an actual tool call is a normal reply, not the empty-budget
    failure: only an empty reply (no content, no tool calls) with that finish_reason raises."""
    call = {"id": "c1", "type": "function", "function": {"name": "emit", "arguments": '{"x": 3}'}}
    message = {"role": "assistant", "content": None, "tool_calls": [call]}
    reply = {"choices": [{"index": 0, "finish_reason": "length", "message": message}], "usage": {}}
    fake_api.add("POST", "/v1/chat/completions", body=reply)
    done = _provider(fake_api).complete(replace(REQUEST, model="gpt-6-astra"))
    assert done.tool_calls[0].arguments == {"x": 3}


def test_compatible_endpoint_model_still_sends_temperature_and_max_tokens(fake_api):
    """A non-reasoning model id served through an OpenAI-compatible endpoint (e.g. vLLM) is
    unaffected by the reasoning-tier gating: plain max_tokens/temperature, no reasoning_effort."""
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api, base_url="http://localhost:1234/v1").complete(
        replace(REQUEST, model="meta-llama/Llama-3-8b-instruct", temperature=0.2)
    )
    body = fake_api.body()
    assert body["max_tokens"] == 1024 and "max_completion_tokens" not in body
    assert body["temperature"] == 0.2
    assert "reasoning_effort" not in body


def test_unparseable_arguments_come_back_as_text(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply('{"x": 3'))
    done = _provider(fake_api).complete(REQUEST)
    assert done.tool_calls == () and done.text == '{"x": 3'


def test_compatible_endpoint_via_base_url(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api, base_url="http://localhost:1234/v1/").complete(REQUEST)
    assert str(fake_api.requests[0].url) == "http://localhost:1234/v1/chat/completions"


def test_list_models(fake_api):
    fake_api.add("GET", "/v1/models", body={"object": "list", "data": [{"id": "gpt-4.1-mini"}, {"id": "gpt-4.1"}]})
    assert _provider(fake_api).list_models() == ["gpt-4.1-mini", "gpt-4.1"]


def test_invalid_key_message_omits_provider_detail(fake_api):
    body = {"error": {"message": "Incorrect API key provided: sk-proj-****uvwx."}}
    fake_api.add("POST", "/v1/chat/completions", status=401, body=body)
    with pytest.raises(InvalidKey) as info:
        _provider(fake_api).complete(REQUEST)
    assert "Incorrect" not in str(info.value)
    assert str(info.value).startswith("OpenAI rejected the API key (HTTP 401)")


def test_model_without_tools_raises_tools_unsupported(fake_api):
    """gpt-6.1-sol's docs say Chat Completions is supported without tool calling; a 400 phrased
    that way should map to ToolsUnsupported so the structured-output repair loop falls back to
    JSON mode on the same provider and model, the same way it already does for Ollama."""
    body = {"error": {"message": "This model does not support function calling."}}
    fake_api.add("POST", "/v1/chat/completions", status=400, body=body)
    with pytest.raises(ToolsUnsupported, match="does not support tool calling"):
        _provider(fake_api).complete(replace(REQUEST, model="gpt-6.1-sol"))


def test_unrelated_400_is_not_tools_unsupported(fake_api):
    body = {"error": {"message": "Invalid value for 'temperature': must be between 0 and 2."}}
    fake_api.add("POST", "/v1/chat/completions", status=400, body=body)
    with pytest.raises(BadRequest):
        _provider(fake_api).complete(REQUEST)


def test_context_leads_the_system_turn_and_temperature_is_sent(fake_api):
    fake_api.add("POST", "/v1/chat/completions", body=_reply("{}"))
    _provider(fake_api).complete(replace(REQUEST, context="GUIDE", temperature=0.2))
    body = fake_api.body()
    assert body["messages"][0] == {"role": "system", "content": "GUIDE\n\nsys"} and body["temperature"] == 0.2


def test_unforced_tools_send_no_tool_choice_and_every_call_comes_back(fake_api):
    other = Tool("name", "Name it.", {"type": "object", "properties": {"s": {"type": "string"}}})
    calls = [
        {"id": "c1", "type": "function", "function": {"name": "emit", "arguments": '{"x": 1}'}},
        {"id": "c2", "type": "function", "function": {"name": "name", "arguments": '{"s": "a"}'}},
    ]
    message = {"role": "assistant", "content": "Done.", "tool_calls": calls}
    fake_api.add(
        "POST", "/v1/chat/completions", body={"choices": [{"finish_reason": "tool_calls", "message": message}]}
    )
    done = _provider(fake_api).complete(replace(REQUEST, tools=(TOOL, other), force_tool=None))
    body = fake_api.body()
    assert "tool_choice" not in body and [t["function"]["name"] for t in body["tools"]] == ["emit", "name"]
    assert [c.name for c in done.tool_calls] == ["emit", "name"] and done.text == "Done."
