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
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(state.map);
}

function clearMapLayers() {
  Object.values(state.layers).forEach((l) => { if (l && state.map.hasLayer(l)) state.map.removeLayer(l); });
  state.layers = {};
}

function roadWord(st) { return ({BLOCKED:"BLOCKED", UNCERTAIN:"MAYBE BLOCKED", CLEAR:"OPEN", NO_DATA:"NO PHOTO COVERAGE"})[st] || st; }
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
    ? `<strong>PRACTICE MAP (made-up data).</strong> Used to test the system. Not a real disaster.`
    : `<strong>REAL DISASTER:</strong> ${esc(m.name)} (${esc(m.event_date)}). Made from real satellite photos. Processed offline in ${esc(Math.round(rep.processing_time_s?.total || 0))} seconds.`;

  // KPIs
  const rLen = rep.roads?.length_m_by_status || {};
  $("kpiArea").textContent = rep.area_km2;
  $("kpiBlocked").textContent = `${Math.round(rLen.BLOCKED || 0)} m`;
  $("kpiIsolated").textContent = `${iso.isolated_population.toLocaleString()}`;
  $("kpiAcc").textContent = `within ${Math.round(rep.georeferencing?.horizontal_accuracy_95_m || 10)} m`;
  $("accuracyLine").textContent = "Positions on this map can be off by up to about " + Math.round(rep.georeferencing?.horizontal_accuracy_95_m || 10) + " m. Not checked on the ground.";
  const top = pq.queue.find((q) => q.currently_blocked);
  let sum = `<b>${Math.round(rLen.BLOCKED || 0)} m of road is blocked.</b> `;
  if (iso.isolated_count > 0) {
    sum += `<b>${iso.isolated_count} places</b> (about <b>${iso.isolated_population.toLocaleString()} people</b>) are cut off. `;
    if (top) sum += `Clear <b>road ${esc(top.road_name)}</b> first: it reconnects about <b>${top.population_reconnected.toLocaleString()} people</b>.`;
  } else {
    sum += `Everyone mapped can still be reached.`;
  }
  $("summaryBox").innerHTML = sum;

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
      
      layer.bindPopup(
        `<strong>Road ${esc(p.name)}</strong><br/><b>${esc(roadWord(p.status))}${p.cleared_by_pwd ? " (cleared by road crew)" : ""}</b><br/>` +
        `${esc(p.length_m)} m stretch · ${p.changed_frac != null ? Math.round(p.changed_frac * 100) + "% of it looks changed" : ""}`
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
      layer.bindPopup(`<strong>Official damage survey</strong><br/>Verdict: <b>${esc(p.damage_gra || "Surveyed")}</b>`);
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
    const tag = f.is_staging ? " (start point)" : (f.reachable ? "" : " — CUT OFF");
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
    ["brief.pdf", "Print 1-page report", "btn-primary"],
    ["roads_status.kml", "Open in Google Earth", "btn-cyan"],
    ["roads_status.gpkg", "Open in QGIS", ""],
    ["roads_status.geojson", "Roads file", ""],
    ["blocked_and_uncertain_stretches.csv", "Blocked roads list (Excel)", ""],
    ["change_polygons.geojson", "Changed areas", ""],
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
      `About ${esc(f.population_est)} people<br/>` +
      `<b style="color:${color}">${f.is_staging ? "START POINT" : f.reachable ? "CAN BE REACHED" : "CUT OFF"}</b>`
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
    toast("Pick two different places.");
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
      box.innerHTML = `<strong style="color:#f87171">NO SAFE ROAD ROUTE.</strong><br/>` +
        `Blocked: <b>road ${esc(res.blocked_roads_avoided.join(", ") || "on the way")}</b>.<br/>` +
        `<span class="muted">Send a road crew (see the “Road crews” tab), or plan helicopter or foot rescue.</span>`;
    } else if (res.verdict === "SAFE_DETOUR_FOUND") {
      box.innerHTML = `<strong style="color:#22d3ee">SAFE ROUTE FOUND (blue line)</strong><br/>` +
        `<b>${esc(Math.round(res.safe_distance_m))} m</b> long. Goes around blocked road ${esc(res.blocked_roads_avoided.join(", "))}.`;
    } else {
      box.innerHTML = `<strong style="color:#34d399">ROAD IS OPEN</strong><br/>Shortest route: <b>${esc(Math.round(res.safe_distance_m))} m</b>.`;
    }
    $("detourCypher").textContent = res.cypher || "";
    $("detourEngineChip").textContent = res.engine === "neo4j_live" ? "Answered by the live cloud database." : "Answered by the built-in offline copy.";
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- Segment 2: PWD / BRO Clearance Queue ---------- */
function renderPwdQueue(iso, pq) {
  $("pwdIsoCount").textContent = `${iso.isolated_count}`;
  $("pwdIsoNames").textContent = iso.isolated_facilities.length
    ? iso.isolated_facilities.map((f) => f.name).join(", ")
    : "Nobody is cut off.";
  $("pwdIsoPop").textContent = `${iso.isolated_population.toLocaleString()}`;
  $("pwdEngineNote").textContent = "Mark a road as cleared and the map and numbers update at once. Sign in as Road Engineer first.";
  const nxt = pq.queue.find((q) => q.currently_blocked);
  $("pwdSummary").innerHTML = nxt
    ? `<b>Do this first:</b> clear <b>road ${esc(nxt.road_name)}</b> (${esc(nxt.length_m)} m, about ${esc(nxt.est_debris_vol_m3)} m³ of rubble). It reconnects about <b>${nxt.population_reconnected.toLocaleString()} people</b>.`
    : `<b>No blocked roads left to clear.</b>`;
  $("pwdCypher").textContent = pq.cypher || "";
  $("btnExportLedger").href = `/api/missions/${encodeURIComponent(state.missionId)}/work-orders/export.csv`;

  const tbody = $("pwdTableBody");
  if (!pq.queue.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">No blocked roads found.</td></tr>`;
    return;
  }
  tbody.innerHTML = pq.queue.map((item) => {
    const st = item.work_order_status || "OPEN";
    const recon = item.facilities_reconnected?.length
      ? `<strong>${esc(item.population_reconnected.toLocaleString())} people</strong><br/><span class="muted">${esc(item.facilities_reconnected.join(", "))}</span>`
      : `<span class="muted">Helps other roads open</span>`;
    const isCleared = st === "CLEARED";
    return `<tr>
      <td><strong>${esc(item.priority_rank)}</strong></td>
      <td><strong>Road ${esc(item.road_name)}</strong></td>
      <td>${esc(item.length_m)} m</td>
      <td>about ${esc(item.est_debris_vol_m3)} m³</td>
      <td>${recon}</td>
      <td><span class="status-chip st-${esc(st)}">${esc(({OPEN:"WAITING",DISPATCHED:"CREW SENT",CLEARED:"OPEN NOW"})[st] || st)}</span></td>
      <td>${esc(item.assigned_team || "Unassigned")}</td>
      <td>
        ${!isCleared
          ? `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="DISPATCHED">Send crew</button>
             <button class="btn btn-sm btn-green" data-wo="${esc(item.work_order_id)}" data-act="CLEARED">Road is open</button>`
          : `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="OPEN">Blocked again</button>`}
      </td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll("button[data-wo]").forEach((btn) => {
    btn.addEventListener("click", () => handleWorkOrderAction(btn.dataset.wo, btn.dataset.act));
  });
}

async function handleWorkOrderAction(woId, newStatus) {
  if (!state.user || !["engineer", "admin"].includes(state.user.role)) {
    toast("Please sign in as Road Engineer first.");
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
    toast(`Saved. People still cut off: ${res.isolation_after.isolated_population.toLocaleString()}`);
    await loadMission(state.missionId);
    loadAuditLogs();
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- Audit / Validation View ---------- */
const TIME_LABELS = {"1_load_and_resample":"Load photos","2_register_to_base":"Line up the photos","3_change_detection":"Find what changed","4_road_scoring":"Check each road","5_export_geodata":"Save map files","6_brief_figure":"Draw the map","7_pdf":"Make the report","total":"TOTAL"};
function renderAuditView(m) {
  const r0 = m.summary, g0 = r0.georeferencing || {}, cv0 = r0.copernicus_validation, v0 = r0.validation;
  let t = `<p>Positions can be off by up to about <b>${Math.round(g0.horizontal_accuracy_95_m || 10)} m</b>. Not checked on the ground.</p>`;
  if (cv0 && cv0.available) t += `<p>Checked against the official flood survey: we caught about <b>${Math.round(cv0.recall_vs_copernicus_damaged * 100)}%</b> of the damaged road. <b>Expect false alarms</b> (photos only).</p>`;
  else if (v0) t += `<p>Practice map: we know the right answer, and all blocked roads were found.</p>`;
  t += r0.change?.height_used ? "" : `<p>A drone height scan would make blocked-road calls much more reliable.</p>`;
  const tn = $("trustNote"); if (tn) tn.innerHTML = t;
  if (!$("honestyBox")) return;
  const rep = m.summary;
  const g = rep.georeferencing || {};
  const ch = rep.change || {};
  $("honestyBox").innerHTML = `
    <p><b>Position:</b> places on this map can be off by up to about <b>${esc(Math.round(g.horizontal_accuracy_95_m))} m</b>. Nobody has checked this on the ground.</p>
    <p><b>Small debris:</b> the older photo is not very sharp, so small rubble can be missed.</p>
    <p><b>Height check:</b> ${ch.height_used ? "Used (more reliable for rubble on roads)." : "Not used. Fly a drone to get height data; it makes blocked-road calls much more reliable."}</p>
    <details class="tech"><summary>For engineers</summary>
      <p class="muted">Method: ${esc(g.method)}, ${esc(g.n_inliers)} matching points, shift applied ${esc(g.gps_only_correction_m)} m. ${esc(rep.provenance)}</p></details>
  `;

  if (rep.copernicus_validation?.available) {
    const cv = rep.copernicus_validation;
    $("validationBox").innerHTML = `
      <p><b>Yes, against the official Copernicus emergency survey of this flood.</b></p>
      <p>Of the road the experts marked damaged, we flagged about <b>${Math.round(cv.recall_vs_copernicus_damaged * 100)}%</b>.</p>
      <p>Of the road we flagged, about <b>${Math.round(cv.precision_vs_copernicus_damaged * 100)}%</b> was confirmed damaged. So expect false alarms.</p>
      <p>Roads the experts found fine that we also called open: <b>${cv.clear_agreement_on_undamaged != null ? Math.round(cv.clear_agreement_on_undamaged * 100) + "%" : "n/a"}</b>.</p>
      <p class="muted">This is photos only. A drone height scan would improve these numbers a lot.</p>
    `;
  } else if (rep.validation) {
    const v = rep.validation;
    $("validationBox").innerHTML = `
      <p><b>This is a practice map where we know the right answer.</b></p>
      <p>Map position error: <b>${esc(v.error_before_mean_m)} m</b> before fixing, <b>${esc(v.error_after_mean_m)} m</b> after.</p>
      <p>Blocked roads found correctly: <b>${esc(v.roads_vs_truth?.recall)}</b> (1.0 = all). False alarms: <b>${esc(1 - (v.roads_vs_truth?.precision ?? 1))}</b>.</p>
      <p class="muted">Lesson: rubble looks like a dirt road in a photo. Height data is what catches it.</p>
    `;
  } else {
    $("validationBox").innerHTML = `<p class="muted">Your own map. Nothing to compare it against yet.</p>`;
  }

  const tm = rep.processing_time_s || {};
  const total = Math.max(tm.total || 1, 0.1);
  $("timingBox").innerHTML = Object.entries(tm)
    .filter(([k]) => k !== "total_so_far")
    .map(([k, v]) => {
      const pct = Math.min(100, Math.round((v / total) * 100));
      return `<div style="margin-bottom:7px">
        <div style="display:flex;justify-content:space-between;font-size:14px"><span>${esc(TIME_LABELS[k] || k)}</span><strong>${esc(v)} s</strong></div>
        <div style="background:#1e293b;height:7px;border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${k === "total" ? "#f97316" : "#06b6d4"}"></div></div>
      </div>`;
    }).join("") + `<p class="muted" style="margin-top:8px">Total ${esc(Math.round(tm.total || 0))} seconds for ${esc(rep.area_km2)} km². Normal laptop, no internet, no graphics card.</p>`;
}

async function loadAuditLogs() {
  if (!$("auditLogBox")) return;
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
    btn.textContent = `${state.user.name} · Sign out`;
    btn.className = "btn btn-sm";
  } else {
    btn.textContent = "Sign in";
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
    lbl.textContent = "Smart routing: ON (cloud)";
  } else {
    dot.className = "dot dot-amber";
    lbl.textContent = "Smart routing: ON (offline)";
  }
  $("neo4jStatusDetail").innerHTML =
    `${st.connected ? '<b style="color:#34d399">Connected to the cloud database.</b>' : '<b style="color:#fbbf24">Working offline with a built-in copy.</b>'}<br/>` +
    `Safe routes and “what to clear first” work either way. No internet needed in the field.`;
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
    toast(`Signed in as ${res.user.full_name}`);
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
      <b>Fly ${esc(p.n_lines)} straight lines</b> at ${esc(alt_m)} m height, camera pointing straight down.<br/>
      Fly no faster than <b>${esc(p.max_speed_ms)} m/s</b>. Expect about <b>${esc(p.n_images)} photos</b> and <b>~${esc(Math.round(p.flight_time_min))} minutes</b> in the air.
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
      $("neo4jErr").textContent = "Please sign in first.";
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
      toast("Connected.");
    } catch (err) {
      $("neo4jErr").textContent = err.message;
    }
  });

  // Upload Pipeline Form
  $("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.user) {
      toast("Please sign in first.");
      $("authModal").classList.add("open");
      return;
    }
    const statusEl = $("uploadStatus");
    statusEl.textContent = "Working… this takes a few minutes. Keep this page open.";
    try {
      const fd = new FormData(e.target);
      const res = await api("/api/missions/run", { method: "POST", body: fd });
      statusEl.textContent = `Done. ${res.report.area_km2} km² mapped in ${Math.round(res.report.processing_time_s.total)} seconds.`;
      await loadMissionsList(res.mission_id);
      document.querySelector('.tab[data-tab="mapView"]').click();
      toast("Your map is ready.");
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
