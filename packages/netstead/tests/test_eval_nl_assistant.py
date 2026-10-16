"""The assistant eval script's scoring (offline). The eval itself runs by hand against a real model."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "eval_nl_assistant.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("eval_nl_assistant", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_case_in_the_set_is_well_formed(ev):
    cases = ev.load_cases(ev.EVAL_SET)
    assert len(cases) >= 12 and all(c["expect"] for c in cases if not c.get("no_calls"))
    assert all(not c["expect"] for c in cases if c.get("no_calls"))


def test_check_scores_types_in_order_and_the_fields_given(ev):
    expect = [{"type": "style", "color_by": "lanes"}, {"type": "navigate", "to_selection": True}]
    good = [{"type": "style", "color_by": "lanes", "ramp": None}, {"type": "navigate", "to_selection": True}]
    assert ev.check(good, expect) == []
    assert ev.check(list(reversed(good)), expect) == [
        "step 1: navigate, expected style",
        "step 2: style, expected navigate",
    ]
    assert ev.check(good[:1], expect) == ["1 step(s), expected 2"]
    assert ev.check([{"type": "style", "color_by": "speed"}], expect[:1]) == [
        "step 1: color_by='speed', expected 'lanes'"
    ]


def test_nested_fields_compare_by_dotted_path(ev):
    got = [{"type": "select", "query": {"facility": {"name": "Ski Hill Drive"}}}]
    assert ev.check(got, [{"type": "select", "query.facility.name": "ski hill drive"}]) == []
    assert (
        ev.check([{"type": "filter", "where": [{"col": "lanes"}]}], [{"type": "filter", "where.0.col": "lanes"}]) == []
    )


def test_the_eval_never_dispatches_a_plan(ev, tmp_path, isolated_env, monkeypatch):
    """A scripted provider stands in for the model: the table prints, and the network is untouched."""
    from netstead.llm.types import Completion, ToolCall

    class Scripted:
        name, label, base_url = "ollama", "Scripted", "http://localhost:11434"

        def complete(self, request):
            return Completion(tool_calls=(ToolCall("style", {"color_by": "lanes"}),), input_tokens=10, output_tokens=2)

        def list_models(self):
            return []

    out = ev.run_eval(
        ev.load_cases(ev.EVAL_SET)[:2], runs=1, provider=Scripted(), environ=isolated_env, project_dir=tmp_path
    )
    assert out.rows[0] == ("colour the links by lanes", 1, 1, pytest.approx(out.rows[0][3]))
    assert out.rows[1][1] == 0 and out.tokens == 24 and out.dispatched == 0


def test_an_unreachable_ollama_stops_the_eval_before_any_case(ev, tmp_path, isolated_env):
    """Nothing listens on port 9: the probe fails once, with a message, and no case is planned."""
    overrides = {"select.provider": "ollama", "llm.ollama.base_url": "http://127.0.0.1:9", "llm.ollama.timeout_s": 2}
    with pytest.raises(SystemExit, match=r"Ollama isn.t reachable at http://127\.0\.0\.1:9"):
        ev.run_eval(ev.load_cases(ev.EVAL_SET), runs=1, overrides=overrides, environ=isolated_env, project_dir=tmp_path)
