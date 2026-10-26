"""Unit tests for the Workbench layout's pure modules (tooltip placement, the rail's model), run under node."""

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

VIEW = "{width: 1000, height: 700}"
TIP = "{width: 120, height: 24}"


def place_tip(node_module, target: str, opts: str = "", tip: str = TIP, view: str = VIEW) -> dict:
    expr = f"placeTip({target}, {tip}, {view}{', ' + opts if opts else ''})"
    return node_module("tipmodel.js", ["placeTip"], expr)


def test_tip_goes_below_the_target_centered_by_default(node_module):
    got = place_tip(node_module, "{left: 400, top: 100, right: 430, bottom: 130}")
    assert got == {"side": "bottom", "left": 355, "top": 138}


def test_tip_flips_above_at_the_bottom_edge(node_module):
    got = place_tip(node_module, "{left: 400, top: 660, right: 430, bottom: 690}")
    assert got["side"] == "top" and got["top"] == 660 - 8 - 24


def test_tip_slides_in_from_the_left_edge_and_the_right_edge(node_module):
    left = place_tip(node_module, "{left: 0, top: 100, right: 30, bottom: 130}")
    assert left["side"] == "bottom" and left["left"] == 4
    right = place_tip(node_module, "{left: 970, top: 10, right: 1000, bottom: 40}")
    assert right["side"] == "bottom" and right["left"] == 1000 - 120 - 4


def test_tip_prefers_a_side_and_flips_it_at_the_edge(node_module):
    got = place_tip(node_module, "{left: 0, top: 300, right: 40, bottom: 340}", '{prefer: "right"}')
    assert got == {"side": "right", "left": 48, "top": 308}
    got = place_tip(node_module, "{left: 960, top: 300, right: 1000, bottom: 340}", '{prefer: "right"}')
    assert got["side"] == "left" and got["left"] == 960 - 8 - 120


def test_tip_uses_a_perpendicular_side_when_above_and_below_do_not_fit(node_module):
    got = place_tip(node_module, "{left: 100, top: 0, right: 140, bottom: 700}")
    assert got["side"] == "right"


def test_tip_larger_than_the_viewport_stays_on_the_preferred_side_clamped(node_module):
    got = place_tip(
        node_module,
        "{left: 0, top: 0, right: 50, bottom: 50}",
        tip="{width: 2000, height: 24}",
        view="{width: 100, height: 30}",
    )
    assert got["left"] == 4 and got["top"] == 4
