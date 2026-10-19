"""With the plugin opted in, the assistant offers project_cards.* as tools and drafts them for the user to approve."""

import pytest
from netstead.llm.types import Completion, ToolCall
from netstead.workbench.actions import EditCells
from netstead.workbench.assistant.tools import tool_specs
from netstead_project_cards.actions import PreviewChange
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

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


def reply(*calls, text=""):
    return Completion(tool_calls=tuple(ToolCall(n, a) for n, a in calls), text=text, input_tokens=100, output_tokens=20)


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
    assert all(s.mutates for s in specs.values())
    assert specs["project_cards.add_change"].tool.name == "project_cards__add_change"


def test_a_card_change_from_the_assistant_waits_for_approval(make_session):
    session = make_session(provider=Scripted(reply(SELECT, ADD)), overrides=OPTED_IN)
    turn = session.assistant.ask("set Benton Street to 2 lanes in the card")
    assert [s["status"] for s in turn["steps"]] == ["done", "drafted"]
    assert session.network(NET).version == 0  # nothing applied yet
    out = session.assistant.approve(turn["draft_id"])
    assert out["steps"][1]["status"] == "done"
    assert session.history[-1].action["type"] == "project_cards.add_change"
    assert session.history[-1].via == "assistant"
    assert session.state()["plugins"]["project_cards"]["drafts"][NET]["count"] == 1
    assert session.network(NET).version == 1


def test_without_the_opt_in_the_assistant_has_no_cards_tools(make_session):
    assert not [s for s in tool_specs(make_session()) if s.plugin == "project_cards"]


LINK_1_LANES_3 = RoadwayPropertyChange(facility=Selection(ids=[1]), property_changes={"lanes": PropertyChange(set=3)})
LINK_2_LANES_4 = RoadwayPropertyChange(facility=Selection(ids=[2]), property_changes={"lanes": PropertyChange(set=4)})


def test_a_preview_from_the_assistant_derives_nothing_until_approved(make_session):
    preview = ("project_cards__preview", {"change": LINK_1_LANES_3.model_dump(mode="json", exclude_none=True)})
    session = make_session(provider=Scripted(reply(preview)), overrides=OPTED_IN)
    turn = session.assistant.ask("preview link 1 with 3 lanes")
    assert [s["status"] for s in turn["steps"]] == ["drafted"]
    assert session.registry.ids() == [NET]
    out = session.assistant.approve(turn["draft_id"])
    assert out["steps"][0]["status"] == "done"
    derived = [h for h in session.registry if h.derived_from == NET]
    assert len(derived) == 1
    assert session.active == derived[0].id


def test_approving_a_card_change_drafted_on_the_preview_is_stale_once_the_base_moved(make_session):
    add = ("project_cards__add_change", {"change": LINK_1_LANES_3.model_dump(mode="json", exclude_none=True)})
    session = make_session(provider=Scripted(reply(add)), overrides=OPTED_IN)
    session.dispatch(PreviewChange(change=LINK_2_LANES_4))  # the preview is the active network
    turn = session.assistant.ask("set link 1 to 3 lanes in the card")
    assert [s["status"] for s in turn["steps"]] == ["drafted"]
    session.dispatch(EditCells(net_id=NET, ids=[1], values={"lanes": 5}))  # an outside edit on the base
    with pytest.raises(ValueError, match="changed since"):
        session.assistant.approve(turn["draft_id"])
    links = session.network(NET).links_df()
    assert links.loc[links.link_id == 1, "lanes"].tolist() == [5]  # the outside edit stands
    assert NET not in session.state()["plugins"]["project_cards"]["drafts"]


@pytest.mark.parametrize(("mode", "offered"), [("auto", False), ("off", True)])
def test_a_local_model_gets_no_cards_tools_while_tools_are_compact(make_session, mode, offered):
    # Core's compact tool set ("auto" on a local endpoint) keeps only edit_cells, delete_rows and undo_edit among the
    # drafted tools, so every project_cards.* Action is dropped. The cookbook should tell local-model users to set
    # assistant.compact_tools to "off" to draft cards (a later task).
    provider = Scripted(reply(text="ok"))
    session = make_session(provider=provider, overrides={"assistant.plugins": ["project_cards"]})
    session.dispatch({"type": "set_setting", "key": "assistant.compact_tools", "value": mode})
    session.assistant.plan("hello")
    names = {t.name for t in provider.requests[0].tools}
    assert any(n.startswith("project_cards__") for n in names) == offered
