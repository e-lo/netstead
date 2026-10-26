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
