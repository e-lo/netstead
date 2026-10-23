"""Static front-end checks: served, module graph consistent, JS syntax valid."""

import re
import shutil

import pytest
from fastapi.testclient import TestClient
from netstead.workbench import Session, build_app
from netstead.workbench.server import STATIC_DIR

JS_DIR = STATIC_DIR / "js"
_IMPORT = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w-]+\.js)"')
_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const|let)\s+([\w$]+)")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wb")
    return TestClient(build_app(Session(project_dir=tmp, environ={"NETSTEAD_CONFIG_DIR": str(tmp / "u")})))


def test_index_loads_main_module(client):
    html = client.get("/").text
    assert '<script type="module" src="/static/js/main.js">' in html
    assert "/static/app.css" in html


@pytest.mark.parametrize("name", sorted(p.name for p in JS_DIR.glob("*.js")))
def test_every_module_is_served_as_javascript(client, name):
    r = client.get(f"/static/js/{name}")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    # Revalidated on every load, so an upgrade never pairs a cached old module with a fresh one.
    assert r.headers["cache-control"] == "no-cache"


def test_an_unchanged_module_revalidates_as_not_modified(client):
    first = client.get("/static/js/main.js")
    again = client.get("/static/js/main.js", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304


def test_relative_imports_resolve_to_real_exports():
    exports = {p.name: set(_EXPORT.findall(p.read_text())) for p in JS_DIR.glob("*.js")}
    for path in JS_DIR.glob("*.js"):
        for names, target in _IMPORT.findall(path.read_text()):
            assert target in exports, f"{path.name} imports missing module {target}"
            for name in (n.strip() for n in names.split(",") if n.strip()):
                assert name in exports[target], f"{path.name} imports {name!r} not exported by {target}"


def test_every_element_id_used_by_js_exists_in_index():
    html = (STATIC_DIR / "index.html").read_text()
    ids = set(re.findall(r'id="([\w-]+)"', html))
    for path in JS_DIR.glob("*.js"):
        for used in re.findall(r'\$\("([\w-]+)"\)', path.read_text()):
            assert used in ids, f"{path.name} uses #{used} which index.html lacks"


#: Parses every module in one `node` process instead of spawning `node --check` once per
#: file. `--check` only ever validates its *first* positional argument, so checking N files
#: still meant N separate process starts; under `pytest -n auto` those extra Node startups
#: measurably raised the odds of hitting a rare, unrelated Node/V8 crash under host memory
#: pressure (SIGSEGV with empty stderr, reproduced here and in test_workbench_redact.py's
#: single `node` call -- see the commit that added this comment for the investigation).
#: `vm.SourceTextModule` parses (but does not execute or link) an ES module, which is exactly
#: what a syntax check needs.
_CHECK_ALL_MODULES_JS = """
import { readFileSync } from "node:fs";
import vm from "node:vm";

const failures = [];
for (const file of process.argv.slice(2)) {
    try {
        new vm.SourceTextModule(readFileSync(file, "utf8"), { identifier: file });
    } catch (error) {
        failures.push(`${file}: ${error.message}`);
    }
}
if (failures.length) {
    console.error(failures.join("\\n"));
    process.exit(1);
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_syntax(tmp_path, run_node):
    driver = tmp_path / "check_all_modules.mjs"
    driver.write_text(_CHECK_ALL_MODULES_JS)
    paths = sorted(JS_DIR.glob("*.js"))
    proc = run_node(["--no-warnings", "--experimental-vm-modules", str(driver), *(str(p) for p in paths)])
    assert proc.returncode == 0, proc.stderr


def test_header_has_open_menu_save_and_jobs_not_the_path_box_or_recent_select():
    html = (STATIC_DIR / "index.html").read_text()
    for element_id in (
        "open-wizard",
        "open-menu",
        "save-btn",
        "save-dialog",
        "valid-light",
        "jobs-btn",
        "jobs-panel",
        "wizard",
    ):
        assert f'id="{element_id}"' in html
    assert 'id="open-src"' not in html and 'id="open-go"' not in html and 'id="recent"' not in html


def test_open_menu_lists_recents_from_the_pure_module_then_browse():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index("</header>")]
    assert 'id="open-wizard"' in row and 'aria-haspopup="menu"' in row and 'aria-controls="open-menu"' in row
    button = row[row.index('<button id="open-wizard"') : row.index("</button>", row.index('id="open-wizard"'))]
    assert (
        'class="iconbtn"' in button and 'data-tip="Open a network' in button and 'aria-label="Open a network"' in button
    )
    assert "Open…" not in button and 'data-icon="folder-open"' in button  # an icon, no text
    assert '<div id="open-menu" role="menu"' in html and 'id="open-browse"' in html
    header = (JS_DIR / "header.js").read_text()
    assert 'from "./recents.js"' in header and "recentEntries(" in header and "openWizard()" in header
    assert "export function renderRecent(" not in header and '$("recent")' not in header  # the Recent select is gone
    assert 'e.key === "Escape"' in header


def test_the_save_dialog_owns_the_save_a_copy_flow():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index("</header>")]
    assert 'id="save-btn"' in row and 'data-tip="Save a copy…"' in row
    assert (
        row.index('id="open-wizard"')
        < row.index('id="save-btn"')
        < row.index('id="valid-light"')
        < row.index('id="count"')
    )
    dialog = html[
        html.index('<dialog id="save-dialog"') : html.index("</dialog>", html.index('<dialog id="save-dialog"'))
    ]
    for element_id in (
        "ed-save-name",
        "ed-save-format",
        "ed-save-fb",
        "ed-save",
        "save-warn",
        "save-warn-text",
        "save-accept",
        "save-cancel",
    ):
        assert f'id="{element_id}"' in dialog
    for fmt in ("parquet", "csv", "duckdb", "zip"):
        assert f'<option value="{fmt}"' in dialog
    pane = html[html.index('id="dock-edit"') : html.index("</aside>")]
    assert 'id="ed-save' not in pane and "Save a copy" not in pane
    save = (JS_DIR / "savedialog.js").read_text()
    assert "showModal()" in save and "createFileBrowser(" in save and "savePrompt(" in save
    assert "accept_warnings" in save and "window.confirm(" not in save  # the checkbox replaces the confirm
    assert "savedText(job.result)" in save and "check_failed" in save and "dispatchJob(action, onSaveEnded)" in save
    edits = (JS_DIR / "edits.js").read_text()
    assert "save_network" not in edits and "createFileBrowser" not in edits and "window.confirm(" not in edits
    assert "validatedText(job.result)" in edits  # a validation's outcome is still reported from the Edits module
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./savedialog.js"' in main and "wireSaveDialog()" in main and "onEditJob(e.job)" in main
    assert "run: () => openSaveDialog()" in (JS_DIR / "editcmds.js").read_text()  # the palette's Save a copy…


def test_the_validity_light_follows_the_server_state():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index("</header>")]
    light = row[row.index('id="valid-light"') - 30 : row.index(">", row.index('id="valid-light"'))]
    assert 'class="iconbtn vl-unknown"' in light and "aria-label=" in light and "data-tip=" in light
    css = (STATIC_DIR / "app.css").read_text()
    for cls in ("vl-unknown", "vl-stale", "vl-clean", "vl-warn", "vl-error"):
        assert f".{cls}" in css
    src = (JS_DIR / "validity.js").read_text()
    assert "import " not in src  # pure
    header = (JS_DIR / "header.js").read_text()
    assert "validityState(" in header and 'showPanel("validate")' in header


def test_wizard_modules_exist_and_are_wired_from_main():
    names = {p.name for p in JS_DIR.glob("*.js")}
    assert {"wizard.js", "filebrowser.js", "areapicker.js", "jobs.js"} <= names
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./wizard.js"' in main and 'from "./jobs.js"' in main


def test_llm_module_never_persists_or_stores_key_text():
    src = (JS_DIR / "llm.js").read_text()
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "./store.js"):
        assert banned not in src, f"llm.js must not use {banned}"


def test_llm_key_field_is_write_only():
    src = (JS_DIR / "llm.js").read_text()
    assert 'type="password"' in src and 'autocomplete="new-password"' in src
    assert "data-1p-ignore" in src and 'data-lpignore="true"' in src and "data-bwignore" in src
    assert "X-Netstead-Secrets" in src  # key writes and tests carry the secrets header


def test_header_has_the_llm_picker_and_panel():
    html = (STATIC_DIR / "index.html").read_text()
    for marker in ('id="nl-provider"', 'id="nl-model"', 'id="nl-dot"', 'id="nl-default"', 'id="nl-manage"'):
        assert marker in html
    assert 'id="llm-panel"' in html
    # The picker is a popover under the robot button in header row 1, not part of the Find panel's bar.
    header = html[html.index("<header>") : html.index("</header>")]
    assert 'id="nl-btn"' in header and 'aria-haspopup="dialog"' in header and 'id="nl-picker"' in header
    assert 'id="nl-dot"' in header.split('id="nl-btn"', 1)[1].split('id="nl-picker"', 1)[0]
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./llm.js"' in main and "llm:" in main  # wired, and subscribed to the `llm` SSE event


def test_pull_button_clears_only_its_own_model_on_job_events():
    # A pull in progress must stay disabled while a DIFFERENT model's pull job finishes: the
    # panel must not clear every "pulling" entry on each `llm` status snapshot (the bug this
    # replaces), but only the one model whose own `ollama_pull` job just ended.
    src = (JS_DIR / "llm.js").read_text()
    assert "pulling.clear()" not in src
    assert re.search(r"export function onLLMJob\(job\)", src)
    assert 'job.kind !== "ollama_pull"' in src
    main = (JS_DIR / "main.js").read_text()
    assert "onLLMJob" in main and 'from "./llm.js"' in main
    assert re.search(r"job:\s*e\s*=>\s*\{[^}]*onLLMJob\(e\.job\)", main)


def test_ollama_setup_links_point_at_the_published_guide():
    from pathlib import Path

    from netstead.llm.providers.ollama import SETUP_DOCS_URL

    pkg = Path(__file__).resolve().parents[1]
    site_url = next(
        line.split(":", 1)[1].strip()
        for line in (pkg / "mkdocs.yml").read_text().splitlines()
        if line.startswith("site_url:")
    )
    assert f"{site_url.rstrip('/')}/cookbook/local-llm-ollama/" == SETUP_DOCS_URL
    assert (pkg / "docs" / "cookbook" / "local-llm-ollama.md").is_file()
    src = (JS_DIR / "llm.js").read_text()
    assert f'const OLLAMA_GUIDE = "{SETUP_DOCS_URL}"' in src
    assert '"/api/llm/ollama/pull", { model }, SECRETS' in src  # the pull carries the write-guard header


def test_settings_dialog_hosts_the_language_models_section():
    html = (STATIC_DIR / "index.html").read_text()
    start, end = html.index('id="settings"'), html.index("<!-- /settings -->")
    dialog = html[start:end]
    for element_id in ("set-nav", "set-scope", "set-form", "set-close", "llm-panel", "llm-providers", "llm-quality"):
        assert f'id="{element_id}"' in dialog
    assert 'id="settings-btn"' in html and 'id="llm-close"' not in html


def test_settings_module_is_wired_from_main():
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./settings.js"' in main and "registerSection(" in main and "wireSettings()" in main


def test_the_header_is_one_row_and_the_find_panel_holds_the_utterance_box_not_the_picker():
    html = (STATIC_DIR / "index.html").read_text()
    header = html[html.index("<header>") : html.index("</header>")]
    assert header.count('class="hrow"') == 1 and 'id="nl-row"' not in html
    for element_id in ("net-select", "open-wizard", "save-btn", "valid-light", "viewmode", "jobs-btn", "settings-btn"):
        assert f'id="{element_id}"' in header
    assert 'id="cmd-btn"' in header
    for element_id in ("utterance", "go", "ask"):
        assert f'id="{element_id}"' not in header
    for element_id in ("nl-btn", "nl-picker", "nl-provider", "nl-model", "nl-default", "nl-manage"):
        assert f'id="{element_id}"' in header
    bar = html[html.index('id="find-bar"') : html.index('id="status-wrap"')]
    for element_id in ("utterance", "go", "ask"):
        assert f'id="{element_id}"' in bar
    for element_id in ("nl-picker", "nl-provider", "nl-model"):
        assert f'id="{element_id}"' not in bar
    selection = (JS_DIR / "selection.js").read_text()
    assert "barRoute(e)" in selection and '$("ask").onclick' in selection  # Enter selects; Ctrl/⌘+Enter asks
    header_js = (JS_DIR / "header.js").read_text()
    assert '$("utterance")' not in header_js and '$("go")' not in header_js


def test_basemap_swaps_in_place_on_a_viz_setting():
    assert "export function setBasemap(" in (JS_DIR / "map.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert "setBasemap(" in main and "viz" in main


def test_table_bar_has_a_scope_menu_not_the_old_checkbox():
    html = (STATIC_DIR / "index.html").read_text()
    assert 'id="tbl-scope"' in html and 'id="tbl-hint"' in html and 'id="tbl-tosel"' not in html
    for scope in ("all", "selection", "highlighted", "related"):
        assert f'<option value="{scope}"' in html


def test_fk_cells_link_to_their_target():
    table = (JS_DIR / "table.js").read_text()
    assert 'class="fk' in table and "jumpTo(" in table and "stopPropagation" in table  # an FK click is not a row click


def test_text_cells_edit_with_a_datalist_of_allowed_values():
    html = (STATIC_DIR / "index.html").read_text()
    table = (JS_DIR / "table.js").read_text()
    assert 'id="cell-opts"' in html and "<datalist" in html
    assert 'list="cell-opts"' in table and "cellOptions(" in table and "distinct" in table
    assert 'icon("alert-triangle"' in table and '"warn"' in table  # a warned cell is outlined and carries the icon
    assert "shouldApplyOnLeave(" in table and "onblur" in table  # leaving a cell applies changed text
    assert "#tbl-grid td.warn" in (STATIC_DIR / "app.css").read_text()


def test_related_module_feeds_the_map_and_the_rail():
    related = (JS_DIR / "related.js").read_text()
    assert '"related"' in related and "rel-badge" in related
    assert "relatedLayers(" in (JS_DIR / "map.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./related.js"' in main and "scheduleRelated" in main


def test_history_script_imports_come_from_the_server():
    js = (JS_DIR / "history.js").read_text(encoding="utf-8")
    assert "...imports" in js
    assert "SetActiveNetwork, Select" not in js  # no hard-coded core import list


def test_settings_and_schema_forms_share_one_field_model():
    settings = (JS_DIR / "settings.js").read_text()
    assert "function inputHTML" not in settings and 'from "./schemaform.js"' in settings
    settingsform = (JS_DIR / "settingsform.js").read_text()
    assert "function fieldKind" not in settingsform and 'from "./schemaform.js"' in settingsform


def test_map_draws_every_layer_from_the_registry_in_the_old_order():
    src = (JS_DIR / "map.js").read_text()
    assert 'layerRegistry.build("roadway"' in src
    core_ids = ("base", "selection", "related", "highlighted", "focus", "marker")
    for core_id in core_ids:
        assert f'reg("{core_id}"' in src, f"core layer group {core_id} must register on the registry"
    render = re.search(r"export function render\(\) \{.*?\n\}", src, re.S).group(0)
    assert "layers.push(" not in render  # nothing bypasses the registry
    order = [src.index(f'reg("{core_id}"') for core_id in core_ids]
    assert order == sorted(order)  # registered in drawing order (CORE_ORDER enforces it anyway)


def test_the_view_section_lists_overlays_from_the_registry():
    html = (STATIC_DIR / "index.html").read_text()
    view = html[html.index('id="view-section"') : html.index("</details>")]
    assert 'id="plugin-layers"' in view
    assert "export function renderPluginLayers(" in (JS_DIR / "panels.js").read_text()


def test_the_view_controls_live_in_the_find_panel_not_in_map_popovers():
    html = (STATIC_DIR / "index.html").read_text()
    for gone in ("layers-panel", "settings-panel", "btn-layers", "btn-settings"):
        assert f'id="{gone}"' not in html
    view = html[html.index('id="view-section"') : html.index("</details>")]
    for element_id in (
        "tg-links",
        "tg-nodes",
        "tg-selection",
        "tg-labels",
        "col-links",
        "col-nodes",
        "col-selection",
        "colorby",
        "node-colorby",
        "ramp",
        "widthby",
        "tg-direction",
        "tg-offset",
        "tg-legend",
        "flt-col",
        "flt-op",
        "flt-val",
        "flt-apply",
        "flt-clear",
        "plugin-layers",
    ):
        assert f'id="{element_id}"' in view, element_id
    find = html[html.index('id="dock-find"') : html.index('id="dock-inspect"')]
    assert 'id="view-section"' in find and 'id="hl-set"' in find and 'id="anchors"' in find
    panels = (JS_DIR / "panels.js").read_text()
    assert "node_color_by: e.target.value" in panels and "populateNodeColorby(" in panels
    assert "settings-panel" not in panels and "btn-layers" not in panels
    assert "renderLegend(s.server.style, s.prop, s.nodeProp)" in (JS_DIR / "main.js").read_text()
    assert "buildNodeStyle(" in (JS_DIR / "map.js").read_text()  # nodes take their colors from node_color_by


def test_the_dock_tabs_are_find_inspect_edit_cards_validate_assistant():
    html = (STATIC_DIR / "index.html").read_text()
    side = html[html.index('<aside id="side">') : html.index("</aside>")]
    order = [side.index(f'id="dock-{p}"') for p in ("find", "inspect", "edit", "validate", "assistant")]
    assert order == sorted(order)
    for gone in ("dock-details", "dock-selection", "dock-issues", "dock-edits"):
        assert gone not in html
    registrations = {
        "selection.js": 'title: "Find", icon: "search", workspace: ALL_WORKSPACES, order: 0',
        "workspaces.js": 'title: "Inspect", icon: "info", workspace: ALL_WORKSPACES, order: 10',
        "edits.js": 'title: "Edit", icon: "pencil", workspace: ALL_WORKSPACES, order: 20',
        "assistant.js": 'title: "Assistant", icon: "message", workspace: ALL_WORKSPACES, order: 40',
    }
    for name, text in registrations.items():
        assert text in (JS_DIR / name).read_text(), name
    edits = (JS_DIR / "edits.js").read_text()
    assert (
        'addPanel(CORE, { id: "validate", title: "Validate", icon: "shield-check", workspace: ALL_WORKSPACES, order: 30'
        in edits
    )
    # Order 25 is the project-cards plugin's Cards tab: core reserves the bare id for it.
    assert 'RESERVED_IDS = { cards: "project_cards" }' in (JS_DIR / "slots.js").read_text()
    assert 'const CARDS_PANEL = "cards"' in edits and 'hub.on("core.panels", syncCardButton)' in edits
    edit_pane = html[html.index('id="dock-edit"') : html.index('id="dock-validate"')]
    for element_id in ("ed-undo", "ed-redo", "iss-edit-count", "iss-edit", "ed-card"):
        assert f'id="{element_id}"' in edit_pane, element_id
    assert 'id="ed-card" disabled' in edit_pane  # enabled only once the plugin's panel is registered
    for old in ('showPanel("issues")', 'showPanel("edits")', 'showPanel("details")', 'showPanel("selection")'):
        for path in JS_DIR.glob("*.js"):
            assert old not in path.read_text(), f"{path.name} still shows {old}"


def test_validate_groups_repeated_structural_findings_and_offers_zoom_to():
    issues = (JS_DIR / "issues.js").read_text()
    assert 'from "./issuegroups.js"' in issues and "groupIssues(issues)" in issues and "groupSummary(g)" in issues
    assert 'class="iss grp' in issues and 'aria-expanded="false"' in issues and 'class="iss-members"' in issues
    assert (
        'icon("crosshair"' in issues
        and 'class="iconbtn iss-zoom" data-tip="Zoom to" aria-label="${esc(zoomLabel(i, where))}"' in issues
    )
    assert 'listHTML(page.issues, "iss-list")' in issues and 'listHTML(unlocated.issues, "iss-unlocated")' in issues
    assert "const id = `${prefix}-grp-${k}`" in issues  # group ids never collide across the two lists
    for zoom in ("fitLinks([a.link])", "flyToNode(a.node)", "flyTo(a.lonlat[0], a.lonlat[1])"):
        assert zoom in issues
    assert '"#dock-validate .iss[data-i]"' in issues and 'showPanel("validate")' in issues
    assert "import " not in (JS_DIR / "issuegroups.js").read_text()  # pure


def test_map_popovers_sit_over_the_map_not_inside_it():
    # MapLibre's container clips (overflow:hidden); a popover inside it lost its bottom in Split view.
    html = (STATIC_DIR / "index.html").read_text()
    wrap = html[html.index('<div id="mapwrap">') : html.index('id="split-handle"')]
    map_el = wrap[wrap.index('<div id="map">') : wrap.index('<div id="map-overlay">')]
    overlay = wrap[wrap.index('<div id="map-overlay">') :]
    for element_id in ("map-btns", "legend"):
        assert f'id="{element_id}"' in overlay and f'id="{element_id}"' not in map_el
    buttons = overlay[overlay.index('<div id="map-btns">') : overlay.index('<div id="legend">')]
    assert 'id="btn-view"' in buttons and 'data-icon="layers"' in buttons and 'data-tip="Map view settings"' in buttons
    assert 'id="boxsel"' in map_el  # the box-select rectangle is measured against the map
    css = (STATIC_DIR / "app.css").read_text()
    assert "#map-overlay { position:absolute; inset:0; z-index:3; pointer-events:none; }" in css
    assert "#map-overlay > * { pointer-events:auto; }" in css
    assert 'container: "map"' in (JS_DIR / "map.js").read_text()  # MapLibre still owns #map


def test_the_split_and_side_handles_are_separators_wired_from_main():
    html = (STATIC_DIR / "index.html").read_text()
    for element_id, orientation in (("split-handle", "horizontal"), ("side-handle", "vertical")):
        tag = html[html.index(f'id="{element_id}"') : html.index('data-icon="grip"', html.index(f'id="{element_id}"'))]
        assert 'role="separator"' in tag and f'aria-orientation="{orientation}"' in tag and 'tabindex="0"' in tag
    assert html.index('id="mapwrap"') < html.index('id="split-handle"') < html.index('id="tablepane"')
    assert html.index('</div>\n  <div id="side-handle"') < html.index('<aside id="side">')
    handles = (JS_DIR / "handles.js").read_text()
    assert 'from "./resize.js"' in handles and "setPointerCapture(" in handles and "resizeSoon()" in handles
    assert "localStorage.setItem(STORAGE_KEY, serializeSizes(sizes))" in handles
    assert "wireHandles()" in (JS_DIR / "main.js").read_text()


def test_the_drawer_is_the_dock_and_keeps_the_details_content():
    html = (STATIC_DIR / "index.html").read_text()
    side = html[html.index('<aside id="side">') : html.index("</aside>")]
    assert 'id="dock-tabs" role="tablist"' in side and side.index('id="dock-tabs"') < side.index('id="dock-find"')
    pane = side[side.index('id="dock-find"') : side.index('id="dock-inspect"')]
    assert 'data-panel="find"' in pane and 'role="tabpanel"' in pane
    for element_id in ("status-wrap", "hl-count", "hl-set", "hl-clear", "anchors", "fragment", "diag"):
        assert f'id="{element_id}"' in pane
    inspect = side[side.index('id="dock-inspect"') : side.index('id="dock-edit"')]
    assert 'id="details"' in inspect and 'id="details"' not in pane


def test_a_map_or_table_focus_brings_inspect_forward_but_a_panels_own_does_not():
    main = (JS_DIR / "main.js").read_text()
    on_focus = main[main.index("function onFocus(") : main.index("onFocusChanged();", main.index("function onFocus("))]
    assert 'if (f.from === "map" || f.from === "table") showPanel("inspect");' in on_focus
    assert 'f.from !== "map"' in on_focus  # a panel's focus still flies the map there
    issues = (JS_DIR / "issues.js").read_text()
    assert 'jumpTo(table, id, "panel")' in issues and 'focus: { table, id, from: "panel" }' in issues
    assert 'from: "panel"' in (JS_DIR / "editcmds.js").read_text()  # the right-click Fix… flow stays in Validate
    assert 'export async function jumpTo(table, id, from = "table")' in (JS_DIR / "table.js").read_text()


def test_the_workspace_strip_is_in_the_first_header_row_and_starts_hidden():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index("</header>")]
    assert '<nav id="ws-tabs" role="tablist" aria-label="Workspaces" hidden>' in row
    assert '<div id="dock-tabs" role="tablist" aria-label="Panels" hidden>' in html


def test_core_registers_inspect_and_details_through_the_slots():
    src = (JS_DIR / "workspaces.js").read_text()
    assert 'slots.addWorkspace(CORE, { id: "inspect"' in src and 'slots.addPanel(CORE, { id: "inspect"' in src
    main = (JS_DIR / "main.js").read_text()
    assert "registerCoreSlots()" in main and "wireWorkspaces(" in main


def test_context_menus_are_wired_on_the_map_the_grid_and_the_selection():
    html = (STATIC_DIR / "index.html").read_text()
    assert '<div id="ctxmenu" role="menu" aria-label="Commands" hidden></div>' in html
    pane = html[html.index('id="dock-find"') : html.index("</aside>")]
    assert 'id="sel-cmds-wrap" hidden' in pane and 'aria-haspopup="menu"' in pane
    map_js = (JS_DIR / "map.js").read_text()
    assert 'map.on("contextmenu"' in map_js and "handlers.onContextMenu(" in map_js
    table = (JS_DIR / "table.js").read_text()
    assert 'openContextMenu("row"' in table and "oncontextmenu" in table
    main = (JS_DIR / "main.js").read_text()
    assert 'openContextMenu("feature"' in main and "wireContextMenus(" in main


def test_the_palette_is_an_accessible_combobox_dialog():
    html = (STATIC_DIR / "index.html").read_text()
    palette = html[html.index('id="cmdk"') : html.index("<!-- /cmdk -->")]
    assert 'role="dialog" aria-modal="true"' in palette
    assert 'id="cmdk-input" role="combobox"' in palette and 'aria-controls="cmdk-list"' in palette
    assert 'id="cmdk-list" role="listbox"' in palette
    row = html[html.index("<header>") : html.index("</header>")]
    assert 'id="cmd-btn"' in row and 'aria-keyshortcuts="Control+K Meta+K"' in row


def test_dialogs_share_one_focus_trap():
    settings = (JS_DIR / "settings.js").read_text()
    assert "function trapTab" not in settings and 'from "./modal.js"' in settings
    assert 'from "./modal.js"' in (JS_DIR / "cmdpalette.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert "wirePalette(" in main and "registerCoreCommands()" in main


def test_schema_forms_reuse_the_shared_field_model_and_the_action_dialog_exists():
    formview = (JS_DIR / "formview.js").read_text()
    for name in ("fieldsFrom", "inputHTML", "parseControl", "setPath", "formErrors"):
        assert name in formview
    html = (STATIC_DIR / "index.html").read_text()
    dialog = html[html.index('id="actform"') : html.index("<!-- /actform -->")]
    for suffix in ("title", "desc", "body", "result", "run", "close"):
        element_id = f"actform-{suffix}"
        assert f'id="{element_id}"' in dialog
    assert "wireActionForm()" in (JS_DIR / "main.js").read_text()


def test_main_loads_plugins_after_core_and_forwards_their_events():
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./plugins.js"' in main and "loadPlugins()" in main
    assert main.index("registerCoreSlots()") < main.index("loadPlugins()")
    assert "await loadPlugins()" not in main  # never delays the map
    assert re.search(r"plugin:\s*e\s*=>\s*hub\.emit\(", main)
    assert 'hub.emit("core.history"' in main and 'hub.emit("core.job"' in main
    assert "onLayerError:" in main


def test_the_loader_isolates_each_plugin():
    # The paths themselves (import failure, no activate, timeout, "core") are node-tested against pluginload.js.
    src = (JS_DIR / "plugins.js").read_text()
    assert "await activatePlugins(" in src and "timeoutMs: ACTIVATE_TIMEOUT_MS" in src
    assert src.index("await activatePlugins(") < src.index("restoreWorkspace()")  # every plugin settles first
    assert "actionCommands(" in src and "openActionForm(" in src
    load = (JS_DIR / "pluginload.js").read_text()
    assert "rollback()" in load and "status.id === CORE" in load


def test_settings_has_a_plugins_section_registered_like_language_models():
    html = (STATIC_DIR / "index.html").read_text()
    dialog = html[html.index('id="settings"') : html.index("<!-- /settings -->")]
    for element_id in ("plugins-panel", "plugins-host", "plugins-note", "plugins-list"):
        assert f'id="{element_id}"' in dialog
    main = (JS_DIR / "main.js").read_text()
    assert 'registerSection("plugins", "Plugins", "plugins-panel"' in main and "wirePluginsPanel()" in main
    panel = (JS_DIR / "pluginspanel.js").read_text()
    assert '"app.disabled_plugins"' in panel and 'scope: "user"' in panel


def test_the_network_switcher_marks_derived_networks():
    header = (JS_DIR / "header.js").read_text()
    assert 'from "./tabs.js"' in header and "networkBadge(" in header


def test_the_view_mode_is_saved_per_workspace_not_in_one_key():
    table = (JS_DIR / "table.js").read_text()
    save = "localStorage.setItem(VIEWS_KEY, JSON.stringify(rememberView(savedViews(), store.get().workspace, mode)))"
    assert save in table
    assert "localStorage.setItem(LEGACY_VIEW_KEY" not in table  # the old single key is only read


def test_menus_and_tabs_name_their_groups_and_states():
    ctxmenu = (JS_DIR / "ctxmenu.js").read_text()
    assert '<div role="group" aria-label="${esc(sec.title)}">' in ctxmenu and '"aria-expanded"' in ctxmenu
    palette = (JS_DIR / "cmdpalette.js").read_text()
    assert '<li role="group" aria-label="${esc(g.title)}">' in palette
    html = (STATIC_DIR / "index.html").read_text()
    assert 'id="sel-cmds" aria-haspopup="menu" aria-expanded="false"' in html
    workspaces = (JS_DIR / "workspaces.js").read_text()
    assert "stripVisibility(" in workspaces and "setPanelRole(" in workspaces
    assert 'onStripKey(e, slots.workspaces.list(), showWorkspace, "wtab", { manual: true })' in workspaces


def test_validate_and_edit_are_core_dock_panels():
    html = (STATIC_DIR / "index.html").read_text()
    side = html[html.index('<aside id="side">') : html.index("</aside>")]
    order = [side.index(f'id="dock-{p}"') for p in ("inspect", "edit", "validate")]
    assert order == sorted(order)
    for panel in ("validate", "edit"):
        assert f'data-panel="{panel}"' in side and f'aria-labelledby="dtab-{panel}" hidden' in side
    edits = (JS_DIR / "edits.js").read_text()
    assert 'addPanel(CORE, { id: "validate"' in edits and 'addPanel(CORE, { id: "edit"' in edits
    assert "workspace: ALL_WORKSPACES" in edits
    main = (JS_DIR / "main.js").read_text()
    assert main.index("registerCoreSlots()") < main.index("registerEditPanels()") < main.index("wireWorkspaces(")
    assert "drawerTab" not in (JS_DIR / "store.js").read_text()  # the dock's dockPanel holds the choice


def test_issue_markers_and_validation_commands_use_the_slots():
    issues = (JS_DIR / "issues.js").read_text()
    assert 'addLayer(CORE, "roadway", "issues"' in issues and 'title: "Issues"' in issues
    assert '"issues"' not in (JS_DIR / "map.js").read_text()  # the layer lives in issues.js, not in map.js
    cmds = (JS_DIR / "editcmds.js").read_text()
    for command in ("run_validation", "fix_issue", "fix_record"):
        assert f'id: "{command}"' in cmds
    # R8 (2026-10-11): Fix… is the only core feature / row command, and only on a record that has an issue.
    assert cmds.count('contexts: ["feature", "row"]') == 1
    assert 'id: "fix_record", title: "Fix…", contexts: ["feature", "row"], when: c => Boolean(recordFixTarget(' in cmds
    assert 'runCore("fix_issue")' in issues  # an issue's Fix button goes through the commands registry
    main = (JS_DIR / "main.js").read_text()
    assert "registerIssueLayer()" in main and "registerEditCommands()" in main
    html = (STATIC_DIR / "index.html").read_text()
    pane = html[html.index('id="dock-validate"') : html.index('id="dock-assistant"')]
    for element_id in ("iss-run", "iss-report", "iss-status", "iss-list", "iss-unlocated", "iss-fixer"):
        assert f'id="{element_id}"' in pane
    assert 'id="iss-edit"' in html[html.index('id="dock-edit"') : html.index('id="dock-validate"')]  # warnings: Edit
    assert 'id="tg-issues"' not in html  # hidden through View → Overlays, not a toggle of its own


def test_cells_of_the_focused_row_are_edited_in_place():
    table = (JS_DIR / "table.js").read_text()
    assert "cellEditable(" in table  # the key column (decision 2) and geometry stay read-only
    assert "if (!tableVisible() || editing) { dirty = true; return; }" in table  # rows never re-render under the input
    assert "if (editing) { dirty = true; return; }" in table  # nor does a page already loading
    assert "dispatch(action)" in table and "cellAction(" in table
    assert 'openContextMenu("row"' in table  # the row menu wiring stays (R8: it opens only on a record with an issue)
    assert "edit_record" not in table and "delete_record" not in table


def test_record_edits_undo_and_save_are_registered_commands():
    cmds = (JS_DIR / "editcmds.js").read_text()
    assert 'id: "edit_record"' in cmds and 'id: "delete_record"' in cmds
    # R8 (2026-10-11): Edit record… and Delete record… are palette commands on the focused record, never menu items;
    # Fix… (Task 15) stays the only feature / row command.
    assert cmds.count('contexts: ["feature", "row"]') == 1
    assert 'title: "Edit record…", when: c => Boolean(c.focus)' in cmds
    assert 'title: "Delete record…", when: c => Boolean(c.focus)' in cmds
    for command in ("undo_edit", "save_copy"):
        assert f'id: "{command}"' in cmds
    fix = (JS_DIR / "fixeditor.js").read_text()
    assert 'from "./formview.js"' in fix and "schemaForm(" in fix and "recordSchema(" in fix
    assert "delete-plan" in fix and "deletePlanText(" in fix  # the dry-run cascade is shown before deleting
    assert "version: plan.version" in fix  # the delete runs against the version its plan was confirmed on
    assert "form.errors()" in fix and "changedValues(" in fix  # one EditCells with only what changed
    side = (JS_DIR / "side.js").read_text()
    for act in ("edit", "delete", "undo"):
        assert f'data-act="{act}"' in side
    # Saving over warnings is an explicit choice (the Save dialog's checkbox); a refused save (the server checks
    # again) is shown in the dialog too, and a save's and a validation's result are reported.
    save = (JS_DIR / "savedialog.js").read_text()
    assert "accept_warnings" in save and "savePrompt(job.payload)" in save and "savedText(job.result)" in save
    assert "validatedText(job.result)" in (JS_DIR / "edits.js").read_text()
    assert "onEditJob(e.job)" in (JS_DIR / "main.js").read_text()
    # Anchors with no coordinates are still listed in the Selection panel, as not on the map (escaped like the others).
    selection = (JS_DIR / "selection.js").read_text()
    assert "anchors_unplaced" in (JS_DIR / "selectmodel.js").read_text() and "not on the map" in selection
    html = (STATIC_DIR / "index.html").read_text()
    pane = html[html.index('id="dock-edit"') : html.index("</aside>")]
    for element_id in ("ed-undo", "ed-undo-why", "ed-status", "ed-list"):
        assert f'id="{element_id}"' in pane
    header = (JS_DIR / "header.js").read_text()
    assert "dirtyBadge(" in header and "networkBadge(" in header


def test_the_find_panel_owns_the_selection_markup():
    html = (STATIC_DIR / "index.html").read_text()
    pane = html.split('id="dock-find"', 1)[1].split("</section>", 1)[0]
    for el in (
        'id="status-wrap"',
        'id="sel-stale"',
        'id="anchors"',
        'id="fragment"',
        'id="diag"',
        'id="sel-export"',
        'id="sel-saved"',
    ):
        assert el in pane
    details = html.split('id="dock-inspect"', 1)[1].split("</section>", 1)[0]
    assert 'id="anchors"' not in details and 'id="details"' in details and 'id="hl-set"' not in details


def test_the_selection_panel_escapes_what_the_server_sends():
    js = (JS_DIR / "selection.js").read_text()
    # Selection names, anchor details and candidate labels all reach innerHTML only through esc().
    for raw in ("${x.name}", "${r.detail}", "${r.anchor}", "node ${c.nodeId}${c.placed", "${d}"):
        assert raw not in js, raw
    assert "${esc(x.name)}" in js and "${esc(r.detail)}" in js and "node ${esc(c.nodeId)}" in js
    assert 'aria-label="${esc(`Pin the ${r.role} anchor to node ${c.nodeId}`)}' in js  # the label escaped whole
    assert "window.confirm(`Delete the saved selection" in js  # Delete asks first


def test_layers_panel_has_width_by_and_a_filter_row():
    html = (STATIC_DIR / "index.html").read_text()
    for el in (
        'id="widthby"',
        'id="flt-col"',
        'id="flt-op"',
        'id="flt-val"',
        'id="flt-apply"',
        'id="flt-clear"',
        'id="flt-count"',
        'value="filtered"',
    ):
        assert el in html
    panels = (JS_DIR / "panels.js").read_text()
    assert 'type: "filter"' in panels and "width_by: e.target.value" in panels  # both are recorded Actions
    main = (JS_DIR / "main.js").read_text()
    assert "ev.record" in main and 'netPath(server.active, "filter")' in main
    assert "combinedFilter(" in (JS_DIR / "table.js").read_text()


def test_the_assistant_panel_and_the_ask_button_exist():
    html = (STATIC_DIR / "index.html").read_text()
    pane = html.split('id="dock-assistant"', 1)[1].split("</section>", 1)[0]
    for el in (
        'id="as-log"',
        'id="as-draft"',
        'id="as-input"',
        'id="as-send"',
        'id="as-clear"',
        'id="as-note"',
        'id="as-usage"',
    ):
        assert el in pane
    assert 'id="ask"' in html.split('id="find-bar"', 1)[1].split('id="status-wrap"', 1)[0]
    js = (JS_DIR / "assistant.js").read_text()
    assert 'addPanel(CORE, { id: "assistant"' in js and "workspace: ALL_WORKSPACES" in js
    assert 'id: "ask_assistant", title: "Ask the assistant…"' in js
    selection = (JS_DIR / "selection.js").read_text()
    assert "barRoute(e)" in selection and '$("ask").onclick' in selection  # Enter selects; Ctrl/⌘+Enter asks
    main = (JS_DIR / "main.js").read_text()
    assert "assistant: e => store.set({ assistant: e.assistant })" in main
    assert main.index("registerSelectionPanel()") < main.index("registerAssistantPanel()")


def test_model_text_is_never_written_as_markup():
    js = (JS_DIR / "assistant.js").read_text()
    assert "text.textContent = turn.error" in js  # the model's sentence and errors go in as text
    assert "${turn.text" not in js and "${t.text" not in js  # never interpolated into markup
    for raw in ("${r.summary}", "${r.result}", "${r.python}", "${r.label}", "${st.error}"):
        assert raw not in js, raw


def test_the_history_strip_marks_the_assistants_entries_but_scripts_do_not():
    js = (JS_DIR / "history.js").read_text()
    assert 'entry.via === "assistant"' in js
    script = js[js.index("export function sessionScript") : js.index("export function wireHistory")]
    assert "via ===" not in script  # "Session as Python" ignores how an Action arrived


def test_the_assistant_settings_section_is_registered():
    js = (JS_DIR / "assistant.js").read_text()
    assert 'registerSection("assistant-tools"' in js and '"assistant.plugins"' in js and '"assistant.actions"' in js
    # Not "assistant": that id is the generated form's own section (assistant.* settings), which must stay reachable.
    assert 'registerSection("assistant",' not in js
    assert "status.warnings" in js  # unknown names in the allowlists are listed
    assert 'id="assistant-panel"' in (STATIC_DIR / "index.html").read_text()
    assert "registerAssistantSettings()" in (JS_DIR / "main.js").read_text()


def test_column_filters_are_popovers_and_chips_not_header_inputs():
    html = (STATIC_DIR / "index.html").read_text()
    assert '<div id="col-filter" role="dialog" aria-label="Column filter" hidden></div>' in html
    pane = html[html.index('id="tbl-bar"') : html.index('id="tbl-pager"')]
    assert pane.index('id="tbl-chips"') < pane.index('id="tbl-grid-wrap"')  # the chip strip sits above the grid
    table = (JS_DIR / "table.js").read_text()
    assert "data-fcol" not in table and 'op: "contains"' not in table  # no header text boxes, no hard-wired op
    assert 'from "./gridfilter.js"' in table and "TBL.filters.conditions()" in table
    assert 'icon("filter"' in table and 'class="iconbtn fbtn' in table and "data-tip=" in table
    assert 'tablePath("distinct")' in table


def test_core_dock_panels_register_icons_and_tabs_render_them_with_tips():
    expected = {
        "selection.js": 'icon: "search"',
        "workspaces.js": 'icon: "info"',
        "edits.js": 'icon: "pencil"',
        "assistant.js": 'icon: "message"',
    }
    for name, needle in expected.items():
        assert needle in (JS_DIR / name).read_text(), name
    assert 'icon: "shield-check"' in (JS_DIR / "edits.js").read_text()
    ws = (JS_DIR / "workspaces.js").read_text()
    assert 'data-tip="${esc(item.title)}"' in ws and "aria-label=" in ws and "Object.hasOwn(ICONS" in ws
    icons = (JS_DIR / "icons.js").read_text()
    for name in ("search", "info", "pencil", "cards", "shield-check", "message"):
        assert f"{name}:" in icons or f'"{name}":' in icons, name
    plugin = (STATIC_DIR.parents[4] / "plugins/project-cards/netstead_project_cards/static/main.js").read_text()
    assert 'icon: "cards"' in plugin


def test_build_form_has_mode_checkboxes_and_a_folder_chooser():
    html = (STATIC_DIR / "index.html").read_text()
    form = html.split('data-step="options"', 1)[1].split('data-step="run"', 1)[0]
    assert 'id="opt-network-type"' not in form
    for mode in ("drive", "walk", "bike", "all"):
        assert f'<input type="checkbox" data-mode="{mode}"' in form
    # The chooser's container, the chosen-path display and a hint all sit in the step, and the path starts unset.
    for marker in ('id="opt-out-fb"', 'id="opt-outdir" class="unset"', 'id="opt-out-hint"'):
        assert marker in form
    wizard = (JS_DIR / "wizard.js").read_text()
    assert (
        "network_types: chosenModes()" in wizard
        and "network_type:" not in wizard.split("function buildAction", 1)[1][:600]
    )
    assert "Boolean(wz.outDir)" in wizard  # Estimate stays disabled until a folder is chosen
    assert (
        "pickFolder" in (JS_DIR / "filebrowser.js").read_text()
        and "data-use" in (JS_DIR / "filebrowser.js").read_text()
    )


def test_table_toolbar_has_the_related_steps_select():
    html = (STATIC_DIR / "index.html").read_text()
    bar = html.split('id="tbl-bar"', 1)[1].split('id="tbl-chips"', 1)[0]
    assert 'id="tbl-hops-wrap" hidden' in bar and '<select id="tbl-hops"' in bar and "Expand a hop" not in html


def test_wizard_has_extras_notice_slots_and_loads_them_read_only():
    html = (STATIC_DIR / "index.html").read_text()
    assert 'class="ex-slot" data-for="osm"' in html and 'class="ex-slot" data-for="overture"' in html
    assert 'id="ap-place-note"' in html
    wizard = (JS_DIR / "wizard.js").read_text()
    assert 'getJSON("/api/extras")' in wizard and "noticeHTML" in wizard and "clipboard.writeText" in wizard


def test_related_steps_control_has_an_info_popover():
    html = (STATIC_DIR / "index.html").read_text()
    wrap = html.split('id="tbl-hops-wrap"', 1)[1].split('id="tbl-chips"', 1)[0]
    assert 'id="tbl-hops-info"' in wrap and 'data-icon="info"' in wrap and 'data-tip="What is Related?"' in wrap
    assert 'id="tbl-hops-pop"' in wrap and 'aria-haspopup="dialog"' in wrap
