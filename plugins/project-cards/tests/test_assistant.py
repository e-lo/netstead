"""With the plugin opted in, the assistant offers project_cards.* as tools and drafts them for the user to approve."""

from netstead.llm.types import Completion, ToolCall
from netstead.workbench.assistant.tools import tool_specs

NET = "leavenworth"


class Scripted:
    """A provider that replays Completions (local, so nothing counts against a budget)."""

    name, label, base_url = "ollama", "Scripted", "http://localhost:11434"

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def complete(self, request):
        self.requests.append(request)
        return self.replies.pop(0)

    def list_models(self):
        return []


def reply(*calls):
    return Completion(tool_calls=tuple(ToolCall(n, a) for n, a in calls), text="", input_tokens=100, output_tokens=20)


OPTED_IN = {"assistant.plugins": ["project_cards"], "assistant.compact_tools": "off"}
SELECT = ("select", {"utterance": "Benton Street", "facility": {"name": "Benton Street"}})
ADD = (
    "project_cards__add_change",
    {"use_selection": True, "change": {"type": "roadway_property_change", "property_changes": {"lanes": {"set": 2}}}},
)


def test_every_cards_action_is_a_drafted_tool(make_session):
    session = make_session(overrides=OPTED_IN)
    specs = {s.action_type: s for s in tool_specs(session) if s.plugin == "project_cards"}
    assert set(specs) == {
        "project_cards.add_change",
        "project_cards.undo_change",
        "project_cards.commit",
        "project_cards.import",
        "project_cards.preview",
        "project_cards.close_preview",
    }
    assert (
        all(s.mutates for s in specs.values())
        and specs["project_cards.add_change"].tool.name == "project_cards__add_change"
    )


def test_a_card_change_from_the_assistant_waits_for_approval(make_session):
    session = make_session(provider=Scripted(reply(SELECT, ADD)), overrides=OPTED_IN)
    turn = session.assistant.ask("set Benton Street to 2 lanes in the card")
    assert [s["status"] for s in turn["steps"]] == ["done", "drafted"]
    assert session.network(NET).version == 0  # nothing applied yet
    out = session.assistant.approve(turn["draft_id"])
    assert out["steps"][1]["status"] == "done"
    assert session.history[-1].action["type"] == "project_cards.add_change" and session.history[-1].via == "assistant"
    assert (
        session.state()["plugins"]["project_cards"]["drafts"][NET]["count"] == 1 and session.network(NET).version == 1
    )


def test_without_the_opt_in_the_assistant_has_no_cards_tools(make_session):
    assert not [s for s in tool_specs(make_session()) if s.plugin == "project_cards"]
