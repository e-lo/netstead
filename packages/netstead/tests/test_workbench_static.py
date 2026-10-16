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


def test_header_has_open_import_recent_and_jobs_not_the_path_box():
    html = (STATIC_DIR / "index.html").read_text()
    for element_id in ("open-wizard", "recent", "jobs-btn", "jobs-panel", "wizard"):
        assert f'id="{element_id}"' in html
    assert 'id="open-src"' not in html and 'id="open-go"' not in html


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
    # The picker sits right after the utterance box (P1b may reflow the header; keep this adjacency).
    assert html.index('id="utterance"') < html.index('id="nl-picker"') < html.index('id="go"')
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


def test_header_puts_the_utterance_and_picker_on_their_own_row():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index('id="nl-row"') : html.index("</header>")]
    for element_id in ("utterance", "nl-picker", "go"):
        assert f'id="{element_id}"' in row
    main_row = html[html.index("<header>") : html.index('id="nl-row"')]
    for element_id in ("net-select", "open-wizard", "recent", "viewmode", "jobs-btn", "settings-btn"):
        assert f'id="{element_id}"' in main_row


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
    assert 'class="fk"' in table and "jumpTo(" in table and "stopPropagation" in table  # an FK click is not a row click


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


def test_layers_popover_lists_overlays_from_the_registry():
    html = (STATIC_DIR / "index.html").read_text()
    panel = html[html.index('id="layers-panel"') : html.index('id="settings-panel"')]
    assert 'id="plugin-layers"' in panel
    assert "export function renderPluginLayers(" in (JS_DIR / "panels.js").read_text()


def test_the_drawer_is_the_dock_and_keeps_the_details_content():
    html = (STATIC_DIR / "index.html").read_text()
    side = html[html.index('<aside id="side">') : html.index("</aside>")]
    assert 'id="dock-tabs" role="tablist"' in side and side.index('id="dock-tabs"') < side.index('id="dock-details"')
    pane = side[side.index('id="dock-details"') :]
    assert 'data-panel="details"' in pane and 'role="tabpanel"' in pane
    for element_id in ("status-wrap", "hl-count", "hl-set", "hl-clear", "details", "anchors", "fragment", "diag"):
        assert f'id="{element_id}"' in pane


def test_the_workspace_strip_is_in_the_first_header_row_and_starts_hidden():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index('id="nl-row"')]
    assert '<nav id="ws-tabs" role="tablist" aria-label="Workspaces" hidden>' in row
    assert '<div id="dock-tabs" role="tablist" aria-label="Panels" hidden>' in html


def test_core_registers_inspect_and_details_through_the_slots():
    src = (JS_DIR / "workspaces.js").read_text()
    assert 'slots.addWorkspace(CORE, { id: "inspect"' in src and 'slots.addPanel(CORE, { id: "details"' in src
    main = (JS_DIR / "main.js").read_text()
    assert "registerCoreSlots()" in main and "wireWorkspaces(" in main


def test_context_menus_are_wired_on_the_map_the_grid_and_the_selection():
    html = (STATIC_DIR / "index.html").read_text()
    assert '<div id="ctxmenu" role="menu" aria-label="Commands" hidden></div>' in html
    pane = html[html.index('id="dock-details"') : html.index("</aside>")]
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
    row = html[html.index("<header>") : html.index('id="nl-row"')]
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
