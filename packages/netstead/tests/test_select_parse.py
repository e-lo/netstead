"""Tests for netstead.select.parse — utterance -> SelectionIntent."""

import pytest
from netstead.config import load_settings
from netstead.llm import build_registry
from netstead.llm.providers.anthropic import AnthropicProvider
from netstead.llm.secrets import KEYRING_SERVICE
from netstead.select.errors import IntentError
from netstead.select.intent import SelectionIntent
from netstead.select.parse import (
    INTENT_TOOL,
    SELECTION_TOOL,
    SYSTEM_PROMPT,
    ClaudeParser,
    LLMParser,
    StubParser,
    intent_from_payload,
    make_parser,
    payload_from_intent,
)
from netstead.select.prompt import PromptContext

pytestmark = pytest.mark.usefixtures("no_network")

UTTER = "I-40 EB between A Street and B Street"


def _anthropic_reply(payload):
    return {
        "content": [{"type": "tool_use", "id": "t", "name": "emit_selection_intent", "input": payload}],
        "stop_reason": "tool_use",
    }


def test_stub_parses_route_direction_between():
    intent = StubParser().parse("I-40 EB between South Miami Blvd and Airport Blvd")
    assert isinstance(intent, SelectionIntent)
    assert intent.facility.ref == "I 40"
    assert intent.facility.direction == "EB"
    assert intent.from_anchor == "South Miami Blvd"
    assert intent.to_anchor == "Airport Blvd"


def test_stub_parses_eastbound_word_and_strips_exits():
    intent = StubParser().parse("I-40 eastbound between Harrison Avenue and NC 54 exits")
    assert intent.facility.direction == "EB"
    assert intent.from_anchor == "Harrison Avenue"
    assert intent.to_anchor == "NC 54"


def test_stub_surface_street_no_direction():
    intent = StubParser().parse("Main Street between 1st Ave and 5th Ave")
    assert intent.facility.name == "Main Street"
    assert intent.facility.ref is None
    assert intent.facility.direction is None


def test_stub_from_to_grammar():
    intent = StubParser().parse("I-40 EB from Davis Drive to Aviation Parkway")
    assert intent.facility.ref == "I 40" and intent.facility.direction == "EB"
    assert intent.from_anchor == "Davis Drive" and intent.to_anchor == "Aviation Parkway"


def test_stub_bare_facility_is_whole_selection():
    intent = StubParser().parse("Electra Ave")
    assert intent.facility.name == "Electra Ave"
    assert intent.from_anchor is None and intent.to_anchor is None


def test_claude_parser_is_llm_parser_over_anthropic(fake_api):
    payload = {"facility": {"ref": "I 40", "direction": "EB"}, "from_anchor": "A Street", "to_anchor": "B Street"}
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply(payload))
    parser = ClaudeParser(provider=AnthropicProvider(api_key="k", transport=fake_api.transport()))
    intent, mode = parser.parse_detailed(UTTER)
    assert mode == "tools"
    assert isinstance(parser, LLMParser) and parser.model == "claude-haiku-4-5-20251001"
    assert (intent.facility.ref, intent.facility.direction, intent.from_anchor, intent.utterance) == (
        "I 40",
        "EB",
        "A Street",
        UTTER,
    )
    body = fake_api.body()
    assert body["tools"][0]["input_schema"] == SELECTION_TOOL.input_schema
    assert body["tool_choice"] == {"type": "tool", "name": "emit_selection_intent"}
    assert parser.describe() == {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "mode": None}


def test_llm_parser_repairs_an_intent_error_then_succeeds(fake_api):
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply({"modes": ["drive"]}))
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply({"facility": {"name": "Main Street"}}))
    provider = AnthropicProvider(api_key="k", transport=fake_api.transport())
    intent = LLMParser(provider, "claude-haiku-4-5-20251001").parse("Main Street")
    assert intent.facility.name == "Main Street" and len(fake_api.requests) == 2
    assert "selection requires one of" in fake_api.body()["messages"][-1]["content"]


def test_llm_parser_gives_up_as_intent_error(fake_api):
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply({"modes": ["drive"]}))
    with pytest.raises(IntentError, match="still invalid after 2 attempts"):
        LLMParser(AnthropicProvider(api_key="k", transport=fake_api.transport()), "m").parse("drive links")


def test_make_parser_stub_default_model_and_explicit_model(tmp_path, isolated_env, fake_keyring, fake_api):
    def parser_for(overrides):
        settings = load_settings(project_dir=tmp_path, environ=isolated_env, overrides=overrides).settings
        registry = build_registry(settings, environ=isolated_env, keyring=fake_keyring, transport=fake_api.transport())
        return make_parser(settings.select, registry)

    stub = parser_for({})
    assert isinstance(stub, StubParser) and stub.describe() == {"provider": "stub", "model": None, "mode": "pattern"}
    fake_keyring.set_password(KEYRING_SERVICE, "openai", "sk-test")
    assert parser_for({"select.provider": "openai"}).model == "gpt-6-luna"
    assert parser_for({"select.provider": "openai", "select.model": "gpt-4.1"}).model == "gpt-4.1"


def test_make_parser_sends_no_tools_for_a_catalog_model_without_tool_calling(
    tmp_path, isolated_env, fake_keyring, fake_api
):
    """gpt-6.1-sol is catalogued with tools=false, so make_parser must start it in JSON mode:
    no tools/tool_choice in the request body, JSON-mode instructions in the system prompt, and
    the reply parsed as a bare JSON object rather than a tool call."""
    overrides = {"select.provider": "openai", "select.model": "gpt-6.1-sol"}
    settings = load_settings(project_dir=tmp_path, environ=isolated_env, overrides=overrides).settings
    registry = build_registry(settings, environ=isolated_env, keyring=fake_keyring, transport=fake_api.transport())
    fake_keyring.set_password(KEYRING_SERVICE, "openai", "sk-test")
    parser = make_parser(settings.select, registry)
    assert isinstance(parser, LLMParser) and parser.model == "gpt-6.1-sol"

    payload = {"facility": {"ref": "I 40", "direction": "EB"}, "from_anchor": "A Street", "to_anchor": "B Street"}
    reply = {
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": str(payload)}}]
    }
    import json as _json

    reply["choices"][0]["message"]["content"] = _json.dumps(payload)
    fake_api.add("POST", "/v1/chat/completions", body=reply)

    intent, mode = parser.parse_detailed(UTTER)
    assert mode == "json"
    assert intent.facility.ref == "I 40" and intent.from_anchor == "A Street"
    body = fake_api.body()
    assert "tools" not in body and "tool_choice" not in body
    assert "Reply with ONLY one JSON object" in body["messages"][0]["content"]


def test_make_parser_still_uses_tools_for_a_catalog_model_with_tool_calling(
    tmp_path, isolated_env, fake_keyring, fake_api
):
    overrides = {"select.provider": "openai", "select.model": "gpt-6-luna"}
    settings = load_settings(project_dir=tmp_path, environ=isolated_env, overrides=overrides).settings
    registry = build_registry(settings, environ=isolated_env, keyring=fake_keyring, transport=fake_api.transport())
    fake_keyring.set_password(KEYRING_SERVICE, "openai", "sk-test")
    parser = make_parser(settings.select, registry)

    payload = {"facility": {"ref": "I 40", "direction": "EB"}, "from_anchor": "A Street", "to_anchor": "B Street"}
    call = {"id": "c1", "type": "function", "function": {"name": "emit_selection_intent", "arguments": payload}}
    reply = {
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [call]}}]
    }
    fake_api.add("POST", "/v1/chat/completions", body=reply)

    _intent, mode = parser.parse_detailed(UTTER)
    assert mode == "tools"
    body = fake_api.body()
    assert body["tool_choice"] == {"type": "function", "function": {"name": "emit_selection_intent"}}


def test_quality_settings_reach_the_request(tmp_path, isolated_env, fake_keyring, fake_api):
    overrides = {"select.provider": "anthropic", "llm.quality.max_repairs": 0, "llm.quality.temperature": 0.3}
    settings = load_settings(project_dir=tmp_path, environ=isolated_env, overrides=overrides).settings
    registry = build_registry(settings, environ=isolated_env, keyring=fake_keyring, transport=fake_api.transport())
    fake_keyring.set_password(KEYRING_SERVICE, "anthropic", "k")
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply({"modes": ["drive"]}))
    with pytest.raises(IntentError, match="after 1 attempts"):
        make_parser(settings.select, registry).parse("drive links")
    assert fake_api.body()["temperature"] == 0.3 and len(fake_api.requests) == 1


def test_prompt_context_is_a_cached_prefix_and_examples_are_per_call(fake_api):
    fake_api.add("POST", "/v1/messages", body=_anthropic_reply({"facility": {"name": "Page Road"}}))
    context = PromptContext(
        assistant="GUIDE", vocabulary=("Page Road",), examples=(("I-40 EB", {"facility": {"ref": "I 40"}}),)
    )
    LLMParser(AnthropicProvider(api_key="k", transport=fake_api.transport()), "m").parse("Page Rd", context=context)
    cached, per_call = fake_api.body()["system"]
    assert cached["cache_control"] == {"type": "ephemeral"}
    assert cached["text"].startswith(SYSTEM_PROMPT) and "GUIDE" in cached["text"] and "Page Road" in cached["text"]
    assert per_call["text"].startswith("Earlier requests") and '{"facility": {"ref": "I 40"}}' in per_call["text"]


def test_anchors_nested_inside_facility_are_repaired_not_dropped(fake_api):
    """qwen2.5:7b nests the anchors in ``facility``; the schema must reject that so the repair loop fixes it."""
    from netstead.llm.providers.ollama import OllamaProvider

    nested = {"facility": {"ref": "I 40", "direction": "EB", "from_anchor": "A Street", "to_anchor": "B Street"}}
    fixed = {"facility": {"ref": "I 40", "direction": "EB"}, "from_anchor": "A Street", "to_anchor": "B Street"}
    for payload in (nested, fixed):
        call = {"function": {"name": "emit_selection_intent", "arguments": payload}}
        fake_api.add("POST", "/api/chat", body={"message": {"role": "assistant", "tool_calls": [call]}, "done": True})
    intent = LLMParser(OllamaProvider(transport=fake_api.transport()), "qwen2.5:7b").parse(UTTER)
    assert (intent.from_anchor, intent.to_anchor) == ("A Street", "B Street")
    assert len(fake_api.requests) == 2
    assert "Additional properties" in fake_api.body()["messages"][-1]["content"]


def test_pins_round_trip_through_the_payload():
    args = {"facility": {"name": "Cherry Street"}, "from_anchor": "A", "to_anchor": "B", "to_node": 60}
    intent = intent_from_payload(args, "u")
    assert (intent.from_node, intent.to_node) == (None, 60)
    assert payload_from_intent(intent) == args


def test_the_llm_tool_schema_never_offers_pins():
    props = INTENT_TOOL["input_schema"]["properties"]
    assert "from_node" not in props and "to_node" not in props
    assert INTENT_TOOL["input_schema"]["additionalProperties"] is False  # a model's pin is a schema error


def test_modes_description_says_to_set_only_when_named():
    desc = INTENT_TOOL["input_schema"]["properties"]["modes"]["description"]
    assert "ONLY when the user explicitly names a travel mode" in desc
    assert "never default to drive" in desc
