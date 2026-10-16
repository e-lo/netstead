"""Tests for the Gemini generateContent adapter."""

from dataclasses import replace

import pytest
from netstead.llm.errors import BadResponse
from netstead.llm.providers.gemini import GeminiProvider
from netstead.llm.types import CompletionRequest, Message, Tool

pytestmark = pytest.mark.usefixtures("no_network")

KEY = "AIzaSyTESTKEYabcdefghijklmnopqrstuvw"
TOOL = Tool("emit", "Emit a thing.", {"type": "object", "properties": {"x": {"type": "integer"}}})
REQUEST = CompletionRequest(
    model="gemini-2.5-flash",
    messages=(Message("user", "hi"), Message("assistant", "prev")),
    system="sys",
    tools=(TOOL,),
    force_tool="emit",
    max_tokens=200,
)
PATH = "/v1beta/models/gemini-2.5-flash:generateContent"
REPLY = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"functionCall": {"name": "emit", "args": {"x": 3}}}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 4},
}


def _provider(fake_api):
    return GeminiProvider(api_key=KEY, transport=fake_api.transport())


def test_key_travels_in_a_header_never_the_url(fake_api):
    fake_api.add("POST", PATH, body=REPLY)
    _provider(fake_api).complete(REQUEST)
    req = fake_api.requests[0]
    assert req.headers["x-goog-api-key"] == KEY
    assert "key=" not in str(req.url) and KEY not in str(req.url)


def test_request_shape_and_parsing(fake_api):
    fake_api.add("POST", PATH, body=REPLY)
    done = _provider(fake_api).complete(REQUEST)
    assert fake_api.body() == {
        "contents": [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "prev"}]},
        ],
        "generationConfig": {"maxOutputTokens": 200},
        "systemInstruction": {"parts": [{"text": "sys"}]},
        "tools": [
            {
                "functionDeclarations": [
                    {"name": "emit", "description": "Emit a thing.", "parametersJsonSchema": TOOL.input_schema}
                ]
            }
        ],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["emit"]}},
    }
    assert done.tool_calls[0].arguments == {"x": 3}
    assert (done.stop_reason, done.input_tokens, done.output_tokens) == ("STOP", 30, 4)


def test_text_reply(fake_api):
    fake_api.add("POST", PATH, body={"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]})
    done = _provider(fake_api).complete(REQUEST)
    assert done.tool_calls == () and done.text == '{"x": 1}'


def test_blocked_prompt_is_bad_response(fake_api):
    fake_api.add("POST", PATH, body={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(BadResponse, match=r"Gemini blocked the request \(SAFETY\)"):
        _provider(fake_api).complete(REQUEST)


def test_list_models_filters_and_strips_prefix(fake_api):
    fake_api.add(
        "GET",
        "/v1beta/models",
        body={
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            ]
        },
    )
    assert _provider(fake_api).list_models() == ["gemini-2.5-flash"]
    assert fake_api.requests[0].url.params["pageSize"] == "1000"


def test_context_joins_the_system_instruction_and_temperature_is_sent(fake_api):
    fake_api.add("POST", PATH, body=REPLY)
    _provider(fake_api).complete(replace(REQUEST, context="GUIDE", temperature=0.0))
    body = fake_api.body()
    assert body["systemInstruction"] == {"parts": [{"text": "GUIDE\n\nsys"}]}
    assert body["generationConfig"] == {"maxOutputTokens": 200, "temperature": 0.0}


def test_unforced_tools_keep_geminis_default_calling_mode(fake_api):
    fake_api.add("POST", PATH, body=REPLY)
    done = _provider(fake_api).complete(replace(REQUEST, force_tool=None))
    body = fake_api.body()
    assert "toolConfig" not in body and body["tools"][0]["functionDeclarations"][0]["name"] == "emit"
    assert done.tool_calls[0].name == "emit"
