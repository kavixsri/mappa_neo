/* MAPPA-Neo Tactical GIS & Neo4j Frontend Controller (XSS-safe, offline-friendly) */
const state = {
  missions: [],
  missionId: null,
  missionData: null,
  user: null,
  neo4j: null,
  map: null,
  layers: {},
};

const $ = (id) => document.getElementById(id);

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 3500);
}

async function api(url, opts = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...opts });
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

/* ---------- Map Setup ---------- */
function initMap() {
  state.map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([28.162, 85.336], 14);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap &copy; CARTO",
    maxZoom: 19,
  }).addTo(state.map);
}

function clearMapLayers() {
  Object.values(state.layers).forEach((l) => { if (l && state.map.hasLayer(l)) state.map.removeLayer(l); });
  state.layers = {};
}

const ROAD_COLORS = { BLOCKED: "#ef4444", UNCERTAIN: "#f59e0b", CLEAR: "#10b981", NO_DATA: "#64748b" };
const COP_COLORS = { Destroyed: "#c026d3", Damaged: "#e879f9", "Possibly damaged": "#f0abfc", "No visible damage": "#4ade80" };

async function loadMission(missionId) {
  state.missionId = missionId;
  clearMapLayers();
  const [m, roadsGj, copGj, iso, pq] = await Promise.all([
    api(`/api/missions/${missionId}`),
    api(`/api/missions/${missionId}/geojson/roads_status`),
    api(`/api/missions/${missionId}/geojson/copernicus_roads`),
    api(`/api/missions/${missionId}/graph/isolation`),
    api(`/api/missions/${missionId}/graph/clearance-queue`),
  ]);
  state.missionData = m;
  const rep = m.summary;

  // Banner
  const b = $("missionBanner");
  b.className = "banner " + (m.simulated ? "banner-sim" : "banner-real");
  b.innerHTML = m.simulated
    ? `<span><strong>SIMULATED BENCHMARK:</strong> ${esc(m.name)} — Synthetic scene with known GPS error &amp; DSM−DEM ground truth.</span><span>Grid: ${esc(rep.analysis_grid_m)} m/px · Runtime: ${esc(rep.processing_time_s?.total)} s</span>`
    : `<span><strong>REAL DISASTER EVENT:</strong> ${esc(m.name)} (${esc(m.event_date)}) — Verified Copernicus EMS EMSR927 &amp; OpenAerialMap VHR data.</span><span>Grid: ${esc(rep.analysis_grid_m)} m/px · Offline Runtime: ${esc(rep.processing_time_s?.total)} s</span>`;

  // KPIs
  const rLen = rep.roads?.length_m_by_status || {};
  $("kpiArea").textContent = rep.area_km2;
  $("kpiBlocked").textContent = `${Math.round(rLen.BLOCKED || 0)} m`;
  $("kpiIsolated").textContent = `${iso.isolated_population} (${iso.isolated_count} sites)`;
  $("kpiAcc").textContent = `±${Math.round(rep.georeferencing?.horizontal_accuracy_95_m || 10)} m`;
  $("accuracyLine").textContent = rep.georeferencing?.statement || "";

  // Raster Overlays
  const bounds = m.bounds_wgs84;
  if (bounds) {
    state.layers.pre = L.imageOverlay(`/api/missions/${missionId}/overlay/web_pre.png`, bounds, { opacity: 0.92 });
    state.layers.post = L.imageOverlay(`/api/missions/${missionId}/overlay/web_post.png`, bounds, { opacity: parseFloat($("postOpacity").value) });
    state.layers.change = L.imageOverlay(`/api/missions/${missionId}/overlay/web_change.png`, bounds, { opacity: 0.72 });
    if ($("chkPre").checked) state.layers.pre.addTo(state.map);
    if ($("chkPost").checked) state.layers.post.addTo(state.map);
    if ($("chkChange").checked) state.layers.change.addTo(state.map);
    state.map.fitBounds(bounds, { padding: [16, 16] });
  }

  // MAPPA Road Status Layer
  state.layers.roads = L.geoJSON(roadsGj, {
    style: (feat) => {
      const st = feat.properties?.status || "CLEAR";
      return {
        color: ROAD_COLORS[st] || "#94a3b8",
        weight: st === "BLOCKED" ? 5 : st === "UNCERTAIN" ? 3.5 : 2.5,
        opacity: 0.95,
      };
    },
    onEachFeature: (feat, layer) => {
      const p = feat.properties || {};
      const clearedTag = p.cleared_by_pwd ? " (CLEARED BY PWD LIVE)" : "";
      layer.bindPopup(
        `<strong>${esc(p.name)}</strong><br/>Status: <b>${esc(p.status)}${clearedTag}</b><br/>` +
        `Segment: ${esc(p.length_m)} m · Changed: ${p.changed_frac != null ? Math.round(p.changed_frac * 100) + "%" : "n/a"}<br/>` +
        `Height anomaly: ${p.height_anom_m != null ? esc(p.height_anom_m) + " m" : "optical-only"}`
      );
    },
  });
  if ($("chkRoads").checked) state.layers.roads.addTo(state.map);

  // Copernicus EMS Ground Truth Layer (Real Nepal 2026)
  state.layers.copernicus = L.geoJSON(copGj, {
    style: (feat) => ({
      color: COP_COLORS[feat.properties?.damage_gra] || "#a855f7",
      weight: 4,
      dashArray: "6 5",
      opacity: 0.9,
    }),
    onEachFeature: (feat, layer) => {
      const p = feat.properties || {};
      layer.bindPopup(`<strong>Copernicus EMS EMSR927</strong><br/>Grading: <b>${esc(p.damage_gra || "Graded")}</b><br/>Type: ${esc(p.obj_type || p.info || "Road")}`);
    },
  });
  if ($("chkCopernicus").checked && copGj.features?.length) state.layers.copernicus.addTo(state.map);

  // Villages / Facilities Layer
  renderFacilities(iso);

  // Populate Detour dropdowns
  const allFacs = [...iso.reachable_facilities, ...iso.isolated_facilities];
  const fromSel = $("fromFac");
  const toSel = $("toFac");
  fromSel.innerHTML = "";
  toSel.innerHTML = "";
  allFacs.forEach((f) => {
    const tag = f.is_staging ? " [STAGING BASE]" : (f.reachable ? " [Reachable]" : " [CUT OFF]");
    fromSel.add(new Option(`${f.name}${tag}`, f.id));
    toSel.add(new Option(`${f.name}${tag}`, f.id));
  });
  if (iso.staging_facility) fromSel.value = iso.staging_facility.id;
  if (iso.isolated_facilities.length) {
    toSel.value = iso.isolated_facilities[0].id;
  } else if (allFacs.length > 1) {
    toSel.value = allFacs[1].id;
  }
  $("detourCypher").textContent = iso.cypher || "";
  $("detourResult").style.display = "none";

  // Exports
  const exports = [
    ["brief.pdf", "One-Page PDF Brief", "btn-primary"],
    ["roads_status.gpkg", "QGIS GeoPackage", "btn-cyan"],
    ["roads_status.geojson", "Roads GeoJSON", ""],
    ["roads_status.kml", "Google Earth KML", ""],
    ["change_polygons.geojson", "Change Polygons", ""],
    ["blocked_and_uncertain_stretches.csv", "Blocked CSV", ""],
    ["report.json", "Audit Report JSON", ""],
  ];
  $("exportLinks").innerHTML = exports
    .map(([fn, lbl, cls]) => `<a class="btn btn-sm ${cls}" href="/api/missions/${esc(missionId)}/download/${esc(fn)}">${esc(lbl)}</a>`)
    .join("");

  // Populate Segment 2 (PWD Queue) & Tab 4 (Validation/Honesty)
  renderPwdQueue(iso, pq);
  renderAuditView(m);
}

function renderFacilities(iso) {
  if (state.layers.facilities && state.map.hasLayer(state.layers.facilities)) {
    state.map.removeLayer(state.layers.facilities);
  }
  const grp = L.layerGroup();
  const all = [...iso.reachable_facilities, ...iso.isolated_facilities];
  all.forEach((f) => {
    const color = f.is_staging ? "#06b6d4" : f.reachable ? "#10b981" : "#ef4444";
    const m = L.circleMarker([f.lat, f.lon], {
      radius: f.is_staging ? 9 : 7,
      color: "#fff",
      weight: 2,
      fillColor: color,
      fillOpacity: 0.95,
    });
    m.bindPopup(
      `<strong>${esc(f.name)}</strong><br/>Category: ${esc(f.category)}<br/>` +
      `Est. Population: ${esc(f.population_est)}<br/>` +
      `Status: <b style="color:${color}">${f.is_staging ? "STAGING BASE" : f.reachable ? "REACHABLE" : "CUT OFF (ISOLATED)"}</b>`
    );
    grp.addLayer(m);
  });
  state.layers.facilities = grp;
  if ($("chkFacilities").checked) grp.addTo(state.map);
}

/* ---------- Neo4j Safe Detour ---------- */
async function computeDetour() {
  const fromId = $("fromFac").value;
  const toId = $("toFac").value;
  if (!fromId || !toId || fromId === toId) {
    toast("Pick two different facilities to compute a detour.");
    return;
  }
  try {
    const res = await api(`/api/missions/${state.missionId}/graph/detour`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from_facility_id: fromId,
        to_facility_id: toId,
        avoid_uncertain: $("avoidUncertain").checked,
      }),
    });
    if (state.layers.detour && state.map.hasLayer(state.layers.detour)) state.map.removeLayer(state.layers.detour);
    const grp = L.layerGroup();
    if (res.baseline_coords_ll?.length) {
      L.polyline(res.baseline_coords_ll, { color: "#ef4444", weight: 4, dashArray: "6 6", opacity: 0.85 }).addTo(grp);
    }
    if (res.safe_coords_ll?.length) {
      L.polyline(res.safe_coords_ll, { color: "#06b6d4", weight: 6, opacity: 0.98 }).addTo(grp);
    }
    state.layers.detour = grp.addTo(state.map);

    const box = $("detourResult");
    box.style.display = "block";
    if (res.verdict === "ISOLATED_NO_SAFE_ROUTE") {
      box.innerHTML = `<strong style="color:#f87171">ISOLATED — NO PASSABLE DETOUR</strong><br/>` +
        `Pre-disaster shortest route: ${esc(res.baseline_distance_m ?? "n/a")} m.<br/>` +
        `Blocked roads cutting access: <b>${esc(res.blocked_roads_avoided.join(", ") || "corridor cut")}</b>.<br/>` +
        `<span class="muted">Dispatch PWD excavator via Tab 2 to reconnect this site, or plan helicopter / foot evac.</span>`;
    } else if (res.verdict === "SAFE_DETOUR_FOUND") {
      box.innerHTML = `<strong style="color:#22d3ee">SAFE DETOUR FOUND (Cyan Line)</strong><br/>` +
        `Safe route: <b>${esc(res.safe_distance_m)} m</b> (baseline ${esc(res.baseline_distance_m)} m · +${esc(res.detour_overhead_m)} m detour)<br/>` +
        `Avoided blocked roads: ${esc(res.blocked_roads_avoided.join(", "))}`;
    } else {
      box.innerHTML = `<strong style="color:#34d399">DIRECT ROUTE CLEAR</strong><br/>Shortest passable route: <b>${esc(res.safe_distance_m)} m</b>.`;
    }
    $("detourCypher").textContent = res.cypher || "";
    $("detourEngineChip").textContent = res.engine === "neo4j_live" ? "Neo4j Live" : "Offline Mirror";
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- Segment 2: PWD / BRO Clearance Queue ---------- */
function renderPwdQueue(iso, pq) {
  $("pwdIsoCount").textContent = `${iso.isolated_count} / ${iso.isolated_count + iso.reachable_count}`;
  $("pwdIsoNames").textContent = iso.isolated_facilities.length
    ? "Cut off: " + iso.isolated_facilities.map((f) => f.name).join(", ")
    : "All mapped communities are currently reachable from staging base.";
  $("pwdIsoPop").textContent = `${iso.isolated_population.toLocaleString()} people`;
  $("pwdEngineNote").textContent = pq.engine === "neo4j_live" ? "Live Neo4j Cypher Simulation" : "Offline Graph Mirror (Syncs to Neo4j when online)";
  $("pwdCypher").textContent = pq.cypher || "";
  $("btnExportLedger").href = `/api/missions/${encodeURIComponent(state.missionId)}/work-orders/export.csv`;

  const tbody = $("pwdTableBody");
  if (!pq.queue.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No blocked road stretches detected in this mission.</td></tr>`;
    return;
  }
  tbody.innerHTML = pq.queue.map((item) => {
    const st = item.work_order_status || "OPEN";
    const recon = item.facilities_reconnected?.length
      ? `<strong>+${esc(item.population_reconnected)} pop</strong><br/><span class="muted">${esc(item.facilities_reconnected.join(", "))}</span>`
      : `<span class="muted">Redundant / secondary cut</span>`;
    const isCleared = st === "CLEARED";
    return `<tr>
      <td><strong>#${esc(item.priority_rank)}</strong></td>
      <td><strong>${esc(item.road_name)}</strong><br/><span class="muted">${esc(item.stretch_key)}</span></td>
      <td>${esc(item.length_m)} m</td>
      <td>~${esc(item.est_debris_vol_m3)} m³</td>
      <td>${recon}</td>
      <td><span class="status-chip st-${esc(st)}">${esc(st)}</span></td>
      <td>${esc(item.assigned_team || "Unassigned")}</td>
      <td>
        ${!isCleared
          ? `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="DISPATCHED">Dispatch JCB</button>
             <button class="btn btn-sm btn-green" data-wo="${esc(item.work_order_id)}" data-act="CLEARED">Mark Cleared</button>`
          : `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="OPEN">Re-Open Cut</button>`}
      </td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("button[data-wo]").forEach((btn) => {
    btn.addEventListener("click", () => handleWorkOrderAction(btn.dataset.wo, btn.dataset.act));
  });
}

async function handleWorkOrderAction(woId, newStatus) {
  if (!state.user || !["engineer", "admin"].includes(state.user.role)) {
    toast("Sign in as PWD / BRO Clearance Engineer to update work orders.");
    $("authModal").classList.add("open");
    return;
  }
  const team = newStatus === "DISPATCHED" ? "BRO Excavator Unit 04" : (newStatus === "CLEARED" ? "BRO Excavator Unit 04 (Completed)" : "");
  try {
    const res = await api(`/api/missions/${state.missionId}/work-orders/${woId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus, assigned_team: team, notes: `Updated via PWD Clearance Queue -> ${newStatus}` }),
    });
    toast(`Updated to ${newStatus}! Isolated population now: ${res.isolation_after.isolated_population}`);
    await loadMission(state.missionId);
    loadAuditLogs();
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- Audit / Validation View ---------- */
function renderAuditView(m) {
  const rep = m.summary;
  const g = rep.georeferencing || {};
  const ch = rep.change || {};
  $("honestyBox").innerHTML = `
    <p><strong>Stated Positional Accuracy:</strong> ${esc(g.statement)}</p>
    <p style="margin-top:8px"><strong>Registration Method:</strong> ${esc(g.method)} · Inliers: ${esc(g.n_inliers)} · RMS: ${esc(g.registration_rms_m ?? "n/a")} m · GPS Correction Applied: ${esc(g.gps_only_correction_m)} m</p>
    <p style="margin-top:8px"><strong>Change Detection Limits:</strong> ${esc(ch.caveat)} Height cue used: <b>${ch.height_used ? "Yes (DSM−DEM)" : "No (optical-only)"}</b>.</p>
    <p style="margin-top:8px" class="muted"><strong>Provenance:</strong> ${esc(rep.provenance)}</p>
  `;

  if (rep.copernicus_validation?.available) {
    const cv = rep.copernicus_validation;
    $("validationBox").innerHTML = `
      <p><strong style="color:#c084fc">Real Ground-Truth Comparison: ${esc(cv.reference)}</strong></p>
      <p style="margin-top:6px">Copernicus EMS graded segments in AOI: <b>${esc(cv.copernicus_segments_in_aoi)}</b> (${esc(cv.copernicus_damaged_segments)} damaged/destroyed, ${esc(cv.copernicus_undamaged_segments)} undamaged).</p>
      <p style="margin-top:6px">MAPPA Recall vs. Copernicus Damaged Roads: <b>${Math.round(cv.recall_vs_copernicus_damaged * 100)}%</b> (${esc(cv.matched_damaged_road_m)} m matched of ${esc(cv.total_gt_damaged_in_footprint_m)} m)</p>
      <p style="margin-top:4px">MAPPA Precision (optical-only): <b>${Math.round(cv.precision_vs_copernicus_damaged * 100)}%</b> · Clear Agreement on Undamaged: <b>${cv.clear_agreement_on_undamaged != null ? Math.round(cv.clear_agreement_on_undamaged * 100) + "%" : "n/a"}</b></p>
      <p style="margin-top:8px" class="muted">${esc(cv.note)}</p>
    `;
  } else if (rep.validation) {
    const v = rep.validation;
    $("validationBox").innerHTML = `
      <p><strong style="color:#fbbf24">Synthetic Ground-Truth Validation (Known Slide &amp; GPS Shift)</strong></p>
      <p style="margin-top:6px">Injected GPS Error Before → After: <b>${esc(v.error_before_mean_m)} m → ${esc(v.error_after_mean_m)} m</b> (${esc(v.n_points)} check points)</p>
      <p style="margin-top:6px">Blocked-Road Precision / Recall (with DSM−DEM): <b>${esc(v.roads_vs_truth?.precision)} / ${esc(v.roads_vs_truth?.recall)}</b></p>
      <p style="margin-top:6px" class="muted">Key ablation finding: Colour-only recall = 0.00 because landslide debris matches dirt-road colour; adding DSM−DEM raised recall to 1.00.</p>
    `;
  } else {
    $("validationBox").innerHTML = `<p class="muted">Custom field run — no independent ground-truth layer attached.</p>`;
  }

  const tm = rep.processing_time_s || {};
  const total = Math.max(tm.total || 1, 0.1);
  $("timingBox").innerHTML = Object.entries(tm)
    .filter(([k]) => k !== "total_so_far")
    .map(([k, v]) => {
      const pct = Math.min(100, Math.round((v / total) * 100));
      return `<div style="margin-bottom:7px">
        <div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(k)}</span><strong>${esc(v)} s</strong></div>
        <div style="background:#1e293b;height:7px;border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${k === "total" ? "#f97316" : "#06b6d4"}"></div></div>
      </div>`;
    }).join("") + `<p class="muted" style="margin-top:8px">${esc(rep.processing_time_statement || "")}</p>`;
}

async function loadAuditLogs() {
  if (!state.user) {
    $("auditLogBox").textContent = "Sign in to view authenticated audit logs.";
    return;
  }
  try {
    const res = await api("/api/audit-logs");
    $("auditLogBox").innerHTML = res.logs.slice(0, 12).map((l) =>
      `<div style="padding:5px 0;border-bottom:1px solid #1e293b;font-size:12px">
        <strong>${esc(l.action)}</strong> · ${esc(l.user_email)} (${esc(l.role)}) → ${esc(l.target)}
        <span class="muted"> · ${esc(l.detail)} (${esc(l.created_at)})</span>
      </div>`
    ).join("") || "No events logged yet.";
  } catch { /* ignore */ }
}

/* ---------- Auth & Neo4j Status ---------- */
async function refreshSession() {
  const res = await api("/api/auth/me");
  state.user = res.authenticated ? res.user : null;
  const btn = $("authBtn");
  if (state.user) {
    btn.textContent = `${state.user.name} (${state.user.role}) · Sign Out`;
    btn.className = "btn btn-sm";
  } else {
    btn.textContent = "Sign In (2 Roles)";
    btn.className = "btn btn-primary";
  }
  loadAuditLogs();
}

async function refreshNeo4j() {
  const st = await api("/api/neo4j/status");
  state.neo4j = st;
  const dot = $("neo4jDot");
  const lbl = $("neo4jLabel");
  if (st.connected) {
    dot.className = "dot dot-green";
    lbl.textContent = `Neo4j Live (${st.graph_stats?.nodes || 0} nodes)`;
  } else {
    dot.className = "dot dot-amber";
    lbl.textContent = `Neo4j Mirror (${st.graph_stats?.nodes || 0} nodes)`;
  }
  $("neo4jStatusDetail").innerHTML =
    `<strong>Mode:</strong> ${st.connected ? '<span style="color:#34d399">LIVE NEO4J BOLT CONNECTED</span>' : '<span style="color:#fbbf24">OFFLINE GRAPH MIRROR (Field Mode)</span>'}<br/>` +
    `Nodes: <b>${esc(st.graph_stats?.nodes)}</b> · Road Segments: <b>${esc(st.graph_stats?.road_edges)}</b><br/>` +
    `<span class="muted">Plug in a Neo4j AuraDB URI below to sync all missions live over Bolt.</span>`;
}

async function loginWith(email, password) {
  $("authErr").textContent = "";
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    $("authModal").classList.remove("open");
    await refreshSession();
    toast(`Signed in as ${res.user.full_name} (${res.user.role})`);
  } catch (e) {
    $("authErr").textContent = e.message;
  }
}

/* ---------- Flight Planner ---------- */
async function calcFlightPlan() {
  const lat = parseFloat($("fpLat").value);
  const lon = parseFloat($("fpLon").value);
  const width_m = parseFloat($("fpW").value);
  const height_m = parseFloat($("fpH").value);
  const alt_m = parseFloat($("fpAlt").value);
  try {
    const res = await api("/api/tools/flight-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon, width_m, height_m, alt_m }),
    });
    const p = res.plan;
    $("planResult").innerHTML = `
      <strong>Survey Grid Summary (${width_m} × ${height_m} m at ${alt_m} m AGL):</strong><br/>
      GSD: <b>${esc(p.gsd_cm_px)} cm/px</b> · Photos: <b>${esc(p.n_images)}</b> · Lines: <b>${esc(p.n_lines)}</b><br/>
      Line Spacing: ${esc(p.line_spacing_m)} m · Max Speed: ${esc(p.max_speed_ms)} m/s · Est. Flight Time: <b>~${esc(p.flight_time_min)} min</b>
    `;
    $("btnDownloadKml").href = `/api/tools/flight-plan.kml?lat=${lat}&lon=${lon}&width_m=${width_m}&height_m=${height_m}&alt_m=${alt_m}`;
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- Boot ---------- */
window.addEventListener("DOMContentLoaded", async () => {
  initMap();

  // Tabs
  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".view").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      $(t.dataset.tab).classList.add("active");
      if (t.dataset.tab === "mapView") setTimeout(() => state.map.invalidateSize(), 120);
    });
  });

  // Layer toggles
  const toggleMap = [
    ["chkPost", "post"],
    ["chkPre", "pre"],
    ["chkChange", "change"],
    ["chkRoads", "roads"],
    ["chkCopernicus", "copernicus"],
    ["chkFacilities", "facilities"],
  ];
  toggleMap.forEach(([cid, key]) => {
    $(cid).addEventListener("change", () => {
      const l = state.layers[key];
      if (!l) return;
      if ($(cid).checked) l.addTo(state.map); else state.map.removeLayer(l);
    });
  });
  $("postOpacity").addEventListener("input", (e) => {
    if (state.layers.post) state.layers.post.setOpacity(parseFloat(e.target.value));
  });

  // Buttons & Modals
  $("btnComputeDetour").addEventListener("click", computeDetour);
  $("authBtn").addEventListener("click", async () => {
    if (state.user) {
      await api("/api/auth/logout", { method: "POST" });
      await refreshSession();
      toast("Signed out.");
    } else {
      $("authModal").classList.add("open");
    }
  });
  $("closeAuth").addEventListener("click", () => $("authModal").classList.remove("open"));
  $("fillCommander").addEventListener("click", () => loginWith("commander@mappa.org", "MappaCmd#2026"));
  $("fillEngineer").addEventListener("click", () => loginWith("engineer@mappa.org", "MappaPwd#2026"));
  $("btnQuickEngineer").addEventListener("click", () => loginWith("engineer@mappa.org", "MappaPwd#2026"));
  $("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    loginWith($("loginEmail").value, $("loginPass").value);
  });

  $("neo4jPill").addEventListener("click", () => { refreshNeo4j(); $("neo4jModal").classList.add("open"); });
  $("closeNeo4j").addEventListener("click", () => $("neo4jModal").classList.remove("open"));
  $("neo4jForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("neo4jErr").textContent = "";
    if (!state.user) {
      $("neo4jErr").textContent = "Please sign in first (Commander or Engineer) to configure Neo4j.";
      return;
    }
    try {
      await api("/api/neo4j/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          uri: $("n4jUri").value.trim(),
          username: $("n4jUser").value.trim(),
          password: $("n4jPass").value,
          database: $("n4jDb").value.trim() || "neo4j",
        }),
      });
      await refreshNeo4j();
      await loadMission(state.missionId);
      $("neo4jModal").classList.remove("open");
      toast("Connected to live Neo4j and synced all missions!");
    } catch (err) {
      $("neo4jErr").textContent = err.message;
    }
  });

  // Upload Pipeline Form
  $("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.user) {
      toast("Please sign in first to run a new mapping mission.");
      $("authModal").classList.add("open");
      return;
    }
    const statusEl = $("uploadStatus");
    statusEl.textContent = "Running offline registration, change detection, road scoring & Neo4j ingest...";
    try {
      const fd = new FormData(e.target);
      const res = await api("/api/missions/run", { method: "POST", body: fd });
      statusEl.textContent = `Done! Processed ${res.report.area_km2} km² in ${res.report.processing_time_s.total} s.`;
      await loadMissionsList(res.mission_id);
      document.querySelector('.tab[data-tab="mapView"]').click();
      toast(`Mission '${res.mission_id}' ready on map & Neo4j!`);
    } catch (err) {
      statusEl.textContent = "Error: " + err.message;
    }
  });

  $("btnCalcPlan").addEventListener("click", calcFlightPlan);
  calcFlightPlan();

  await Promise.all([refreshSession(), refreshNeo4j(), loadMissionsList()]);
});

async function loadMissionsList(selectId = null) {
  const res = await api("/api/missions");
  state.missions = res.missions || [];
  const sel = $("missionSelect");
  sel.innerHTML = "";
  state.missions.forEach((m) => {
    const tag = m.simulated ? "[SIMULATED] " : "[REAL 2026] ";
    sel.add(new Option(tag + m.name, m.id));
  });
  sel.onchange = () => loadMission(sel.value);
  if (state.missions.length) {
    const pick = selectId || state.missions[0].id;
    sel.value = pick;
    await loadMission(pick);
  }
}
