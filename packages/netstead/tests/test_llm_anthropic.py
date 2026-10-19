"""Tests for the Anthropic adapter and the shared HTTP call path (netstead.llm._http)."""

from dataclasses import replace

import httpx
import pytest
from netstead.llm.errors import (
    BadRequest,
    BadResponse,
    InvalidKey,
    ModelNotFound,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from netstead.llm.providers.anthropic import API_VERSION, AnthropicProvider
from netstead.llm.types import CompletionRequest, Message, Tool

pytestmark = pytest.mark.usefixtures("no_network")

KEY = "sk-ant-api03-TESTKEYabcdefghijklmnopqrst"
TOOL = Tool("emit", "Emit a thing.", {"type": "object", "properties": {"x": {"type": "integer"}}})
REQUEST = CompletionRequest(
    model="claude-sonnet-5",
    messages=(Message("user", "hi"),),
    system="sys",
    tools=(TOOL,),
    force_tool="emit",
    max_tokens=256,
)
REPLY = {
    "content": [{"type": "text", "text": "Sure."}, {"type": "tool_use", "id": "t1", "name": "emit", "input": {"x": 3}}],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 11, "output_tokens": 7},
}


def _provider(fake_api, **kw):
    return AnthropicProvider(api_key=KEY, transport=fake_api.transport(), **kw)


def test_request_shape_and_reply_parsing(fake_api):
    fake_api.add("POST", "/v1/messages", body=REPLY)
    done = _provider(fake_api).complete(REQUEST)
    req = fake_api.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages" and KEY not in str(req.url)
    assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == API_VERSION
    assert fake_api.body() == {
        "model": "claude-sonnet-5",
        "max_tokens": 256,
        "system": "sys",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "emit", "description": "Emit a thing.", "input_schema": TOOL.input_schema}],
        "tool_choice": {"type": "tool", "name": "emit"},
    }
    assert done.tool_calls[0].name == "emit" and done.tool_calls[0].arguments == {"x": 3}
    assert (done.text, done.stop_reason, done.input_tokens, done.output_tokens) == ("Sure.", "tool_use", 11, 7)


def test_list_models(fake_api):
    fake_api.add("GET", "/v1/models", body={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5-20251001"}]})
    assert _provider(fake_api).list_models() == ["claude-sonnet-5", "claude-haiku-4-5-20251001"]


@pytest.mark.parametrize(
    ("status", "error", "match"),
    [
        (401, InvalidKey, r"Anthropic rejected the API key \(HTTP 401\)"),
        (403, InvalidKey, r"\(HTTP 403\)"),
        (404, ModelNotFound, "not found.*no such model"),
        (400, BadRequest, "rejected the request.*no such model"),
        (529, ProviderUnavailable, r"unavailable \(HTTP 529\)"),
    ],
)
def test_status_codes_map_to_typed_errors(fake_api, status, error, match):
    fake_api.add("POST", "/v1/messages", status=status, body={"type": "error", "error": {"message": "no such model"}})
    with pytest.raises(error, match=match):
        _provider(fake_api).complete(REQUEST)


def test_rate_limit_carries_retry_after(fake_api):
    fake_api.add("POST", "/v1/messages", status=429, headers={"retry-after": "20"}, body={"error": {"message": "slow"}})
    with pytest.raises(RateLimited, match="retry in 20 s") as info:
        _provider(fake_api).complete(REQUEST)
    assert info.value.retry_after_s == 20.0


def test_timeouts_and_connection_errors(fake_api):
    fake_api.add("POST", "/v1/messages", raises=httpx.ReadTimeout("slow"))
    fake_api.add("POST", "/v1/messages", raises=httpx.ConnectError("refused"))
    provider = _provider(fake_api, timeout_s=5)
    with pytest.raises(ProviderTimeout, match="did not answer within 5 s"):
        provider.complete(REQUEST)
    with pytest.raises(
        ProviderUnavailable, match=r"could not reach Anthropic at https://api\.anthropic\.com \(ConnectError\)"
    ):
        provider.complete(REQUEST)


def test_connect_error_with_userinfo_url_never_echoes_the_token(fake_api):
    token = "super-secret-token-value"
    fake_api.add("POST", "/v1/messages", raises=httpx.ConnectError("refused"))
    provider = _provider(fake_api, base_url=f"https://{token}@api.example.org")
    with pytest.raises(ProviderUnavailable) as info:
        provider.complete(REQUEST)
    assert token not in str(info.value)
    assert "the configured endpoint" in str(info.value)


def test_key_is_scrubbed_from_provider_detail(fake_api):
    detail = f"bad header {KEY} and sk-ant-api03-OTHERKEYabcdefghijklmnop"
    fake_api.add("POST", "/v1/messages", status=400, body={"error": {"message": detail}})
    with pytest.raises(BadRequest) as info:
        _provider(fake_api).complete(REQUEST)
    message = str(info.value)
    assert "TESTKEY" not in message and "OTHERKEY" not in message and "[redacted]" in message


def test_unexpected_shape_is_bad_response(fake_api):
    fake_api.add("POST", "/v1/messages", body={"nope": 1})
    with pytest.raises(BadResponse, match="unexpected reply"):
        _provider(fake_api).complete(REQUEST)


def test_repr_has_no_key(fake_api):
    text = repr(_provider(fake_api))
    assert KEY not in text and "api.anthropic.com" in text


def test_context_is_a_cached_system_block_and_temperature_is_sent(fake_api):
    fake_api.add("POST", "/v1/messages", body=REPLY)
    _provider(fake_api).complete(replace(REQUEST, context="GUIDE", temperature=0.0))
    body = fake_api.body()
    assert body["system"] == [
        {"type": "text", "text": "GUIDE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "sys"},
    ]
    assert body["temperature"] == 0.0


def test_unforced_tools_send_no_tool_choice(fake_api):
    fake_api.add("POST", "/v1/messages", body=REPLY)
    done = _provider(fake_api).complete(replace(REQUEST, force_tool=None))
    body = fake_api.body()
    assert "tool_choice" not in body and [t["name"] for t in body["tools"]] == ["emit"]
    assert done.tool_calls[0].name == "emit" and done.text == "Sure."
