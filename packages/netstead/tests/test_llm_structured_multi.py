"""request_tool_calls: several tools, none forced, every call validated, a bounded repair loop, JSON envelope."""

import pytest
from netstead.llm.errors import InvalidKey, ToolsUnsupported
from netstead.llm.structured import ENVELOPE_SCHEMA, StructuredOutputError, request_tool_calls
from netstead.llm.types import Completion, Message, Tool, ToolCall

COUNT = Tool("count", "Count.", {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]})
NAME = Tool("name", "Name.", {"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]})
ASK = (Message("user", "count to 3 and name it"),)


class Scripted:
    """An LLMProvider that replays scripted Completions (or raises scripted errors) and keeps every request."""

    name, label = "scripted", "Scripted"

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def complete(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def list_models(self):
        return []


def _calls(*calls, text="", tokens=(10, 5)):
    return Completion(
        tool_calls=tuple(ToolCall(n, a) for n, a in calls), text=text, input_tokens=tokens[0], output_tokens=tokens[1]
    )


def test_several_calls_in_one_reply_none_forced():
    p = Scripted(_calls(("count", {"n": 3}), ("name", {"s": "three"}), text="Done."))
    r = request_tool_calls(p, model="m", tools=(COUNT, NAME), messages=ASK)
    assert [c.name for c in r.calls] == ["count", "name"] and r.text == "Done." and r.attempts == 1
    assert p.requests[0].tools == (COUNT, NAME) and p.requests[0].force_tool is None
    assert (r.input_tokens, r.output_tokens) == (10, 5)


def test_a_text_only_answer_is_fine():
    r = request_tool_calls(Scripted(_calls(text="I need a street name.")), model="m", tools=(COUNT,), messages=ASK)
    assert r.calls == () and r.text == "I need a street name."


def test_an_empty_reply_is_repaired_and_usage_sums():
    p = Scripted(_calls(), _calls(text="ok"))
    r = request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK)
    assert r.attempts == 2 and (r.input_tokens, r.output_tokens) == (20, 10)
    assert "the reply was empty" in p.requests[1].messages[-1].content


def test_unknown_tool_and_bad_arguments_are_repaired_naming_the_call():
    p = Scripted(_calls(("count", {"n": 1}), ("nope", {})), _calls(("count", {"n": "x"})), _calls(("count", {"n": 2})))
    r = request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK, max_repairs=2)
    assert r.attempts == 3 and r.calls[0].arguments == {"n": 2}
    assert "call 2: there is no tool named 'nope'" in p.requests[1].messages[-1].content
    assert "call 1 (count): n: 'x' is not of type 'integer'" in p.requests[2].messages[-1].content
    assert [m.role for m in p.requests[2].messages] == ["user", "assistant", "user", "assistant", "user"]


def test_too_many_calls_are_repaired():
    p = Scripted(_calls(*[("count", {"n": i}) for i in range(3)]), _calls(("count", {"n": 1})))
    r = request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK, max_calls=2)
    assert r.attempts == 2 and "at most 2 tool calls" in p.requests[1].messages[-1].content


def test_the_caller_validates_the_whole_list():
    def validate(calls):
        if len(calls) > 1:
            raise ValueError("one step at a time")

    p = Scripted(_calls(("count", {"n": 1}), ("count", {"n": 2})), _calls(("count", {"n": 1})))
    r = request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK, validate=validate)
    assert len(r.calls) == 1 and "one step at a time" in p.requests[1].messages[-1].content


def test_nulls_are_absent():
    p = Scripted(_calls(("name", {"s": "x", "extra": None})))
    assert request_tool_calls(p, model="m", tools=(NAME,), messages=ASK).calls[0].arguments == {"s": "x"}


def test_json_envelope_when_tools_are_unsupported():
    reply = Completion(text='{"reply": "ok", "calls": [{"tool": "count", "arguments": {"n": 4}}]}')
    p = Scripted(ToolsUnsupported("scripted", "no tools"), reply)
    r = request_tool_calls(p, model="m", tools=(COUNT, NAME), messages=ASK, context="ctx")
    assert r.mode == "json" and r.calls[0] == ToolCall("count", {"n": 4}) and r.text == "ok"
    assert p.requests[1].json_schema == ENVELOPE_SCHEMA and '"count"' in p.requests[1].system
    assert p.requests[1].context == "ctx" and p.requests[1].tools == ()


def test_a_bad_envelope_is_repaired_in_json_mode():
    good = Completion(text='{"calls": [{"tool": "count", "arguments": {"n": 1}}]}')
    p = Scripted(Completion(text='{"calls": "count"}'), good)
    r = request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK, json_mode=True)
    assert r.mode == "json" and r.attempts == 2 and r.text == ""
    assert '"calls" must be a list' in p.requests[1].messages[-1].content
    assert "corrected JSON object" in p.requests[1].messages[-1].content


def test_gives_up_after_the_budget_and_never_retries_provider_errors():
    with pytest.raises(StructuredOutputError):
        request_tool_calls(
            Scripted(_calls(("nope", {})), _calls(("nope", {}))), model="m", tools=(COUNT,), messages=ASK
        )
    with pytest.raises(InvalidKey):
        request_tool_calls(Scripted(InvalidKey("scripted", "bad")), model="m", tools=(COUNT,), messages=ASK)


def test_unreported_usage_is_none():
    r = request_tool_calls(Scripted(_calls(text="hi", tokens=(None, None))), model="m", tools=(COUNT,), messages=ASK)
    assert r.input_tokens is None and r.output_tokens is None


# ---------------------------------------------------------------------- tool calls whose arguments aren't JSON


def _openai(fake_api, *replies):
    """An OpenAI adapter behind a fake API answering each request with the next list of ``(tool, raw arguments)``."""
    from netstead.llm.providers.openai import OpenAIProvider

    for calls in replies:
        raw = [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a}}
            for i, (n, a) in enumerate(calls)
        ]
        message = {"role": "assistant", "content": None, "tool_calls": raw}
        body = {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}], "usage": {}}
        fake_api.add("POST", "/v1/chat/completions", body=body)
    return OpenAIProvider(api_key="sk-proj-TESTKEYabcdefghijklmnopqrstuvwx", transport=fake_api.transport())


@pytest.mark.usefixtures("no_network")
def test_a_lone_call_with_truncated_arguments_is_repaired_not_taken_as_text(fake_api):
    p = _openai(fake_api, [("count", '{"n": ')], [("count", '{"n": 3}')])
    out = request_tool_calls(p, model="m", tools=(COUNT, NAME), messages=ASK)
    assert out.calls == (ToolCall("count", {"n": 3}),) and out.text == "" and out.attempts == 2
    repair = fake_api.body(1)["messages"][-1]["content"]
    assert "call 1 (count): arguments were not a JSON object" in repair


@pytest.mark.usefixtures("no_network")
def test_a_bad_call_among_good_ones_is_repaired_never_dropped(fake_api):
    p = _openai(
        fake_api, [("count", '{"n": 3}'), ("name", '{"s": "thr')], [("count", '{"n": 3}'), ("name", '{"s": "x"}')]
    )
    out = request_tool_calls(p, model="m", tools=(COUNT, NAME), messages=ASK)
    assert [c.name for c in out.calls] == ["count", "name"] and out.attempts == 2
    assert "call 2 (name): arguments were not a JSON object" in fake_api.body(1)["messages"][-1]["content"]


@pytest.mark.usefixtures("no_network")
def test_the_forced_single_tool_path_still_salvages_or_repairs(fake_api):
    from netstead.llm.structured import request_tool_call

    p = _openai(fake_api, [("count", '{"n": 4} trailing words')])
    assert request_tool_call(p, model="m", tool=COUNT, user="count").arguments == {"n": 4}  # salvaged, as before
    p = _openai(type(fake_api)(), [("count", '{"n": ')], [("count", '{"n": 5}')])
    out = request_tool_call(p, model="m", tool=COUNT, user="count")
    assert out.arguments == {"n": 5} and out.attempts == 2


# ---------------------------------------------------------------------- a failed question still reports its tokens


def test_a_question_that_never_became_valid_reports_its_tokens():
    p = Scripted(_calls(("nope", {}), tokens=(30, 4)), _calls(("nope", {}), tokens=(40, 6)))
    with pytest.raises(StructuredOutputError) as caught:
        request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK)
    assert (caught.value.input_tokens, caught.value.output_tokens) == (70, 10)


def test_a_failed_repair_call_reports_the_tokens_before_it():
    from netstead.llm.errors import ProviderUnavailable

    p = Scripted(_calls(("nope", {}), tokens=(30, 4)), ProviderUnavailable("scripted", "down"))
    with pytest.raises(ProviderUnavailable) as caught:
        request_tool_calls(p, model="m", tools=(COUNT,), messages=ASK)
    assert (caught.value.input_tokens, caught.value.output_tokens) == (30, 4) and str(caught.value) == "down"
    with pytest.raises(InvalidKey) as first:
        request_tool_calls(Scripted(InvalidKey("scripted", "bad")), model="m", tools=(COUNT,), messages=ASK)
    assert (first.value.input_tokens, first.value.output_tokens) == (0, 0)
