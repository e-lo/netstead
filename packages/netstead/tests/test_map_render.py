"""Tests for :mod:`netstead.reports.html_map` — interactive network-on-map HTML viewer.

The renderer takes a :class:`~netstead.network.Network` and an optional
list of :class:`~corral.reports.Issue` overlays and produces one
self-contained HTML document with a Leaflet map pane on top and a
filterable findings table below. These tests pin the contract: the
output is a single file (no remote ``<script src>``), markers carry the
right data, popups respect the no-WKT rule, and the bridge
(``data-issue-id`` on each marker + matching row) is in place.
"""

from __future__ import annotations

import re

import pandas as pd
from corral.engines.ibis_engine import IbisEngine
from corral.reports import Category, Issue, Severity, ValidationReport
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.map import render_network_html, render_validation_html

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _osm_network(tmp_path) -> Network:
    """Tiny in-memory network with osm_way_id provenance."""
    link = pd.DataFrame(
        {
            "link_id": [1, 2],
            "from_node_id": [10, 11],
            "to_node_id": [11, 12],
            "directed": [True, True],
            "length": [100.0, 200.0],
            "osm_way_id": [5001, 5002],
            "geometry": [
                "LINESTRING (-120.6 47.5, -120.5 47.6)",
                "LINESTRING (-120.5 47.6, -120.4 47.7)",
            ],
        }
    )
    node = pd.DataFrame(
        {
            "node_id": [10, 11, 12],
            "x_coord": [-120.6, -120.5, -120.4],
            "y_coord": [47.5, 47.6, 47.7],
        }
    )
    csv_dir = tmp_path / "osm_net"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    return Network.from_source(csv_dir, engine=IbisEngine())


def _leavenworth_network() -> Network:
    """The bundled non-OSM CSV fixture."""
    return Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())


# ---------------------------------------------------------------------------
# Generality — network alone
# ---------------------------------------------------------------------------


def test_marker_payload_carries_row_props_with_pk_for_inline_editor(tmp_path):
    """Each row-level marker carries the row's full props (incl. PK) for the editor.

    The browser-side "Propose fix" mini-editor needs the PK (link_id /
    node_id) to write a stable edit-log entry, and the current value at
    each column to pre-fill the form. Both come from ``marker.row_props``.
    """
    net = _osm_network(tmp_path)
    report = ValidationReport(source="t.gmns", spec_version="0.97")
    report.add(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        column="length",
        row=0,
    )
    html = render_validation_html(net, report)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    import json as _json

    data = _json.loads(payload_match.group(1))
    located_markers = [m for m in data["markers"] if m["row_props"]]
    assert located_markers, "expected at least one row-level marker with row_props"
    m = located_markers[0]
    # Stable PK is in the row props so the JS editor can build an edit entry.
    assert "link_id" in m["row_props"]
    # The flagged column is surfaced so the editor pre-fills it.
    assert m["column"] == "length"


def test_payload_carries_nodes_as_toggleable_layer(tmp_path):
    """The payload must expose a ``nodes`` layer so the JS can render + toggle it.

    Users on a clean (no-error) network often just want to see what
    they've got — toggling nodes on/off is the obvious next move.
    """
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    payload = payload_match.group(1)
    # Layers structure with a `nodes` entry that carries point coords.
    assert '"id": "nodes"' in payload or '"id":"nodes"' in payload
    # The _osm_network fixture has 3 nodes at x = -120.6, -120.5, -120.4.
    assert "-120.6" in payload
    assert "-120.4" in payload


def test_payload_layers_array_is_extensible(tmp_path):
    """``layers`` is a list of dicts so future layer types slot in without renderer changes."""
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    import json as _json

    data = _json.loads(payload_match.group(1))
    assert isinstance(data["layers"], list)
    ids = {layer["id"] for layer in data["layers"]}
    assert "links" in ids
    assert "nodes" in ids
    for layer in data["layers"]:
        # Every layer must carry the four keys the JS expects.
        assert {"id", "label", "type", "default_on"} <= set(layer)


def test_underlay_uses_geometry_table_when_no_inline_geometry(tmp_path):
    """Leavenworth-shape: link.geometry_id → geometry.geometry WKT polyline.

    The payload's ``links`` underlay must carry the real polyline coords
    from the joined geometry table, not just straight from→to segments.
    """
    link = pd.DataFrame(
        {
            "link_id": [1],
            "from_node_id": [1],
            "to_node_id": [2],
            "directed": [True],
            "length": [100.0],
            "geometry_id": [10],
        }
    )
    node = pd.DataFrame({"node_id": [1, 2], "x_coord": [-120.6, -120.5], "y_coord": [47.5, 47.6]})
    geometry = pd.DataFrame(
        {
            "geometry_id": [10],
            # 4-vertex polyline; the inner two vertices would be lost in a
            # straight from→to fallback.
            "geometry": ["LINESTRING (-120.6 47.5, -120.55 47.55, -120.52 47.58, -120.5 47.6)"],
        }
    )
    csv_dir = tmp_path / "geom_table_net"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    geometry.to_csv(csv_dir / "geometry.csv", index=False)
    net = Network.from_source(csv_dir, engine=IbisEngine())

    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    payload = payload_match.group(1)
    assert "-120.55" in payload
    assert "-120.52" in payload


def test_link_polylines_carry_props_for_tooltips(tmp_path):
    """Each link feature in the payload must carry a ``props`` dict for hover tooltips.

    Hovering a link in the viewer should surface link_id, name, length,
    facility_type, etc. — otherwise the network is just abstract lines
    and users can't tell which polyline maps back to which row.
    """
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    import json as _json

    data = _json.loads(payload_match.group(1))
    links_layer = next(layer for layer in data["layers"] if layer["id"] == "links")
    assert links_layer.get("items"), "links layer must have an `items` array"
    first = links_layer["items"][0]
    assert "coords" in first
    assert isinstance(first.get("props"), dict)
    # link_id is the natural identifier — must be on every item.
    assert "link_id" in first["props"]


def test_node_points_carry_props_for_tooltips(tmp_path):
    """Each node feature must carry a ``props`` dict so hovering surfaces node_id etc."""
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    import json as _json

    data = _json.loads(payload_match.group(1))
    nodes_layer = next(layer for layer in data["layers"] if layer["id"] == "nodes")
    assert nodes_layer.get("items"), "nodes layer must have an `items` array"
    first = nodes_layer["items"][0]
    assert "coord" in first
    assert isinstance(first.get("props"), dict)
    assert "node_id" in first["props"]


def test_props_skip_long_or_internal_columns(tmp_path):
    """``geometry`` WKT strings must NOT be dumped into the tooltip props — far too long."""
    # Build a network with a `geometry` column on the link table; props
    # serialization must not expose it.
    link = pd.DataFrame(
        {
            "link_id": [1],
            "from_node_id": [1],
            "to_node_id": [2],
            "directed": [True],
            "length": [100.0],
            "geometry": ["LINESTRING (-120.6 47.5, -120.5 47.6)"],
        }
    )
    node = pd.DataFrame({"node_id": [1, 2], "x_coord": [-120.6, -120.5], "y_coord": [47.5, 47.6]})
    csv_dir = tmp_path / "with_wkt"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    net = Network.from_source(csv_dir, engine=IbisEngine())

    html = render_network_html(net)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    # The WKT string itself must NEVER appear in the rendered HTML — it's
    # huge, it bloats tooltips, and the user already sees the geometry as a
    # polyline on the map.
    assert "LINESTRING" not in html


def test_inlined_css_preserves_child_combinator_selectors(tmp_path):
    """Inlined CSS must not be HTML-escaped, or `>` becomes `&gt;` and rules silently fail.

    Regression for a real silent breakage: Jinja autoescape converted the
    `>` in Leaflet's `.leaflet-pane > svg` selector into `&gt;`, which made
    the whole comma-separated rule invalid per CSS spec. The first rule
    in leaflet.min.css groups `.leaflet-pane`, `.leaflet-tile`,
    `.leaflet-pane > svg`, and others under `position: absolute` — when
    that rule was discarded, the panes used `position: static` in
    document flow, the tile pane stretched to fit absolutely-positioned
    tile transforms, and the SVG overlay (with all the link polylines)
    rendered hundreds of pixels below the visible map.
    """
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    # Leaflet's first rule has `.leaflet-pane > svg`. The `>` MUST survive
    # raw — `&gt;` here means autoescape leaked into the <style> block.
    assert ".leaflet-pane > svg" in html
    assert ".leaflet-pane &gt; svg" not in html


def test_render_network_html_no_issues_returns_self_contained_html(tmp_path):
    """``render_network_html(network, None)`` works — network alone."""
    net = _osm_network(tmp_path)
    html = render_network_html(net)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    # Single self-contained file: NO remote script/style references.
    assert "<script src=" not in html
    assert '<link rel="stylesheet"' not in html
    assert "unpkg.com" not in html
    assert "cdn.jsdelivr.net" not in html
    # Leaflet is inlined (we just look for its banner comment).
    assert "Leaflet" in html


def test_render_network_html_empty_issues_treated_like_none(tmp_path):
    """An empty issue list renders identically to ``issues=None`` (no overlays)."""
    net = _osm_network(tmp_path)
    html = render_network_html(net, [])
    assert "Findings (0)" not in html  # the table section is not rendered for empty
    # Map div is still present.
    assert 'id="gv-map-' in html


# ---------------------------------------------------------------------------
# Marker / popup contract
# ---------------------------------------------------------------------------


def test_explicit_coord_issue_gets_a_marker(tmp_path):
    """An issue with explicit lon/lat in ``extra`` becomes a map marker."""
    net = _osm_network(tmp_path)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.DATA_QUALITY,
        code="quality.example",
        message="point of interest",
        extra={"lon": -120.55, "lat": 47.55},
    )
    html = render_network_html(net, [issue])
    # The payload blob is JSON — search for the lon coordinate as a number.
    assert "-120.55" in html
    assert "47.55" in html


def test_link_issue_on_osm_network_popup_has_edit_in_osm(tmp_path):
    """OSM-sourced link issue should produce an Edit in OSM link in the table."""
    net = _osm_network(tmp_path)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0: free_speed is null",
        table="link",
        row=0,
    )
    html = render_network_html(net, [issue])
    # iD URL for way 5001 should be present — as HTML-escaped markup, as a raw
    # query string, or (inside the map payload's <script> block) as the
    # JSON-safe & escape used to keep "&" from being misread as markup.
    assert (
        "https://www.openstreetmap.org/edit?editor=id&amp;way=5001" in html
        or "https://www.openstreetmap.org/edit?editor=id&way=5001" in html
        or "editor=id\\u0026way=5001" in html
    )


def test_link_issue_on_non_osm_network_has_no_osm_link(tmp_path):
    """Non-OSM network: NO openstreetmap.org/edit URL anywhere.

    Hand-built fixture — the bundled Leavenworth fixture now carries OSM
    provenance, so we need a deliberately non-OSM mini-network.
    """
    link = pd.DataFrame(
        {
            "link_id": [1],
            "from_node_id": [1],
            "to_node_id": [2],
            "directed": [True],
            "length": [100.0],
        }
    )
    node = pd.DataFrame({"node_id": [1, 2], "x_coord": [-120.6, -120.5], "y_coord": [47.5, 47.6]})
    csv_dir = tmp_path / "non_osm"
    csv_dir.mkdir()
    link.to_csv(csv_dir / "link.csv", index=False)
    node.to_csv(csv_dir / "node.csv", index=False)
    net = Network.from_source(csv_dir, engine=IbisEngine())
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    html = render_network_html(net, [issue])
    assert "openstreetmap.org/edit" not in html


def test_popup_has_no_wkt_geometry_string(tmp_path):
    """Popups must not dump the raw WKT LINESTRING — protect against giant tooltips."""
    net = _osm_network(tmp_path)
    issue = Issue(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0: free_speed is null",
        table="link",
        row=0,
    )
    html = render_network_html(net, [issue])
    # The geometry column in the source DOES contain "LINESTRING (...)" but
    # that's never echoed into the payload or table.
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None, "expected per-instance __NETSTEAD_INSTANCES__ slot"
    payload = payload_match.group(1)
    assert "LINESTRING" not in payload


# ---------------------------------------------------------------------------
# Marker → row bridge
# ---------------------------------------------------------------------------


def test_each_marker_and_table_row_share_an_issue_id(tmp_path):
    """Every located issue's marker and its table row carry a matching data-issue-id.

    The marker → row bridge is a validation-report feature (a standalone
    map has no findings table, so there's nothing to bridge TO).
    """
    net = _osm_network(tmp_path)
    report = ValidationReport(source="t.gmns", spec_version="0.97")
    report.add(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    report.add(
        severity=Severity.WARNING,
        category=Category.DATA_QUALITY,
        code="quality.example",
        message="point",
        extra={"lon": -120.55, "lat": 47.55},
    )
    html = render_validation_html(net, report)
    payload_match = re.search(r"\[\"[a-zA-Z0-9_-]+\"\] = (\{.*?\});", html, re.S)
    assert payload_match is not None
    payload = payload_match.group(1)
    issue_ids_in_payload = re.findall(r'"issue_id":\s*"([^"]+)"', payload)
    assert len(issue_ids_in_payload) >= 1
    for iid in issue_ids_in_payload:
        assert f'data-issue-id="{iid}"' in html


def test_unlocated_issue_goes_to_sidebar_with_issue_id():
    """A cross-cutting issue with no coords appears in the validation-report sidebar."""
    net = _leavenworth_network()
    report = ValidationReport(source="lw.gmns", spec_version="0.97")
    report.add(
        severity=Severity.ERROR,
        category=Category.STRUCTURAL,
        code="structural.missing_table",
        message="some required table missing",
    )
    html = render_validation_html(net, report)
    assert "Unlocated findings" in html
    assert "data-unlocated-issue-id=" in html
    assert "structural.missing_table" in html


def test_payload_contains_severity_code_message_for_each_marker(tmp_path):
    """Marker payload includes the popup fields (severity, code, message, fix_hint)."""
    net = _osm_network(tmp_path)
    issues = [
        Issue(
            severity=Severity.ERROR,
            category=Category.SCHEMA,
            code="schema.specific_code_xyz",
            message="meaningful message about row 0",
            table="link",
            row=0,
            fix_hint="hint about fixing",
        ),
    ]
    html = render_network_html(net, issues)
    assert "schema.specific_code_xyz" in html
    assert "meaningful message about row 0" in html
    assert "hint about fixing" in html


# ---------------------------------------------------------------------------
# render_validation_html wrapper
# ---------------------------------------------------------------------------


def test_render_validation_html_wraps_render_network_html(tmp_path):
    """The validation wrapper passes through report.issues + a source-aware title."""
    net = _osm_network(tmp_path)
    report = ValidationReport(source="my_net.gmns", spec_version="0.97")
    report.add(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )
    html = render_validation_html(net, report)
    assert "my_net.gmns" in html  # source in title
    assert "schema.required" in html  # the issue rendered


# ---------------------------------------------------------------------------
# Header counters
# ---------------------------------------------------------------------------


def test_header_shows_located_vs_unlocated_split(tmp_path):
    """Validation-report header counter shows N on map · M unlocated."""
    net = _osm_network(tmp_path)
    report = ValidationReport(source="t.gmns", spec_version="0.97")
    report.add(
        severity=Severity.WARNING,
        category=Category.SCHEMA,
        code="schema.required",
        message="link row 0",
        table="link",
        row=0,
    )  # located via link midpoint
    report.add(
        severity=Severity.ERROR,
        category=Category.STRUCTURAL,
        code="structural.x",
        message="cross-cutting",
    )  # unlocated
    html = render_validation_html(net, report)
    assert "1 on map" in html
    assert "1 unlocated" in html


def test_a_data_value_cannot_close_the_payload_script(tmp_path):
    """Messages quote data values (a street name): one holding ``</script>`` must stay inside the JSON blob."""
    net = _osm_network(tmp_path)
    evil = "</script><script>alert(1)</script>"
    issue = Issue(Severity.WARNING, Category.SCHEMA, "schema.required", evil, table="link", row=0)
    html = render_validation_html(net, ValidationReport(issues=[issue]))
    assert "<script>alert(1)" not in html


def test_the_payload_script_still_parses_to_the_original_data(tmp_path):
    """Escaping the payload for the ``<script>`` block must round-trip: JSON.parse sees the real value back."""
    import json

    net = _osm_network(tmp_path)
    evil = "</script><script>alert(1)</script> & tricky \u2028 \u2029 line breaks"
    issue = Issue(Severity.WARNING, Category.SCHEMA, "schema.required", evil, table="link", row=0)
    html = render_validation_html(net, ValidationReport(issues=[issue]))

    assignment_prefix = r"\(window\.__NETSTEAD_INSTANCES__ = window\.__NETSTEAD_INSTANCES__ \|\| \{\}\)"
    assignment_suffix = r"\s*=\s*(\{.*\});\s*\(window\.__NETSTEAD_QUEUE__"
    match = re.search(
        assignment_prefix + r'\["[^"]+"\]' + assignment_suffix,
        html,
        re.DOTALL,
    )
    assert match, "could not find the embedded payload assignment in the rendered HTML"
    payload = json.loads(match.group(1))
    messages = [marker["message"] for marker in payload.get("markers", [])]
    assert evil in messages


# ---------------------------------------------------------------------------
# Grouped unlocated findings + resource inventory (2026-10-20 beta feedback)
# ---------------------------------------------------------------------------


def _leavenworth_html() -> tuple[str, str]:
    net = _leavenworth_network()
    report = net.validate()
    return render_validation_html(net, report), str(report.source)


def test_unlocated_findings_are_collapsed_by_default_with_the_count():
    html, _ = _leavenworth_html()
    match = re.search(r'<details class="gv-unlocated"([^>]*)>\s*<summary>Unlocated findings \((\d+)\)</summary>', html)
    assert match, "unlocated block missing or not a <details>"
    assert "open" not in match.group(1)
    assert match.group(2) == "17"


def test_source_path_appears_exactly_once_in_the_report():
    html, source = _leavenworth_html()
    assert html.count(source) == 1


def test_grouped_structural_findings_render_as_one_line_with_the_resource_list():
    html, _ = _leavenworth_html()
    start = html.index('<details class="gv-unlocated"')
    unlocated = html[start : html.index("</details>", start)]
    assert unlocated.count("structural.missing_optional_resource") == 1
    assert unlocated.count("optional resources absent:") == 1
    assert "zone, config, curb_seg" in unlocated  # every name is listed, not truncated
    assert "fk.unverifiable" in unlocated
    assert unlocated.count("<li") == 2
    # Every list entry still links to a findings-table row.
    assert unlocated.count("data-unlocated-issue-id=") == 2


def test_resources_table_lists_present_and_absent_spec_tables():
    html, _ = _leavenworth_html()
    start = html.index('<details class="gv-resources"')
    resources = html[start : html.index("</details>", start)]
    assert "Resources (9 of 25 present)" in resources
    rows = re.findall(r"<tr data-resource=\"(\w+)\" data-present=\"(\w+)\">(.*?)</tr>", resources, flags=re.DOTALL)
    by_name = {name: (present, body) for name, present, body in rows}
    assert by_name["link"][0] == "true"
    assert "required" in by_name["link"][1] and "339" in by_name["link"][1]
    assert by_name["zone"][0] == "false"
    assert "optional" in by_name["zone"][1]
    assert html.index("gv-resources") < html.index("gv-unlocated") < html.index("gv-table-section")


def test_findings_table_messages_drop_the_repeated_source_lead_in():
    html, source = _leavenworth_html()
    assert f"package source &#39;{source}&#39;" not in html
    assert "optional resource &#39;zone&#39; is not present" in html
