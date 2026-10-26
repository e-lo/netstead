/* corral ValidationReport — interactive behavior for the
 * single-file HTML renderer.
 *
 * Vanilla JS only: this script is inlined into every produced report,
 * which lands in email attachments and ticketing systems where build
 * tooling does not exist. No frameworks, no transpilation, no imports.
 *
 * Responsibilities:
 *   1. Filter rows by severity / category / table / code substring.
 *   2. Toggle a per-row detail panel on click.
 *   3. "Export JSON" button: download the embedded report-data blob.
 *
 * Where a map section is present, Vega-Lite (loaded by a separate
 * <script src> tag — the only external dependency in the whole file)
 * renders it. This script is no-op for the map; we only initialise the
 * Vega-Embed spec when both Vega-Lite and a #map div are on the page.
 */

(function () {
  "use strict";

  // -------------------------------------------------------------------
  // Filtering
  // -------------------------------------------------------------------

  function getFilterState() {
    return {
      severity: (document.getElementById("filter-severity") || {}).value || "",
      category: (document.getElementById("filter-category") || {}).value || "",
      table: (document.getElementById("filter-table") || {}).value || "",
      code: ((document.getElementById("filter-code") || {}).value || "")
        .trim()
        .toLowerCase(),
    };
  }

  function applyFilters() {
    var f = getFilterState();
    var rows = document.querySelectorAll("tr.issue");
    var visibleByTable = {};

    rows.forEach(function (row) {
      var s = row.getAttribute("data-severity") || "";
      var c = row.getAttribute("data-category") || "";
      var t = row.getAttribute("data-table") || "";
      var code = (row.getAttribute("data-code") || "").toLowerCase();
      var match =
        (!f.severity || s === f.severity) &&
        (!f.category || c === f.category) &&
        (!f.table || t === f.table) &&
        (!f.code || code.indexOf(f.code) !== -1);
      row.hidden = !match;
      // Always hide the matching detail row when the parent hides.
      var detail = row.nextElementSibling;
      if (detail && detail.classList.contains("detail")) {
        if (!match) detail.hidden = true;
      }
      // Track per-severity visibility for "no matches" hint.
      if (match) {
        visibleByTable[s] = (visibleByTable[s] || 0) + 1;
      }
    });

    // Show / hide the "(no matches)" placeholder per severity section.
    document.querySelectorAll("table[data-severity]").forEach(function (tbl) {
      var sev = tbl.getAttribute("data-severity");
      var hint = tbl.parentElement.querySelector(
        '.empty[data-for-severity="' + sev + '"]'
      );
      if (!hint) return;
      hint.hidden = (visibleByTable[sev] || 0) > 0;
    });
  }

  function bindFilters() {
    ["filter-severity", "filter-category", "filter-table"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", applyFilters);
    });
    var code = document.getElementById("filter-code");
    if (code) code.addEventListener("input", applyFilters);
  }

  // -------------------------------------------------------------------
  // Row expansion
  // -------------------------------------------------------------------

  function bindRowExpansion() {
    document.querySelectorAll("tr.issue").forEach(function (row) {
      row.addEventListener("click", function () {
        var detail = row.nextElementSibling;
        if (detail && detail.classList.contains("detail")) {
          detail.hidden = !detail.hidden;
        }
      });
    });
  }

  // -------------------------------------------------------------------
  // Export JSON
  // -------------------------------------------------------------------

  function bindExport() {
    var btn = document.getElementById("export-json");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var dataTag = document.getElementById("report-data");
      if (!dataTag) return;
      var blob = new Blob([dataTag.textContent], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "validation-report.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  // -------------------------------------------------------------------
  // Map (Vega-Lite) — only runs when both #map and vegaEmbed exist.
  // -------------------------------------------------------------------

  function renderMap() {
    var mapDiv = document.getElementById("map");
    if (!mapDiv) return;
    var specTag = document.getElementById("map-spec");
    if (!specTag) return;
    if (typeof vegaEmbed !== "function") {
      // Vega-Lite did not load — leave a hint instead of a silent blank.
      var note = document.createElement("p");
      note.className = "no-map-fallback";
      note.textContent =
        "Map view requires Vega-Lite (loaded from CDN). " +
        "Open this file with internet access to view the map; the rest " +
        "of the report works offline.";
      mapDiv.appendChild(note);
      return;
    }
    try {
      var spec = JSON.parse(specTag.textContent);
      vegaEmbed("#map", spec, { actions: false });
    } catch (e) {
      mapDiv.textContent = "Could not render map: " + e.message;
    }
  }

  // -------------------------------------------------------------------
  // Init
  // -------------------------------------------------------------------

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  function init() {
    bindFilters();
    bindRowExpansion();
    bindExport();
    renderMap();
  }
})();
