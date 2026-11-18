"""Host API 1.10 (browser only): a plugin's view in the right-hand Details panel (``wb.showDetails`` /
``wb.closeDetails``), ``wb.highlight({links, zoom: true})`` fitting the map to the links, and the ``zoom-in`` icon."""

from __future__ import annotations

from netstead.workbench.plugins import HOST_API
from netstead.workbench.plugins.spec import api_compatible
from netstead.workbench.server import STATIC_DIR

JS = STATIC_DIR / "js"


def test_this_netstead_provides_host_api_1_10():
    assert HOST_API == "1.10" and api_compatible("1.10") and api_compatible("1.9") and not api_compatible("1.11")


# ---------------------------------------------------------------------------- detailsview.js: one thing at a time

VIEWS = """(() => {
  const log = [];
  let focus = { table: "link", id: 7 };
  const views = createDetailsViews({
    mount: title => { log.push(["mount", title]); return { title }; },
    unmount: () => log.push(["unmount"]),
    clearFocus: () => { log.push(["clearFocus"]); focus = null; },
    onError: e => log.push(["error", e.message]),
  });
  const spec = (id, title, extra = {}) => ({ id, title,
    render: el => { log.push(["render", id, el.title]); return () => log.push(["cleanup", id]); },
    onClose: () => log.push(["onClose", id]), ...extra });
  const out = {};
  views.show("probe", spec("card", "Card · A"));
  out.first = [log.splice(0), views.current(), focus];
  views.show("probe", spec("card", "Card · B"));       // the same view again: redrawn, no onClose
  out.again = log.splice(0);
  views.show("other", spec("thing", "Thing"));         // another plugin's view replaces it: its onClose runs
  out.replaced = log.splice(0);
  out.wrongOwner = [views.close("probe"), views.close("other", "nope")];
  out.closed = [views.close("other", "thing"), log.splice(0), views.current()];  // the owner's own close: no onClose
  views.show("probe", spec("card", "Card"));
  log.splice(0);
  out.dismissed = [views.dismiss(), log.splice(0), views.dismiss()];  // the user's close or Escape, or a focused record
  views.show("probe", spec("bad", "Bad", { onClose: () => { throw new Error("boom"); } }));
  views.dismiss();
  out.throwing = log.splice(0).filter(e => e[0] === "error");
  const refused = [];
  for (const bad of [{}, { id: "x" }, { id: "", render() {} }]) {
    try { views.show("probe", bad); } catch (e) { refused.push(e.message); }
  }
  out.refused = refused;
  views.show("probe", { id: "plain", render: () => null });
  out.untitled = views.current().title;
  return out;
})()"""


def test_one_view_at_a_time_and_who_hears_about_it(node_module):
    got = node_module("detailsview.js", ["createDetailsViews"], VIEWS)
    assert got["first"] == [
        [["clearFocus"], ["mount", "Card · A"], ["render", "card", "Card · A"]],
        {"owner": "probe", "id": "card", "title": "Card · A"},
        None,  # showing a view clears the focused record
    ]
    assert got["again"] == [
        ["cleanup", "card"],
        ["unmount"],
        ["clearFocus"],
        ["mount", "Card · B"],
        ["render", "card", "Card · B"],
    ]
    assert got["replaced"][:3] == [["cleanup", "card"], ["onClose", "card"], ["unmount"]]
    assert got["wrongOwner"] == [False, False]
    assert got["closed"] == [True, [["cleanup", "thing"], ["unmount"]], None]
    assert got["dismissed"] == [True, [["cleanup", "card"], ["onClose", "card"], ["unmount"]], False]
    assert got["throwing"] == [["error", "boom"]]  # reported, and the panel still closes
    assert got["refused"] == [
        "probe: showDetails needs an id",
        "probe: showDetails needs render(el)",
        "probe: showDetails needs an id",
    ]
    assert got["untitled"] == "Details"


# ---------------------------------------------------------------------------- wbhost.js: the plugin's side

HOST = """await (async () => {
  const sets = [], fits = [], errors = [], shown = [], closed = [];
  const base = { onError: (id, phase, e) => errors.push([id, phase, e.message]),
    store: { get: () => ({}), set: patch => sets.push({ ...patch, highlights: [...patch.highlights] }),
      subscribe: () => () => {} } };
  const details = { show: (owner, spec) => { shown.push([owner, spec.id, spec.title]); spec.render("EL");
      if (spec.onClose) spec.onClose(); },
    close: (owner, id = null) => { closed.push([owner, id]); return true; } };
  const full = createWb("probe", { ...base, details, fitLinks: ids => fits.push(ids) });
  const bare = createWb("probe", base).wb;
  const { wb } = full;
  wb.showDetails({ id: "card", title: "Card · A",
    render: el => () => { throw new Error("cleanup " + el); }, onClose: () => { throw new Error("closing"); } });
  wb.highlight({ links: [1, 2], zoom: true });
  wb.highlight({ links: [3] });
  wb.highlight();
  const closedOne = wb.closeDetails("card");
  full.rollback();
  return { shown, closedOne, closed, sets, fits, errors,
    bare: [typeof bare.showDetails, typeof bare.closeDetails, typeof wb.showDetails] };
})()"""


def test_wb_show_details_is_there_only_where_the_host_has_the_panel(node_module):
    got = node_module("wbhost.js", ["createWb"], HOST)
    assert got["bare"] == ["undefined", "undefined", "function"]  # a plugin feature-tests typeof wb.showDetails
    assert got["shown"] == [["probe", "card", "Card · A"]]
    assert got["errors"] == [["probe", "details close", "closing"]]  # a throwing onClose is reported, not passed on
    assert got["closedOne"] is True
    assert got["closed"] == [["probe", "card"], ["probe", None]]  # the rollback closes any view the plugin showed


def test_wb_highlight_zoom_fits_the_map_now_and_when_the_network_opens(node_module):
    got = node_module("wbhost.js", ["createWb"], HOST)
    assert got["sets"] == [
        {"highlights": [1, 2], "highlightsKeep": True, "highlightsZoom": True},
        {"highlights": [3], "highlightsKeep": True, "highlightsZoom": False},
        {"highlights": [], "highlightsKeep": False, "highlightsZoom": False},
    ]
    assert got["fits"] == [[1, 2]]


# ---------------------------------------------------------------------------- the page


def test_the_panel_has_a_slot_for_a_plugin_view_and_core_wires_it():
    html = (STATIC_DIR / "index.html").read_text()
    start = html.index('<aside id="details-panel"')
    panel = html[start : html.index("</aside>", start)]
    assert '<div id="details-plugin" hidden></div>' in panel
    src = (JS / "detailspanel.js").read_text()
    assert "export const detailsViews = createDetailsViews(" in src
    assert "if (s.focus) detailsViews.dismiss();" in src  # a focused record replaces a plugin's view
    assert '$("details-title").textContent = title;' in src
    plugins = (JS / "plugins.js").read_text()
    assert "fitLinks, details: detailsViews" in plugins


def test_a_network_opened_for_zoomed_highlights_fits_to_them_not_the_whole_network():
    main = (JS / "main.js").read_text()
    assert "switched && before.highlightsKeep && before.highlightsZoom ? [...before.highlights] : []" in main
    assert "const framedBefore = zoomTo.length > 0 && fitLinks(zoomTo);" in main  # a deleted link: the old network
    assert "if (switched && !framed) fitNetwork({ animate: false });" in main
    assert "highlightsKeep: false, highlightsZoom: false," in main  # used once, like the kept highlights


def test_the_zoom_in_icon(node_module):
    assert "zoom-in" in node_module("icons.js", ["ICONS"], "Object.keys(ICONS)")
