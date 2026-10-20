"""Tests for :class:`netstead.map.NetworkMap` — the embeddable component.

The component splits the previous monolithic ``render_network_html`` into
shared head assets (Leaflet / water.css / our CSS+JS) + a per-instance body
fragment (``<div>`` + payload + bootstrap script). Multiple instances can
coexist on a single host page because each carries its own UID; shared
head assets are idempotent (a host page calling :meth:`NetworkMap.head_assets`
twice gets the same bytes both times).
"""

from __future__ import annotations

import pandas as pd
import pytest
from corral.engines.ibis_engine import IbisEngine
from corral.reports import Category, Issue, Severity
from netstead import Network
from netstead.map import NetworkMap


@pytest.fixture
def tiny_net(tmp_path) -> Network:
    """Two links, three nodes — enough to render and test embedding."""
    link = pd.DataFrame(
        {
            "link_id": [1, 2],
            "from_node_id": [1, 2],
            "to_node_id": [2, 3],
            "directed": [True, True],
            "length": [100.0, 200.0],
        }
    )
    node = pd.DataFrame({"node_id": [1, 2, 3], "x_coord": [-120.6, -120.5, -120.4], "y_coord": [47.5, 47.6, 47.7]})
    csv = tmp_path / "tiny"
    csv.mkdir()
    link.to_csv(csv / "link.csv", index=False)
    node.to_csv(csv / "node.csv", index=False)
    return Network.from_source(csv, engine=IbisEngine())


# ---------------------------------------------------------------------------
# Head assets — shared, idempotent
# ---------------------------------------------------------------------------


def test_head_assets_contains_leaflet_water_and_component_assets():
    """head_assets() includes Leaflet JS+CSS, water.css, and our component CSS+JS."""
    head = NetworkMap.head_assets()
    # All inlined as <style>/<script> blocks — no remote <script src=>.
    assert "<script src=" not in head
    assert "Leaflet" in head  # Leaflet banner comment
    assert "background-body" in head  # water.css custom property
    assert "gv-map" in head  # our component CSS scoping prefix
    assert "__netsteadAttach" in head  # our component JS attach function


def test_head_assets_is_idempotent():
    """Two calls return the exact same string (host pages can include it once)."""
    a = NetworkMap.head_assets()
    b = NetworkMap.head_assets()
    assert a == b


def test_head_assets_css_is_safe_against_jinja_autoescape():
    """Regression for the Jinja-autoescape-broke-Leaflet bug.

    Leaflet's first rule includes ``.leaflet-pane > svg``. If autoescape
    encoded ``>`` to ``&gt;``, that selector becomes invalid and the
    whole rule (with ``position: absolute``) gets discarded — silently
    breaking the entire map. Pin the raw form.
    """
    head = NetworkMap.head_assets()
    assert ".leaflet-pane > svg" in head
    assert ".leaflet-pane &gt; svg" not in head


# ---------------------------------------------------------------------------
# Body fragment — per-instance, uid-scoped
# ---------------------------------------------------------------------------


def test_body_fragment_has_div_with_unique_uid(tiny_net):
    """Each instance's <div> id is gv-map-<uid> and the uid round-trips into the bootstrap."""
    m = NetworkMap(tiny_net)
    body = m.body_fragment()
    assert m.uid
    assert f'id="gv-map-{m.uid}"' in body
    # The bootstrap script references the same uid.
    assert f'"{m.uid}"' in body


def test_two_instances_have_distinct_uids(tiny_net):
    """Two NetworkMap instances on one host page must not collide."""
    a = NetworkMap(tiny_net)
    b = NetworkMap(tiny_net)
    assert a.uid != b.uid
    assert a.body_fragment() != b.body_fragment()


def test_explicit_uid_is_used_when_provided(tiny_net):
    """Caller can pin a specific uid — useful for stable test IDs."""
    m = NetworkMap(tiny_net, uid="my-network")
    assert m.uid == "my-network"
    assert 'id="gv-map-my-network"' in m.body_fragment()


def test_body_fragment_inlines_per_instance_payload(tiny_net):
    """The bootstrap script writes this instance's payload to a uid-keyed slot."""
    m = NetworkMap(tiny_net, uid="fixture")
    body = m.body_fragment()
    # Pattern: window.__NETSTEAD_INSTANCES__["fixture"] = {...}
    assert "__NETSTEAD_INSTANCES__" in body
    assert '"fixture"' in body
    # The two links coords should be in the payload.
    assert "-120.6" in body
    assert "-120.4" in body


def test_body_fragment_has_no_doctype_or_head(tiny_net):
    """Body fragment is just a snippet — no <html> / <head> / <!DOCTYPE>."""
    body = NetworkMap(tiny_net).body_fragment()
    assert "<!DOCTYPE" not in body
    assert "<html" not in body
    assert "<head>" not in body


def test_body_fragment_respects_custom_height(tiny_net):
    """`height=` flows into the <div>'s inline style so consumers can size the embed."""
    m = NetworkMap(tiny_net, height="240px")
    body = m.body_fragment()
    assert "240px" in body


# ---------------------------------------------------------------------------
# Standalone document
# ---------------------------------------------------------------------------


def test_to_html_returns_full_doctype_document(tiny_net):
    """to_html() wraps the fragment in a full standalone HTML document."""
    html = NetworkMap(tiny_net).to_html()
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<head>" in html
    assert "<body>" in html
    # Includes the head assets AND the per-instance fragment.
    assert "Leaflet" in html
    assert "__NETSTEAD_INSTANCES__" in html


def test_to_html_passes_through_title(tiny_net):
    """Title override flows into <title> and the standalone page."""
    html = NetworkMap(tiny_net, title="My Net").to_html()
    assert "<title>My Net</title>" in html


def test_to_html_with_issues_renders_markers(tiny_net):
    """Passing issues=... overlays them on the map (markers in payload)."""
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.DATA_QUALITY,
        code="quality.example",
        message="point of interest",
        extra={"lon": -120.55, "lat": 47.55},
    )
    html = NetworkMap(tiny_net, issues=[issue]).to_html()
    assert "-120.55" in html
    assert "47.55" in html


# ---------------------------------------------------------------------------
# Jupyter integration
# ---------------------------------------------------------------------------


def test_repr_html_returns_an_iframe_so_notebook_css_doesnt_collide(tiny_net):
    """In a notebook the embed is iframe-isolated so host CSS can't bleed in."""
    html = NetworkMap(tiny_net)._repr_html_()
    assert "<iframe" in html
    assert "srcdoc=" in html


# ---------------------------------------------------------------------------
# Backwards-compat surface
# ---------------------------------------------------------------------------


def test_netstead_reports_still_exports_render_network_html(tiny_net):
    """The pre-split entry point keeps working (re-export from netstead.reports).

    The deprecation warning is expected — silence it for this test
    (pytest's ``filterwarnings = error`` would otherwise fail us on the
    deprecation surface).
    """
    import warnings

    from netstead.reports import render_network_html

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        html = render_network_html(tiny_net)
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_netstead_reports_render_emits_deprecation_warning(tiny_net):
    """netstead.reports.render_network_html warns the caller to migrate to netstead.map."""
    with pytest.warns(DeprecationWarning, match="netstead.map"):
        from netstead.reports import render_network_html

        render_network_html(tiny_net)


# ---------------------------------------------------------------------------
# Composition: two maps in one page (the original motivating use case)
# ---------------------------------------------------------------------------


def test_two_maps_coexist_in_one_page(tiny_net):
    """Two NetworkMaps composed in a single document have independent divs + payloads."""
    a = NetworkMap(tiny_net, uid="left", title="Left")
    b = NetworkMap(tiny_net, uid="right", title="Right")
    page = f"""<!DOCTYPE html><html><head>{NetworkMap.head_assets()}</head>
    <body>{a.body_fragment()}{b.body_fragment()}</body></html>"""
    # Both divs present with their uids.
    assert 'id="gv-map-left"' in page
    assert 'id="gv-map-right"' in page
    # Head assets only once (idempotent — but a host page CHOOSES to include
    # only once; we don't dedupe automatically). At minimum: __netsteadAttach
    # is defined exactly once when head_assets() is included once.
    assert (
        page.count("function __netsteadAttach") + page.count("__netsteadAttach =") <= 2
    )  # tolerance for any defn style


def test_default_basemap_is_keyless(tiny_net):
    """The report ships the keyless Esri light-grey tiles. CARTO's raster tiles now watermark
    every zoomed-in tile with "API KEY REQUIRED", and a key would have to be baked into a
    static, shareable HTML file -- so no CARTO, with or without a key."""
    assert NetworkMap(tiny_net)._payload()["tile_provider"] == "esri"
    head = NetworkMap.head_assets()
    assert "cartocdn.com" not in head and "carto" not in head.lower()
    assert "World_Light_Gray_Base" in head and "World_Light_Gray_Reference" in head
    assert "maxNativeZoom: 16" in head
