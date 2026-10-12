"""Tests for netstead.select.emit — SelectionResult -> validated fragment dict."""

import pytest
from netstead.select.emit import to_fragment, to_projectcard, validate_fragment
from netstead.select.errors import SelectError
from netstead.select.intent import Facility, SelectionIntent
from netstead.select.result import AnchorMatch, SelectionResult


def _resolved():
    intent = SelectionIntent(
        facility=Facility(ref="I 40", direction="EB"),
        from_anchor="South Miami Boulevard",
        to_anchor="Airport Boulevard",
        utterance="I-40 EB between South Miami Blvd and Airport Blvd",
    )
    return SelectionResult(
        status="resolved",
        intent=intent,
        link_ids=[5019, 5018, 5016, 801],
        node_path=[170510751, 1, 2, 195394331],
        from_match=AnchorMatch("South Miami Boulevard", 170510751, [], 1.0, "gore", "off-ramp diverge"),
        to_match=AnchorMatch("Airport Boulevard", 195394331, [], 1.0, "merge", "on-ramp merge"),
        diagnostics=[],
    )


def test_fragment_uses_gmns_native_keys():
    frag = to_fragment(_resolved())
    assert frag["links"]["link_id"] == [5019, 5018, 5016, 801]
    assert frag["from"]["node_id"] == 170510751
    assert frag["to"]["node_id"] == 195394331
    assert "South Miami" in frag["notes"]


def test_fragment_validates_against_schema():
    frag = to_fragment(_resolved())
    validate_fragment(frag)  # must not raise


def test_projectcard_adapter_maps_keys():
    pc = to_projectcard(_resolved())
    assert pc["links"]["model_link_id"] == [5019, 5018, 5016, 801]
    assert pc["from"]["model_node_id"] == 170510751
    assert pc["to"]["model_node_id"] == 195394331


def test_emit_requires_resolved_status():
    r = _resolved()
    unresolved = SelectionResult(
        status="ambiguous",
        intent=r.intent,
        link_ids=[],
        node_path=[],
        from_match=None,
        to_match=None,
        diagnostics=["two candidates for Airport Boulevard"],
    )
    with pytest.raises(SelectError):
        to_fragment(unresolved)


# --- query-form emit (re-resolvable ProjectCard-style selection) ---


def _resolved_result(intent, link_ids=(1, 2, 3)):
    return SelectionResult(
        status="resolved",
        intent=intent,
        link_ids=list(link_ids),
        node_path=[],
        from_match=None,
        to_match=None,
        diagnostics=[],
    )


def test_query_form_facility_emits_name_and_ref():
    intent = SelectionIntent(facility=Facility(name="Page Road", ref="SR 1234"), utterance="all of Page Road")
    frag = to_fragment(_resolved_result(intent), form="query")
    assert frag["links"]["name"] == ["Page Road"]
    assert frag["links"]["ref"] == ["SR 1234"]
    assert "link_id" not in frag["links"]
    validate_fragment(frag)


def test_query_form_select_all():
    frag = to_fragment(_resolved_result(SelectionIntent(select_all=True)), form="query")
    assert frag["links"]["all"] is True
    validate_fragment(frag)


def test_query_form_carries_conditions_and_modes():
    intent = SelectionIntent(facility=Facility(ref="I 40"), conditions={"lanes": [2]}, modes=["drive"])
    frag = to_fragment(_resolved_result(intent), form="query")
    assert frag["links"]["ref"] == ["I 40"]
    assert frag["links"]["lanes"] == [2]  # condition emitted as extra field
    assert frag["links"]["modes"] == ["drive"]
    validate_fragment(frag)


def test_query_form_explicit_link_ids():
    intent = SelectionIntent(link_ids=[10, 20])
    frag = to_fragment(_resolved_result(intent, link_ids=[10, 20]), form="query")
    assert frag["links"]["link_id"] == [10, 20]
    validate_fragment(frag)


def test_query_form_carries_segment_anchors():
    intent = SelectionIntent(
        facility=Facility(ref="I 40", direction="EB"),
        from_anchor="South Miami Boulevard",
        to_anchor="Airport Boulevard",
    )
    r = SelectionResult(
        status="resolved",
        intent=intent,
        link_ids=[1],
        node_path=[],
        from_match=AnchorMatch("South Miami Boulevard", 111, [], 1.0, "gore", ""),
        to_match=AnchorMatch("Airport Boulevard", 222, [], 1.0, "merge", ""),
        diagnostics=[],
    )
    frag = to_fragment(r, form="query")
    assert frag["links"]["ref"] == ["I 40"]
    assert frag["from"]["node_id"] == 111 and frag["to"]["node_id"] == 222
    validate_fragment(frag)


def test_projectcard_query_form_emits_native_fields():
    intent = SelectionIntent(facility=Facility(name=["Page Road"]), conditions={"lanes": [2]})
    pc = to_projectcard(_resolved_result(intent), form="query")
    assert pc["links"]["name"] == ["Page Road"]
    assert pc["links"]["lanes"] == [2]
    assert "model_link_id" not in pc["links"]


def test_unknown_form_raises():
    with pytest.raises(SelectError):
        to_fragment(_resolved_result(SelectionIntent(select_all=True)), form="bogus")


def test_projectcard_links_always_carry_ignore_missing():
    # The ProjectCard schema requires it on every links selector (select_links.json "required").
    assert to_projectcard(_resolved())["links"]["ignore_missing"] is True
    page = SelectionIntent(facility=Facility(name=["Page Road"]))
    assert to_projectcard(_resolved_result(page), form="query")["links"]["ignore_missing"] is True
    strict = SelectionIntent(facility=Facility(name=["Page Road"]), ignore_missing=False)
    assert to_projectcard(_resolved_result(strict), form="query")["links"]["ignore_missing"] is False
    assert to_projectcard(_resolved_result(strict))["links"]["ignore_missing"] is False
