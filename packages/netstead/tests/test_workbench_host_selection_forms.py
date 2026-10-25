"""Host API 1.8 (additive): ``host.selection["projectcard"]`` gains the Find tab's strict forms; the old keys stay."""

from __future__ import annotations

from typing import Any

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import OpenNetwork, SaveSelection, Select
from netstead.workbench.plugins import HOST_API, Host, WorkbenchPlugin
from netstead.workbench.selection import payload_facility_forms

PINE = "Pine Street between Orchard Street and Central Avenue"
OLD_KEYS = {"picked", "resolved", "query"}
NEW_KEYS = {"segment", "link_list", "segment_problem", "id_systems", "id_captions"}


class Probe:
    host: Host | None = None

    def plugin(self) -> WorkbenchPlugin:
        def on_load(host: Host) -> None:
            self.host = host

        return WorkbenchPlugin(id="probe", name="Probe", version="0.1", requires_api="1.8", on_load=on_load)


@pytest.fixture
def probe() -> tuple[Session, Probe]:
    holder = Probe()
    session = Session(parser=StubParser(), plugins=[holder.plugin()], keyring=None)
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir())))
    return session, holder


def test_the_old_keys_are_unchanged_and_the_api_version_did_not_move(probe):
    session, holder = probe
    session.dispatch(Select(utterance=PINE))
    forms = holder.host.selection["projectcard"]
    assert HOST_API == "1.8"
    assert set(forms) >= OLD_KEYS | NEW_KEYS
    assert forms["resolved"] == {
        "links": {"model_link_id": [60], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    assert forms["query"] == {
        "links": {"name": ["Pine Street"], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }


def test_the_strict_forms_are_written_in_each_id_system(probe):
    session, holder = probe
    session.dispatch(Select(utterance=PINE))
    forms = holder.host.selection["projectcard"]
    assert forms["segment"]["gmns"] == {
        "links": {"name": ["Pine Street"], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    assert set(forms["segment"]["osm"]) == {"links", "from", "to"}
    assert forms["link_list"]["osm"] == {"links": {"osm_link_id": ["5846369"], "ignore_missing": True}}
    assert forms["id_systems"]["gmns"] is None and forms["id_systems"]["osm"] is None
    assert forms["id_systems"]["gers"] is not None  # this network has no GERS columns
    assert forms["id_captions"] == {"osm": ["OSM way 5846369 also covers 11 links outside this selection"]}


def test_a_pick_has_no_segment_and_says_why(probe):
    session, holder = probe
    session.dispatch(Select(link_ids=[60]))
    forms = holder.host.selection["projectcard"]
    assert forms["picked"] is True and forms["segment"] == {}
    assert forms["segment_problem"] == "This selection has no named segment; use Link list"
    assert forms["link_list"]["gmns"] == {"links": {"model_link_id": [60], "ignore_missing": True}}


def test_a_saved_selection_carries_the_same_forms(probe):
    session, holder = probe
    session.dispatch(Select(utterance=PINE))
    session.dispatch(SaveSelection(name="pine"))
    saved = holder.host.saved_selection("pine")["projectcard"]
    assert set(saved) >= OLD_KEYS | NEW_KEYS and saved["link_list"]["osm"]


def test_without_a_network_only_gmns_ids_are_written(probe):
    session, _ = probe
    session.dispatch(Select(utterance=PINE))
    forms: dict[str, Any] | None = payload_facility_forms(session.selection_copy())
    assert forms is not None and set(forms["link_list"]) == {"gmns"}
    assert "network is not open" in forms["id_systems"]["osm"]
    assert set(forms) >= OLD_KEYS


def test_an_unresolved_selection_has_no_forms(probe):
    session, holder = probe
    assert holder.host.selection is None
    session.dispatch(Select(utterance="Nowhere Boulevard"))
    selection = holder.host.selection
    assert selection is not None and selection["projectcard"] is None
