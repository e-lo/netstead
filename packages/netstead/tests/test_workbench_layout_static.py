"""Static checks for the Workbench layout: tooltip layer, left rail and drawer, Details panel, Layers card."""

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
    assert (
        HTML.index('id="dock-tabs"')
        < HTML.index('<aside id="side">')
        < HTML.index('id="side-handle"')
        < HTML.index('id="stage"')
    )
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


# ---- the right-hand Details panel ----


def test_details_is_a_right_hand_panel_with_a_close_button_its_own_handle_and_no_rail_entry():
    panel = HTML[
        HTML.index('<aside id="details-panel"') : HTML.index("</aside>", HTML.index('<aside id="details-panel"'))
    ]
    assert "hidden" in panel.split(">", 1)[0]  # closed until something is focused
    assert 'id="details"' in panel and 'id="details-close"' in panel and 'aria-label="Close details"' in panel
    assert HTML.index('id="stage"') < HTML.index('id="details-handle"') < HTML.index('id="details-panel"')
    handle = HTML[HTML.index('id="details-handle"') : HTML.index('data-icon="grip"', HTML.index('id="details-handle"'))]
    assert 'role="separator"' in handle and 'aria-orientation="vertical"' in handle and 'tabindex="0"' in handle
    assert "dock-inspect" not in HTML and 'slots.addPanel(CORE, { id: "inspect"' not in js("workspaces.js")


def test_a_focus_opens_details_and_the_close_button_and_escape_clear_it():
    src = js("detailspanel.js")
    assert 'store.subscribe(["focus"], sync)' in src and "hidden = !open" in src
    assert "closeDetails() { store.set({ focus: null }); }" in src
    assert '$("details-close").onclick = closeDetails' in src
    assert 'e.key !== "Escape"' in src and 'document.addEventListener("keydown", onKey)' in src
    assert "wireDetailsPanel()" in js("main.js")
    assert 'if (f.from === "map" || f.from === "table") showPanel' not in js("main.js")  # any focus opens it


def test_details_has_its_own_stored_width_and_the_highlighted_links_stay_in_find():
    assert 'details: e => $("app").getBoundingClientRect().right - e.clientX' in js("handles.js")
    assert 'wireHandle("details-handle", "details")' in js("handles.js")
    assert "--details-w" in js("handles.js") and "details: [260, 640]" in js("resize.js")
    find = HTML[HTML.index('id="dock-find"') : HTML.index('id="dock-edit"')]
    assert 'id="hl-wrap"' in find and 'id="hl-set"' in find


# ---- the Layers & legend card ----

MOVED_IDS = (
    "tg-links", "tg-nodes", "tg-selection", "tg-labels", "tg-direction", "tg-offset", "tg-legend",
    "col-links", "col-nodes", "col-selection", "colorby", "node-colorby", "ramp", "ramp-row", "widthby",
    "flt-col", "flt-op", "flt-val", "flt-apply", "flt-clear", "flt-count", "flt-note", "plugin-layers",
)  # fmt: skip


def test_the_layers_card_floats_in_the_map_overlay_and_holds_every_moved_control():
    overlay = HTML[HTML.index('<div id="map-overlay">') : HTML.index('id="split-handle"')]
    card = overlay[overlay.index('id="layers-card"') :]
    for element_id in MOVED_IDS:
        assert f'id="{element_id}"' in card, element_id
        assert HTML.count(f'id="{element_id}"') == 1, element_id  # moved, not copied
    assert 'id="layers-toggle"' in card and "aria-expanded=" in card and 'id="layers-hide"' in card


def test_the_view_panel_and_the_separate_legend_are_gone():
    for gone in ('id="dock-view"', 'data-panel="view"', 'id="legend"', "vsec"):
        assert gone not in HTML, gone
    assert 'id: "view"' not in js("panels.js") and "registerViewPanel" not in js("panels.js")
    assert "#legend" not in CSS and "#dock-view" not in CSS
    assert 'getElementById("legend")' not in js("panels.js") and '$("legend")' not in js("panels.js")


def test_each_layer_is_a_row_with_an_eye_and_links_and_nodes_have_a_by_picker_and_a_legend():
    card = HTML[HTML.index('id="layers-card"') : HTML.index('id="split-handle"')]
    for row in ("links", "nodes", "selection"):
        assert f'data-row="{row}"' in card
    assert card.count('class="eye"') == 3 and 'id="lg-links"' in card and 'id="lg-nodes"' in card
    links = card[card.index('data-row="links"') : card.index('data-row="nodes"')]
    assert 'id="lc-links-more"' in links and 'aria-controls="lc-links-opts"' in links
    nodes = card[card.index('data-row="nodes"') : card.index('data-row="selection"')]
    assert "lc-opts" not in nodes  # nothing extra for nodes yet
    panels = js("panels.js")
    assert 'class="eye"' in panels and "data-layer=" in panels  # overlays are eye rows too
    assert '$("lg-links").innerHTML' in panels and '$("lg-nodes").innerHTML' in panels
    assert "wb.layers.open" in js("viewmodel.js") and "CARD_KEY" in panels


def test_the_card_hides_in_table_view_because_it_lives_in_the_map_wrap():
    wrap = HTML[HTML.index('<div id="mapwrap">') : HTML.index('id="split-handle"')]
    assert 'id="layers-card"' in wrap
    assert '#stage[data-mode="table"] #mapwrap { display:none; }' in CSS
    assert re.search(r"#layers-card \{[^}]*max-height", CSS) and "overflow:auto" in CSS
