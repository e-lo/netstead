// netstead.map — shared NetworkMap component bootstrap.
//
// Loaded ONCE per host page via NetworkMap.head_assets(). Defines
// window.__netsteadAttach(uid) which reads window.__NETSTEAD_INSTANCES__[uid]
// and attaches a Leaflet map to #gv-map-<uid>. Each per-instance fragment
// pushes its uid into __NETSTEAD_QUEUE__ and calls __netsteadAttach when
// available; this file drains the queue on load, so attach happens
// regardless of fragment-vs-shared-asset ordering on the page.
//
// Safe to load multiple times (re-defines the same function, idempotent).
(function () {
  "use strict";

  if (typeof window.__netsteadAttach === "function") {
    (window.__NETSTEAD_QUEUE__ || []).splice(0).forEach(window.__netsteadAttach);
    return;
  }

  window.__netsteadAttach = function (uid) {
    const data = (window.__NETSTEAD_INSTANCES__ || {})[uid];
    if (!data) return;
    const mapEl = document.getElementById("gv-map-" + uid);
    if (!mapEl || typeof L === "undefined") return;
    if (mapEl.dataset.gvInit === "1") return;
    mapEl.dataset.gvInit = "1";
    attachOne(mapEl, data);
  };

  (window.__NETSTEAD_QUEUE__ || []).splice(0).forEach(window.__netsteadAttach);

  function attachOne(mapEl, data) {
    const tileProvider = data.tile_provider || "esri";
    const markers = Array.isArray(data.markers) ? data.markers : [];
    const layersData = Array.isArray(data.layers) ? data.layers : [];
    const networkBbox = Array.isArray(data.bbox) ? data.bbox : null;

    // Keyless providers only: the report is a static, shareable file, so any tile key would ship
    // inside it. Esri's light-grey canvas (base + a labels overlay) is the muted default, the same
    // basemap the Workbench offers as viz.basemap = "esri"; its tiles stop at z16, so Leaflet
    // upscales them past that rather than showing nothing.
    const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
    const TILE_CONFIG = {
      esri: {
        layers: [
          `${ESRI}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
          `${ESRI}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
        ],
        attribution: 'Esri, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxNativeZoom: 16,
        maxZoom: 19,
      },
      openstreetmap: {
        layers: ["https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"],
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxNativeZoom: 19,
        maxZoom: 19,
      },
    };
    const tileConfig = TILE_CONFIG[tileProvider] || TILE_CONFIG.esri;
    const SEVERITY_COLOR = { error: "#d1242f", warning: "#9a6700", info: "#0969da" };

    const targetBounds = computeTargetBounds();
    const map = L.map(mapEl, { zoomControl: true, attributionControl: true });
    if (targetBounds) {
      map.fitBounds(targetBounds, { padding: [40, 40], animate: false });
    } else {
      map.setView([47.5, -120.5], 9);
    }
    tileConfig.layers.forEach((url, i) => {
      L.tileLayer(url, {
        attribution: i === 0 ? tileConfig.attribution : "",
        maxNativeZoom: tileConfig.maxNativeZoom,
        maxZoom: tileConfig.maxZoom,
      }).addTo(map);
    });

    // ----- Toggleable layers (links, nodes, ...) ---------------------------
    const layerByName = {};
    layersData.forEach((spec) => {
      const group = L.layerGroup();
      const style = spec.style || {};
      const items = Array.isArray(spec.items) ? spec.items : [];
      items.forEach((item) => {
        let leafletLayer = null;
        if (spec.type === "polyline" && Array.isArray(item.coords) && item.coords.length >= 2) {
          const latlngs = item.coords.map((c) => [c[1], c[0]]);
          leafletLayer = L.polyline(latlngs, {
            color: style.color || "#1f77b4",
            weight: style.weight || 3,
            opacity: style.opacity || 0.9,
          });
        } else if (spec.type === "point" && Array.isArray(item.coord) && item.coord.length >= 2) {
          leafletLayer = L.circleMarker([item.coord[1], item.coord[0]], {
            radius: style.radius || 3,
            color: style.color || "#1f77b4",
            fillColor: style.color || "#1f77b4",
            fillOpacity: style.fillOpacity || 0.85,
            weight: 1,
          });
        }
        if (!leafletLayer) return;
        if (item.props && Object.keys(item.props).length) {
          leafletLayer.bindTooltip(formatProps(item.props), {
            sticky: true,
            direction: "top",
            opacity: 0.95,
          });
        }
        leafletLayer.addTo(group);
      });
      layerByName[spec.id] = { group, spec };
      if (spec.default_on) group.addTo(map);
    });

    // ----- Issue markers ---------------------------------------------------
    const markerLayer = L.layerGroup();
    markers.forEach((m) => {
      if (typeof m.lon !== "number" || typeof m.lat !== "number") return;
      const marker = L.circleMarker([m.lat, m.lon], {
        radius: 7,
        color: SEVERITY_COLOR[m.severity] || "#666",
        fillColor: SEVERITY_COLOR[m.severity] || "#666",
        fillOpacity: 0.75,
        weight: 2,
      });
      marker.bindPopup(buildPopup(m), { maxWidth: 360 });
      marker.on("popupopen", () => {
        // "Show row" — scrolls the findings table on the host page.
        const showBtn = document.querySelector(`[data-popup-show-row="${cssEscape(m.issue_id)}"]`);
        if (showBtn) {
          showBtn.addEventListener("click", (ev) => {
            ev.preventDefault();
            const row = document.querySelector('tr[data-issue-id="' + cssEscape(m.issue_id) + '"]');
            if (!row) return;
            document.querySelectorAll("tr.is-highlighted").forEach((r) => r.classList.remove("is-highlighted"));
            row.classList.add("is-highlighted");
            row.scrollIntoView({ behavior: "smooth", block: "center" });
          });
        }
        // "Propose fix" — opens the inline mini-editor in place of the
        // action bar.
        const fixBtn = document.querySelector(`[data-popup-propose-fix="${cssEscape(m.issue_id)}"]`);
        if (fixBtn) {
          fixBtn.addEventListener("click", (ev) => {
            ev.preventDefault();
            openInlineEditor(fixBtn, m);
          });
        }
      });
      marker.addTo(markerLayer);
    });
    markerLayer.addTo(map);

    // ----- Layer toggle widget --------------------------------------------
    buildLayerToggle();

    // ----- Resize handling -------------------------------------------------
    let allowRefit = true;
    function refresh() {
      map.invalidateSize();
      if (allowRefit && targetBounds) {
        map.fitBounds(targetBounds, { padding: [40, 40], animate: false });
      }
    }
    requestAnimationFrame(refresh);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(refresh).observe(mapEl);
    window.addEventListener("resize", refresh);
    window.addEventListener("load", () => {
      refresh();
      setTimeout(() => {
        allowRefit = false;
      }, 200);
    });

    // ----- helpers (closure-scoped per instance) ---------------------------

    function computeTargetBounds() {
      if (markers.length) {
        const latlngs = markers
          .filter((m) => typeof m.lon === "number" && typeof m.lat === "number")
          .map((m) => [m.lat, m.lon]);
        if (latlngs.length) {
          const b = L.latLngBounds(latlngs);
          if (latlngs.length === 1) return b.pad(0.5);
          return b.pad(0.2);
        }
      }
      if (networkBbox && networkBbox.length === 4) {
        return L.latLngBounds([
          [networkBbox[1], networkBbox[0]],
          [networkBbox[3], networkBbox[2]],
        ]);
      }
      const all = [];
      layersData.forEach((spec) => {
        const items = Array.isArray(spec.items) ? spec.items : [];
        items.forEach((item) => {
          if (Array.isArray(item.coords)) {
            item.coords.forEach((c) => all.push([c[1], c[0]]));
          } else if (Array.isArray(item.coord)) {
            all.push([item.coord[1], item.coord[0]]);
          }
        });
      });
      return all.length ? L.latLngBounds(all) : null;
    }

    function buildLayerToggle() {
      const entries = [];
      layersData.forEach((spec) => {
        entries.push({
          id: spec.id,
          label: `${spec.label} (${spec.count})`,
          on: !!spec.default_on,
          toggle: (checked) => {
            const layer = layerByName[spec.id].group;
            if (checked) map.addLayer(layer);
            else map.removeLayer(layer);
          },
        });
      });
      if (markers.length) {
        entries.push({
          id: "_markers",
          label: `Findings (${markers.length})`,
          on: true,
          toggle: (checked) => {
            if (checked) map.addLayer(markerLayer);
            else map.removeLayer(markerLayer);
          },
        });
      }
      if (!entries.length) return;
      const ctl = L.control({ position: "topright" });
      ctl.onAdd = function () {
        const div = L.DomUtil.create("div", "gv-layers-control leaflet-bar");
        div.innerHTML = entries
          .map(
            (e) =>
              `<label><input type="checkbox" data-gv-layer="${e.id}"${e.on ? " checked" : ""}> ${e.label}</label>`
          )
          .join("");
        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);
        div.querySelectorAll("input[data-gv-layer]").forEach((cb) => {
          cb.addEventListener("change", (ev) => {
            const id = ev.target.getAttribute("data-gv-layer");
            const entry = entries.find((e) => e.id === id);
            if (entry) entry.toggle(ev.target.checked);
          });
        });
        return div;
      };
      ctl.addTo(map);
    }

    function buildPopup(m) {
      const sev = (m.severity || "info").toLowerCase();
      const sevLabel = sev.toUpperCase();
      const esc = htmlEscape;
      let html =
        '<div class="gv-marker-popup">' +
        '<div class="gv-popup-header gv-sev severity-' +
        esc(sev) +
        '">' +
        esc(sevLabel) +
        ' <span class="gv-popup-code">' +
        esc(m.code || "") +
        "</span></div>" +
        '<div class="gv-popup-message">' +
        esc(m.message || "") +
        "</div>";
      if (m.fix_hint) {
        html += '<div class="gv-popup-fixhint">' + esc(m.fix_hint) + "</div>";
      }
      html += '<div class="gv-popup-actions">';
      // Two fix paths + one navigation:
      // - "Fix locally" opens the inline mini-editor inside the popup.
      //   Only offered when the finding maps to a known link/node row
      //   whose PK we can record for the edit log.
      // - "Fix upstream (OSM)" opens the source-of-truth editor. Today
      //   that's iD/JOSM for OSM-sourced networks; future upstreams
      //   (INRIX, HERE, ...) will slot in the same action slot.
      // - "Show error in table" is nav-only — scrolls the findings
      //   table to this row and highlights it.
      if (m.row_props && m.column) {
        html +=
          '<button type="button" class="gv-action gv-action-fix" data-popup-propose-fix="' +
          esc(m.issue_id || "") + '">Fix locally</button>';
      }
      if (m.edit_url) {
        html +=
          '<a class="gv-action" href="' + esc(m.edit_url) + '" target="_blank" rel="noopener noreferrer">Fix upstream (OSM) &rarr;</a>';
      }
      html += '<button type="button" class="gv-action" data-popup-show-row="' + esc(m.issue_id || "") + '">Show error in table</button>';
      html += "</div></div>";
      return html;
    }

    function formatProps(props) {
      const esc = htmlEscape;
      return Object.entries(props)
        .map(([k, v]) => `<b>${esc(k)}:</b> ${esc(String(v))}`)
        .join("<br>");
    }

    // ----- Edit-log: Propose-fix mini-editor + sidebar ----------------------
    //
    // Two entry points share the same editor UI:
    //  - From a marker popup: openInlineEditor(trigger, marker) — swaps the
    //    popup's action bar for the form.
    //  - From a findings-table row: window.netsteadEditor.openInRow(tr, meta)
    //    — inserts a colspan sub-row below the clicked row with the form.

    function openInRowEditor(tr, m) {
      if (!tr || !m) return;
      const table = tr.closest("table");
      const nCols = table ? table.querySelectorAll("thead th").length : 5;
      const pk = pickPk(m.table, m.row_props);
      if (!pk) return;
      const colName = m.column || "";
      const current = m.row_props ? m.row_props[colName] : "";
      const esc = htmlEscape;
      const pkSummary = Object.entries(pk).map(([k, v]) => `${esc(k)}=${esc(String(v))}`).join(", ");

      // Toggle: clicking Fix locally again on an already-open row closes it.
      let editorRow = tr.nextElementSibling;
      if (editorRow && editorRow.classList.contains("gv-inline-editor-row")) {
        editorRow.remove();
        return;
      }
      editorRow = document.createElement("tr");
      editorRow.className = "gv-inline-editor-row";
      const cell = document.createElement("td");
      cell.colSpan = nCols;
      cell.innerHTML =
        '<div class="gv-fix-editor">' +
        '<div class="gv-fix-row"><b>' + esc(m.table || "") + "</b> [" + esc(pkSummary) + "]</div>" +
        '<div class="gv-fix-row"><label><b>' + esc(colName) + ":</b> " +
        '<input class="gv-fix-input" type="text" value="' + esc(current == null ? "" : String(current)) + '"></label></div>' +
        '<div class="gv-fix-row"><label>Reason: ' +
        '<input class="gv-fix-reason" type="text" value="' +
        esc((m.code ? m.code + ": " : "") + (m.message || "")) + '"></label></div>' +
        '<div class="gv-fix-row gv-fix-actions">' +
        '<button type="button" class="gv-action gv-fix-save">Add to edit log</button>' +
        '<button type="button" class="gv-action gv-fix-cancel">Cancel</button>' +
        "</div></div>";
      editorRow.appendChild(cell);
      tr.insertAdjacentElement("afterend", editorRow);
      cell.querySelector(".gv-fix-cancel").addEventListener("click", () => editorRow.remove());
      cell.querySelector(".gv-fix-save").addEventListener("click", () => {
        const newVal = cell.querySelector(".gv-fix-input").value;
        const reason = cell.querySelector(".gv-fix-reason").value;
        addEdit({
          id: "e" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          kind: "fix",
          table: m.table,
          pk,
          column: colName,
          from_value: current == null ? null : current,
          to_value: coerceLikely(newVal, current),
          reason: reason || null,
          issue_id: m.issue_id || null,
          timestamp: new Date().toISOString(),
        });
        renderEditLogSidebar();
        editorRow.remove();
      });
    }

    // Cross-instance handle so the validation-report chrome (which
    // lives outside this closure) can open the same editor UI on a
    // findings-table row.
    window.netsteadEditor = window.netsteadEditor || {};
    window.netsteadEditor.openInRow = openInRowEditor;

    function openInlineEditor(triggerBtn, m) {
      // Replace the popup action bar with a small form for the suspect column.
      const popup = triggerBtn.closest(".gv-marker-popup");
      if (!popup) return;
      const actions = popup.querySelector(".gv-popup-actions");
      if (!actions) return;
      const pk = pickPk(m.table, m.row_props);
      if (!pk) return;
      const colName = m.column || "";
      const current = m.row_props ? m.row_props[colName] : "";
      const esc = htmlEscape;
      const pkSummary = Object.entries(pk).map(([k, v]) => `${esc(k)}=${esc(String(v))}`).join(", ");
      actions.innerHTML =
        '<div class="gv-fix-editor">' +
        '<div class="gv-fix-row"><b>' + esc(m.table || "") + "</b> [" + esc(pkSummary) + "]</div>" +
        '<div class="gv-fix-row"><label><b>' + esc(colName) + ":</b> " +
        '<input class="gv-fix-input" type="text" value="' + esc(current == null ? "" : String(current)) + '"></label></div>' +
        '<div class="gv-fix-row"><label>Reason: ' +
        '<input class="gv-fix-reason" type="text" placeholder="" value="' +
        esc((m.code ? m.code + ": " : "") + (m.message || "")) + '"></label></div>' +
        '<div class="gv-fix-row gv-fix-actions">' +
        '<button type="button" class="gv-action gv-fix-save">Add to edit log</button>' +
        '<button type="button" class="gv-action gv-fix-cancel">Cancel</button>' +
        "</div></div>";
      actions.querySelector(".gv-fix-cancel").addEventListener("click", () => {
        // Re-render popup to restore the action bar.
        triggerBtn.closest(".leaflet-popup-content")?.querySelector(".gv-marker-popup")?.replaceWith(_buildPopupNode(m));
      });
      actions.querySelector(".gv-fix-save").addEventListener("click", () => {
        const newVal = actions.querySelector(".gv-fix-input").value;
        const reason = actions.querySelector(".gv-fix-reason").value;
        addEdit({
          id: "e" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          kind: "fix",
          table: m.table,
          pk: pk,
          column: colName,
          from_value: current == null ? null : current,
          to_value: coerceLikely(newVal, current),
          reason: reason || null,
          issue_id: m.issue_id || null,
          timestamp: new Date().toISOString(),
        });
        renderEditLogSidebar();
        // Close the popup so the user sees the sidebar update.
        triggerBtn.closest(".leaflet-popup")?.querySelector(".leaflet-popup-close-button")?.click();
      });
    }

    function _buildPopupNode(m) {
      // Used to restore the popup body after Cancel — reparses buildPopup().
      const wrapper = document.createElement("div");
      wrapper.innerHTML = buildPopup(m);
      return wrapper.firstChild;
    }

    // Pick the GMNS primary-key columns for a given table from a row's props.
    // For Phase 1 we only know link/node; future tables would extend here.
    function pickPk(table, props) {
      if (!props) return null;
      if (table === "link" && "link_id" in props) return { link_id: props.link_id };
      if (table === "node" && "node_id" in props) return { node_id: props.node_id };
      return null;
    }

    // Coerce a typed input back into the type the source column appeared
    // to have. Numbers stay numbers, otherwise string. Empty → null.
    function coerceLikely(input, sample) {
      if (input === "" || input == null) return null;
      if (typeof sample === "number" || (typeof sample === "string" && sample.match(/^-?\d+(\.\d+)?$/))) {
        const n = Number(input);
        if (!isNaN(n)) return n;
      }
      return input;
    }

    // ----- Edit-log state + sidebar UI -------------------------------------

    const EDIT_LOG_KEY = "netsteadEditLog";

    function loadEditLog() {
      try {
        const raw = window.sessionStorage.getItem(EDIT_LOG_KEY);
        return raw ? JSON.parse(raw) : [];
      } catch {
        return [];
      }
    }
    function saveEditLog(edits) {
      try {
        window.sessionStorage.setItem(EDIT_LOG_KEY, JSON.stringify(edits));
      } catch {
        /* sessionStorage quota or disabled — ignore */
      }
    }
    function addEdit(edit) {
      const log = loadEditLog();
      log.push(edit);
      saveEditLog(log);
    }
    function removeEdit(id) {
      saveEditLog(loadEditLog().filter((e) => e.id !== id));
      renderEditLogSidebar();
    }

    function ensureEditLogSidebar() {
      let el = document.querySelector(".gv-edit-log-sidebar");
      if (el) return el;
      el = document.createElement("aside");
      el.className = "gv-edit-log-sidebar";
      document.body.appendChild(el);
      return el;
    }

    function renderEditLogSidebar() {
      const el = ensureEditLogSidebar();
      const log = loadEditLog();
      if (!log.length) {
        el.innerHTML = "";
        el.style.display = "none";
        return;
      }
      const esc = htmlEscape;
      const rows = log.map((e) => {
        const pk = Object.entries(e.pk).map(([k, v]) => `${esc(k)}=${esc(String(v))}`).join(", ");
        const fromV = e.from_value == null ? "null" : String(e.from_value);
        const toV = e.to_value == null ? "null" : String(e.to_value);
        return (
          '<li><div class="gv-edit-summary">' +
          '<b>' + esc(e.table) + "</b> [" + esc(pk) + "]." + esc(e.column) +
          ': <code>' + esc(fromV) + "</code> → <code>" + esc(toV) + "</code>" +
          '<button type="button" class="gv-edit-remove" data-edit-id="' + esc(e.id) + '" title="Remove">×</button>' +
          "</div></li>"
        );
      }).join("");
      el.style.display = "";
      el.innerHTML =
        '<details open><summary>Edit log (' + log.length + ')</summary>' +
        '<ul class="gv-edit-list">' + rows + '</ul>' +
        '<div class="gv-edit-actions">' +
        '<button type="button" class="gv-action gv-edit-download">Download YAML</button>' +
        '<button type="button" class="gv-action gv-edit-copy">Copy YAML</button>' +
        '<button type="button" class="gv-action gv-edit-clear">Clear</button>' +
        "</div></details>";
      el.querySelectorAll(".gv-edit-remove").forEach((b) =>
        b.addEventListener("click", () => removeEdit(b.getAttribute("data-edit-id")))
      );
      el.querySelector(".gv-edit-download").addEventListener("click", () => downloadYaml());
      el.querySelector(".gv-edit-copy").addEventListener("click", () => copyYaml());
      el.querySelector(".gv-edit-clear").addEventListener("click", () => {
        if (window.confirm("Clear all " + log.length + " proposed edits?")) {
          saveEditLog([]);
          renderEditLogSidebar();
        }
      });
    }

    function buildYaml() {
      // Emits a network-wrangler ProjectCard:
      //   project: <name>
      //   tags: [netstead, edit-log]
      //   notes: |
      //     source: ...
      //     spec_version: ...
      //     created_at: ...
      //     client: netstead.map (browser)
      //   changes:
      //     - roadway_property_change:
      //         facility:
      //           model_link_id: [42]     # or model_node_id for node edits
      //         property_changes:
      //           free_speed:
      //             existing: 40           # our "from" (omitted when null)
      //             set: 25                # our "to"
      //         notes: 'reason · issue_id=i0 · edit_id=e...'
      //
      // Node property changes aren't a first-class ProjectCard type; we
      // emit the same shape via ``model_node_id`` as a netstead extension
      // so the file round-trips through netstead.map.edits.load_edit_log.
      const log = loadEditLog();
      const PK_TO_FACILITY = { link_id: "model_link_id", node_id: "model_node_id" };
      let s = "";
      s += "project: netstead edits " + new Date().toISOString() + "\n";
      s += "tags: [netstead, edit-log]\n";
      s += "notes: |\n";
      s += "  created_at: " + new Date().toISOString() + "\n";
      s += "  client: netstead.map (browser)\n";
      s += "changes:\n";
      log.forEach((e) => {
        let facKey = null, facId = null;
        for (const [col, val] of Object.entries(e.pk || {})) {
          if (PK_TO_FACILITY[col]) { facKey = PK_TO_FACILITY[col]; facId = val; break; }
        }
        if (!facKey) return; // skip unsupported PK shape
        s += "  - roadway_property_change:\n";
        s += "      facility:\n";
        s += "        " + facKey + ": [" + yamlScalar(facId) + "]\n";
        s += "      property_changes:\n";
        s += "        " + e.column + ":\n";
        if (e.from_value !== null && e.from_value !== undefined) {
          s += "          existing: " + yamlScalar(e.from_value) + "\n";
        }
        s += "          set: " + yamlScalar(e.to_value) + "\n";
        const bits = [];
        if (e.reason) bits.push(e.reason);
        if (e.issue_id) bits.push("issue_id=" + e.issue_id);
        if (e.id) bits.push("edit_id=" + e.id);
        if (bits.length) s += "      notes: " + yamlScalar(bits.join(" · ")) + "\n";
      });
      return s;
    }
    function yamlScalar(v) {
      if (v == null) return "null";
      if (typeof v === "number" || typeof v === "boolean") return String(v);
      const s = String(v);
      if (/^[A-Za-z0-9_\-./:]+$/.test(s) && !["null", "true", "false", "yes", "no"].includes(s.toLowerCase())) {
        return s;
      }
      return "'" + s.replace(/'/g, "''") + "'";
    }
    function downloadYaml() {
      const yaml = buildYaml();
      const blob = new Blob([yaml], { type: "text/yaml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "edits-" + new Date().toISOString().slice(0, 19).replace(/:/g, "-") + ".yaml";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    function copyYaml() {
      const yaml = buildYaml();
      navigator.clipboard?.writeText(yaml);
    }

    // Render once on attach so a refresh keeps showing the running log.
    renderEditLogSidebar();

    function htmlEscape(s) {
      return String(s).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c]));
    }
    function cssEscape(s) {
      if (window.CSS && CSS.escape) return CSS.escape(String(s));
      return String(s).replace(/(["\\])/g, "\\$1");
    }
  }
})();
