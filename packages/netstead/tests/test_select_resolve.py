"""Resolver tests against the committed rdu_i40 fixture (real I-40 interchanges)."""

from importlib import resources

import pandas as pd
import pytest
from netstead.fixtures import leavenworth
from netstead.select.errors import IntentError
from netstead.select.intent import Facility, SelectionIntent
from netstead.select.resolve import _apply_modes, resolve_frames


@pytest.fixture(scope="module")
def rdu():
    base = resources.files("netstead.fixtures.rdu_i40").joinpath("parquet")
    links = pd.read_parquet(base.joinpath("link.parquet"))
    nodes = pd.read_parquet(base.joinpath("node.parquet"))
    return links, nodes


def _intent(direction, a, b):
    return SelectionIntent(
        facility=Facility(ref="I 40", direction=direction),
        from_anchor=a,
        to_anchor=b,
        utterance=f"I-40 {direction} between {a} and {b}",
    )


def test_resolves_eb_between_two_interchanges(rdu):
    links, nodes = rdu
    r = resolve_frames(_intent("EB", "South Miami Boulevard", "Airport Boulevard"), links, nodes)
    assert r.status == "resolved"
    assert r.link_ids, "expected a non-empty link path"
    # every selected link is an I-40 mainline motorway link
    sel = links[links.link_id.isin(r.link_ids)]
    assert (sel.facility_type == "motorway").all()
    assert sel["ref"].apply(lambda x: "40" in str(x)).all()
    # path endpoints match the resolved anchor nodes
    assert r.node_path[0] == r.from_match.node_id
    assert r.node_path[-1] == r.to_match.node_id
    # gore/merge rule: upstream=gore (off-ramp diverge), downstream=merge (on-ramp)
    assert r.from_match.kind == "gore"
    assert r.to_match.kind == "merge"


def test_direction_reverses_path(rdu):
    links, nodes = rdu
    eb = resolve_frames(_intent("EB", "South Miami Boulevard", "Airport Boulevard"), links, nodes)
    wb = resolve_frames(_intent("WB", "Airport Boulevard", "South Miami Boulevard"), links, nodes)
    assert eb.status == wb.status == "resolved"
    # opposite carriageways -> disjoint directed link sets
    assert set(eb.link_ids).isdisjoint(set(wb.link_ids))


def test_unknown_facility_not_found(rdu):
    links, nodes = rdu
    r = resolve_frames(
        _intent("EB", "South Miami Boulevard", "Airport Boulevard").__class__(
            facility=Facility(ref="I 999", direction="EB"),
            from_anchor="South Miami Boulevard",
            to_anchor="Airport Boulevard",
        ),
        links,
        nodes,
    )
    assert r.status == "not_found"


def test_golden_eb_south_miami_to_airport(rdu):
    """Regression lock: exact resolved link path + gore/merge nodes."""
    links, nodes = rdu
    r = resolve_frames(_intent("EB", "South Miami Boulevard", "Airport Boulevard"), links, nodes)
    assert r.link_ids == [5021, 5019, 5018, 5016, 801, 267, 5015, 5010, 5011, 5009, 5008, 10201, 10200, 7262]
    assert r.from_match.node_id == 170505098 and r.from_match.kind == "gore"
    assert r.to_match.node_id == 195387716 and r.to_match.kind == "merge"


def test_unknown_anchor_is_not_resolved(rdu):
    links, nodes = rdu
    r = resolve_frames(_intent("EB", "Nonexistent Street XYZ", "Airport Boulevard"), links, nodes)
    assert r.status in {"not_found", "ambiguous"}
    assert r.from_match is None or r.from_match.node_id is None


def test_blank_facility_name_matches_nothing(rdu):
    """A whitespace-only name normalizes to "" and must not match blank-named links."""
    links, nodes = rdu
    assert (links["name"].fillna("").astype(str).str.strip() == "").any(), "fixture must have a blank-named link"
    r = resolve_frames(SelectionIntent(facility=Facility(name="  ")), links, nodes)
    assert r.status == "not_found"
    assert r.link_ids == []


def test_network_without_a_ref_column_still_resolves_by_name(rdu):
    """``ref`` is optional in GMNS: a network without it resolves names and treats refs as unmatched."""
    links, nodes = rdu
    no_ref = links.drop(columns=["ref"])
    by_name = resolve_frames(SelectionIntent(facility=Facility(name="Page Road")), no_ref, nodes)
    assert by_name.link_ids
    by_ref = resolve_frames(SelectionIntent(facility=Facility(ref="I 40")), no_ref, nodes)
    assert by_ref.status == "not_found"


# --- ref <-> name fallback (a small model puts a street name in ``ref``, or a route in ``name``) ---


def test_street_name_in_ref_falls_back_to_name(rdu):
    links, nodes = rdu
    by_name = resolve_frames(SelectionIntent(facility=Facility(name="Airport Boulevard")), links, nodes)
    by_ref = resolve_frames(SelectionIntent(facility=Facility(ref="airport boulevard")), links, nodes)
    assert by_ref.status == "resolved"
    assert by_ref.link_ids == by_name.link_ids != []
    assert "treated ref 'airport boulevard' as a street name" in by_ref.diagnostics
    assert not any("treated" in d for d in by_name.diagnostics)


def test_route_number_in_name_falls_back_to_ref(rdu):
    links, nodes = rdu
    by_ref = resolve_frames(SelectionIntent(facility=Facility(ref="I 40")), links, nodes)
    by_name = resolve_frames(SelectionIntent(facility=Facility(name="I-40")), links, nodes)
    assert by_name.status == "resolved"
    assert by_name.link_ids == by_ref.link_ids != []
    assert "treated name 'I-40' as a route number" in by_name.diagnostics
    assert not any("treated" in d for d in by_ref.diagnostics)


def test_fallback_is_per_value_and_never_when_the_ref_matched(rdu):
    """In a ref list, only the value that missed the ref column is tried as a name."""
    links, nodes = rdu
    r = resolve_frames(SelectionIntent(facility=Facility(ref=["I 40", "Page Road"])), links, nodes)
    selected = links[links.link_id.isin(r.link_ids)]
    assert set(selected["name"].dropna()) >= {"Page Road"}
    assert selected["ref"].eq("I 40").any()
    assert [d for d in r.diagnostics if "treated" in d] == ["treated ref 'Page Road' as a street name"]


def test_fallback_that_matches_nothing_stays_not_found(rdu):
    links, nodes = rdu
    r = resolve_frames(SelectionIntent(facility=Facility(ref="Nowhere Lane", name="US 999")), links, nodes)
    assert r.status == "not_found"
    assert not any("treated" in d for d in r.diagnostics)


def test_fallback_note_survives_the_segment_path(rdu):
    """The note reaches the result when the facility came from a fallback and anchors cut a segment."""
    links, nodes = rdu
    intent = SelectionIntent(
        facility=Facility(name="I 40", direction="EB"),
        from_anchor="South Miami Boulevard",
        to_anchor="Airport Boulevard",
    )
    r = resolve_frames(intent, links, nodes)
    golden = resolve_frames(_intent("EB", "South Miami Boulevard", "Airport Boulevard"), links, nodes)
    assert r.status == "resolved"
    assert r.link_ids == golden.link_ids
    assert "treated name 'I 40' as a route number" in r.diagnostics


@pytest.fixture(scope="module")
def leav():
    base = leavenworth.parquet_dir()
    return pd.read_parquet(base / "link.parquet"), pd.read_parquet(base / "node.parquet")


def _cherry(**pins):
    return SelectionIntent(
        facility=Facility(name="Cherry Street"),
        from_anchor="Prospect Street",
        to_anchor="West Center Street",
        **pins,
    )


def test_surface_anchors_resolve_on_a_network_without_a_ref_column(leav):
    links, nodes = leav
    assert "ref" not in links.columns
    r = resolve_frames(_cherry(), links, nodes)
    assert r.status == "ambiguous" and r.link_ids == [170, 182, 187]
    assert (r.from_match.node_id, r.from_match.candidates) == (53, [])
    assert (r.to_match.node_id, r.to_match.candidates) == (60, [61])


def test_rdu_anchors_do_not_raise_without_a_ref_column(rdu):
    """A second network: RDU with ``ref`` dropped must not raise either (``_anchor_links`` reads the same columns).

    The freeway path's ``_surface_links`` gets the same ``_column`` fix; RDU's freeway needs ``ref`` to find I-40
    at all, so that path is covered by reading the diff, not by a test.
    """
    links, nodes = rdu
    no_ref = links.drop(columns=["ref"])
    intent = SelectionIntent(
        facility=Facility(name="Page Road"), from_anchor="South Miami Boulevard", to_anchor="Airport Boulevard"
    )
    resolve_frames(intent, no_ref, nodes)  # the status depends on the fixture's names; raising is the bug


def test_without_a_name_column_the_facility_matches_nothing(leav):
    links, nodes = leav
    assert resolve_frames(_cherry(), links.drop(columns=["name"]), nodes).status == "not_found"


def test_pinning_the_chosen_candidate_resolves(leav):
    links, nodes = leav
    r = resolve_frames(_cherry(to_node=60), links, nodes)
    assert r.status == "resolved" and r.link_ids == [170, 182, 187]
    assert (r.to_match.node_id, r.to_match.kind, r.to_match.candidates) == (60, "pinned", [])
    assert "pinned to node 60" in r.to_match.detail


def test_pinning_another_candidate_re_resolves_and_may_find_no_path(leav):
    links, nodes = leav
    r = resolve_frames(_cherry(to_node=61), links, nodes)
    assert r.status == "not_found" and r.to_match.node_id == 61
    assert "no path between resolved anchor nodes" in r.diagnostics


def test_a_pin_off_the_facility_is_unresolved(leav):
    links, nodes = leav
    r = resolve_frames(_cherry(from_node=999_999), links, nodes)
    assert r.status == "not_found" and r.from_match.node_id is None
    assert "not on the facility" in r.from_match.detail


def test_a_pin_needs_its_anchor():
    with pytest.raises(IntentError, match="from_node pins from_anchor"):
        SelectionIntent(facility=Facility(name="X"), from_node=1)


def _cedar(**pins):
    return SelectionIntent(
        facility=Facility(name="Cedar Street"), from_anchor="Central Avenue", to_anchor="Price Avenue", **pins
    )


@pytest.mark.parametrize("mode", ["drive", "bike", "DRIVE", "walk"])
def test_mode_filter_matches_gmns_uses(leav, mode):
    links, nodes = leav
    r = resolve_frames(_cedar(modes=[mode]), links, nodes)
    assert r.status == "resolved" and r.link_ids == [57, 129]


def test_mode_filter_with_no_matching_use_explains_itself(leav):
    links, nodes = leav
    r = resolve_frames(_cedar(modes=["transit"]), links, nodes)
    assert r.status == "not_found"
    assert any("mode filter ['transit'] matched none of" in d and "allowed_uses: auto" in d for d in r.diagnostics)


def _frames(uses):
    return pd.DataFrame({"link_id": range(len(uses)), "allowed_uses": uses})


def test_apply_modes_is_case_insensitive_on_data():
    kept, applied, _ = _apply_modes(_frames(["Auto,Bike", "WALK"]), ["DRIVE"])
    assert applied and kept["link_id"].tolist() == [0]


def test_apply_modes_unknown_mode_matches_literally():
    kept, _, _ = _apply_modes(_frames(["auto,ferry", "auto"]), ["ferry"])
    assert kept["link_id"].tolist() == [0]


def test_apply_modes_expands_use_groups():
    groups = {"motorized": {"auto", "truck"}}
    kept, _, _ = _apply_modes(_frames(["truck", "bike"]), ["motorized"], groups)
    assert kept["link_id"].tolist() == [0]


def test_apply_modes_any_is_no_filter():
    kept, applied, diags = _apply_modes(_frames(["bike", "walk"]), ["any"])
    assert len(kept) == 2 and not applied and diags == []


def test_apply_modes_reports_partial_removal():
    _, _, diags = _apply_modes(_frames(["bike", "auto", "bike"]), ["bike"])
    assert diags == ["mode filter ['bike']: 3 -> 2 link(s)"]


def test_apply_modes_truncates_listed_uses():
    uses = [",".join(f"u{i:02d}" for i in range(12))]
    _, _, diags = _apply_modes(_frames(uses), ["drive"])
    assert "u09, ..." in diags[0] and "u10" not in diags[0]
