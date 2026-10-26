"""Static checks for the Workbench layout: the tooltip layer, the left rail and drawer, the details panel, the Layers card."""

import re

from netstead.workbench.server import STATIC_DIR

JS_DIR = STATIC_DIR / "js"
HTML = (STATIC_DIR / "index.html").read_text()
CSS = (STATIC_DIR / "app.css").read_text()


def js(name: str) -> str:
    return (JS_DIR / name).read_text()


# ---- the tooltip layer ----


def test_one_tooltip_element_lives_in_the_body_and_is_wired_from_main():
    assert '<div id="tip" role="tooltip" hidden></div>' in HTML
    assert HTML.index('id="tip"') > HTML.index('id="toast"')  # a top-level child of <body>, after the app
    css_rule = re.search(r"#tip \{[^}]*\}", CSS).group(0)
    assert "position:fixed" in css_rule and "z-index:2147483000" in css_rule
    assert "wireTooltip()" in js("main.js")


def test_tooltips_show_after_a_delay_on_hover_and_focus_and_hide_on_leave_blur_escape_scroll():
    src = js("tooltip.js")
    assert "DELAY_MS = 300" in src
    for event in ('"pointerover"', '"pointerout"', '"focusin"', '"focusout"', '"scroll"', '"Escape"'):
        assert event in src, event
    assert 'setAttribute("aria-describedby", "tip")' in src
    assert "placeTip(" in src and 'from "./tipmodel.js"' in src


def test_no_css_tooltips_remain():
    assert not re.search(r"\[data-tip\][^{]*::(after|before)", CSS)
    assert "content:attr(data-tip)" not in CSS


# ---- the left rail and its drawer ----


def test_the_rail_is_a_vertical_tablist_left_of_the_drawer_and_the_stage():
    rail = re.search(r'<div id="dock-tabs"[^>]*>', HTML).group(0)
    assert 'role="tablist"' in rail and 'aria-orientation="vertical"' in rail and 'aria-label="Panels"' in rail
    assert HTML.index('id="dock-tabs"') < HTML.index('<aside id="side">') < HTML.index('id="side-handle"') < HTML.index('id="stage"')
    side = HTML[HTML.index('<aside id="side">') : HTML.index("</aside>")]
    assert 'id="dock-tabs"' not in side


def test_the_rail_entries_are_icon_buttons_with_tips_and_the_drawer_toggles_on_click_and_moves_on_arrows():
    ws = js("workspaces.js")
    assert 'data-tip="${esc(label)}"' in ws and "aria-label=" in ws and 'role="tab"' in ws
    assert 'axis: "vertical"' in ws and "toggleRail(" in ws and "dataset.drawer" in ws
    assert "isPluginPanel" in ws and "rail-sep" in ws  # plugin panels sit below a divider
    assert "drawerOpen" in js("store.js")


def test_the_changes_entry_is_the_edit_panel_with_a_history_icon():
    assert 'id: "edit", title: "Changes", icon: "history"' in js("edits.js")
    assert "history:" in js("icons.js")


def test_the_drawer_reuses_the_side_handle_and_its_stored_width():
    assert '#app[data-drawer="closed"]' in CSS
    assert 'side: e => e.clientX - $("side").getBoundingClientRect().left' in js("handles.js")
