/* MAPPA Role-Based Frontend Controller (NDRF Rescue Workspace vs. PWD Clearance Workspace) */
const state = {
  user: null,
  missions: [],
  missionId: null,
  missionData: null,
  iso: null,
  pq: null,
  roadsGj: null,
  copGj: null,
  ndrfMap: null,
  pwdMap: null,
  ndrfLayers: {},
  pwdLayers: {},
  selectedCut: null,
};

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 3200);
}

async function api(url, opts = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...opts });
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

const ROAD_COLORS = { BLOCKED: "#c81e1e", UNCERTAIN: "#d97706", CLEAR: "#15803d", NO_DATA: "#9ca3af" };
const ROAD_WORDS = { BLOCKED: "Blocked", UNCERTAIN: "Caution", CLEAR: "Open", NO_DATA: "No coverage" };

/* ---------- Auth & Role Switching ---------- */
async function loginWith(email, password) {
  $("loginErr").textContent = "";
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    state.user = res.user;
    await enterWorkspace();
    toast(`Signed in as ${res.user.full_name}`);
  } catch (e) {
    $("loginErr").textContent = e.message;
  }
}

async function signOut() {
  await api("/api/auth/logout", { method: "POST" });
  state.user = null;
  $("appShell").classList.add("hidden");
  $("loginScreen").classList.remove("hidden");
}

async function switchRole() {
  if (!state.user) return;
  if (state.user.role === "engineer") {
    await loginWith("commander@mappa.org", "MappaCmd#2026");
  } else {
    await loginWith("engineer@mappa.org", "MappaPwd#2026");
  }
}

function isPwdRole() {
  return state.user && state.user.role === "engineer";
}

function setActiveRoleView(viewId) {
  ["ndrfMapView", "ndrfPilotView", "pwdQueueView", "pwdLedgerView"].forEach((id) => {
    $(id).classList.toggle("hidden", id !== viewId);
  });
  const nav = isPwdRole() ? $("pwdNav") : $("ndrfNav");
  nav.querySelectorAll(".nav-pill").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === viewId);
  });
  setTimeout(() => {
    if (viewId === "ndrfMapView" && state.ndrfMap) state.ndrfMap.invalidateSize();
    if (viewId === "pwdQueueView" && state.pwdMap) state.pwdMap.invalidateSize();
  }, 80);
}

async function enterWorkspace() {
  $("loginScreen").classList.add("hidden");
  $("appShell").classList.remove("hidden");

  const pwd = isPwdRole();
  $("userName").textContent = state.user.full_name;
  $("rolePill").textContent = pwd ? "PWD CLEARANCE" : "NDRF RESCUE";
  $("rolePill").className = "role-pill " + (pwd ? "pwd" : "ndrf");
  $("btnSwitchRole").textContent = pwd ? "Switch to NDRF View" : "Switch to PWD View";

  $("ndrfNav").classList.toggle("hidden", pwd);
  $("pwdNav").classList.toggle("hidden", !pwd);

  setActiveRoleView(pwd ? "pwdQueueView" : "ndrfMapView");

  if (!state.ndrfMap) state.ndrfMap = createBaseMap("ndrfMap");
  if (!state.pwdMap) state.pwdMap = createBaseMap("pwdMap");

  await Promise.all([refreshGraphStatus(), loadMissionsList(state.missionId)]);
}

function createBaseMap(elId) {
  const m = L.map(elId, { zoomControl: true, preferCanvas: true }).setView([28.162, 85.336], 14);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(m);
  return m;
}

async function refreshGraphStatus() {
  try {
    const st = await api("/api/neo4j/status");
    const el = $("graphStatus");
    if (st.connected) {
      el.classList.remove("offline");
      el.innerHTML = `<i></i><span>Live road graph (${st.graph_stats?.nodes || 0})</span>`;
    } else {
      el.classList.add("offline");
      el.innerHTML = `<i></i><span>Offline road graph</span>`;
    }
  } catch { /* ignore */ }
}

/* ---------- Missions ---------- */
async function loadMissionsList(selectId = null) {
  const res = await api("/api/missions");
  state.missions = res.missions || [];
  const sel = $("missionSelect");
  sel.innerHTML = "";
  state.missions.forEach((m) => {
    const prefix = m.simulated ? "Practice: " : "Real 2026: ";
    sel.add(new Option(prefix + m.name.replace(" — Syabrubesi Corridor (EMSR927)", " — Syabrubesi"), m.id));
  });
  sel.onchange = () => loadMission(sel.value);
  if (state.missions.length) {
    const pick = selectId || state.missions[0].id;
    sel.value = pick;
    await loadMission(pick);
  }
}

async function loadMission(missionId) {
  state.missionId = missionId;
  const [m, roadsGj, copGj, iso, pq] = await Promise.all([
    api(`/api/missions/${missionId}`),
    api(`/api/missions/${missionId}/geojson/roads_status`),
    api(`/api/missions/${missionId}/geojson/copernicus_roads`),
    api(`/api/missions/${missionId}/graph/isolation`),
    api(`/api/missions/${missionId}/graph/clearance-queue`),
  ]);
  state.missionData = m;
  state.roadsGj = roadsGj;
  state.copGj = copGj;
  state.iso = iso;
  state.pq = pq;

  renderNdrfWorkspace();
  renderPwdWorkspace();
}

/* ---------- AUDIENCE A: NDRF RENDER ---------- */
function clearLayers(map, store) {
  Object.values(store).forEach((l) => { if (l && map.hasLayer(l)) map.removeLayer(l); });
  Object.keys(store).forEach((k) => delete store[k]);
}

function renderNdrfWorkspace() {
  const m = state.missionData, rep = m.summary, iso = state.iso, pq = state.pq;
  const rLen = rep.roads?.length_m_by_status || {};
  const blockedM = Math.round(rLen.BLOCKED || 0);

  // Floating situation strip
  const top = pq.queue.find((q) => q.currently_blocked);
  let strip = `<strong>${blockedM} m of road blocked</strong> · ${iso.isolated_count} places cut off (~${iso.isolated_population.toLocaleString()} people)`;
  if (top) strip += ` · Highest impact cut: Road ${esc(top.road_name)}`;
  if (m.simulated) strip = `<span class="mono" style="font-size:11px;color:#b45309">[PRACTICE DATA]</span> ` + strip;
  $("ndrfSituationStrip").innerHTML = strip;

  // Map layers
  clearLayers(state.ndrfMap, state.ndrfLayers);
  const bounds = m.bounds_wgs84;
  if (bounds) {
    state.ndrfLayers.pre = L.imageOverlay(`/api/missions/${state.missionId}/overlay/web_pre.png`, bounds, { opacity: 0.92 });
    state.ndrfLayers.post = L.imageOverlay(`/api/missions/${state.missionId}/overlay/web_post.png`, bounds, { opacity: parseFloat($("postOpacity").value) });
    state.ndrfLayers.change = L.imageOverlay(`/api/missions/${state.missionId}/overlay/web_change.png`, bounds, { opacity: 0.62 });
    if ($("chkPre").checked) state.ndrfLayers.pre.addTo(state.ndrfMap);
    if ($("chkPost").checked) state.ndrfLayers.post.addTo(state.ndrfMap);
    if ($("chkChange").checked) state.ndrfLayers.change.addTo(state.ndrfMap);
    state.ndrfMap.fitBounds(bounds, { padding: [24, 24] });
  }

  state.ndrfLayers.roads = L.geoJSON(state.roadsGj, {
    style: (f) => {
      const st = f.properties?.status || "CLEAR";
      return { color: ROAD_COLORS[st] || "#6b7280", weight: st === "BLOCKED" ? 5 : st === "UNCERTAIN" ? 3.5 : 2.5, opacity: 0.95 };
    },
    onEachFeature: (f, layer) => {
      const p = f.properties || {};
      const coords = f.geometry?.coordinates || [];
      const mid = coords.length ? coords[Math.floor(coords.length / 2)] : null;
      const btnHtml = mid
        ? `<br/><button class="btn btn-sm btn-dark" style="margin-top:6px" onclick="window.inspectCut(${mid[1]},${mid[0]},'${esc(p.name)}','${esc(p.status)}')">Inspect Before/After (Qwen-VL)</button>`
        : "";
      layer.bindPopup(`<strong>Road ${esc(p.name)}</strong><br/>Status: <b>${esc(ROAD_WORDS[p.status] || p.status)}</b><br/>Length: ${esc(p.length_m)} m${btnHtml}`);
    },
  });
  if ($("chkRoads").checked) state.ndrfLayers.roads.addTo(state.ndrfMap);

  state.ndrfLayers.copernicus = L.geoJSON(state.copGj, {
    style: () => ({ color: "#7e22ce", weight: 4, dashArray: "6 5", opacity: 0.9 }),
    onEachFeature: (f, layer) => layer.bindPopup(`<strong>Official Survey</strong><br/>${esc(f.properties?.damage_gra || "Graded")}`),
  });
  if ($("chkCopernicus").checked && state.copGj.features?.length) state.ndrfLayers.copernicus.addTo(state.ndrfMap);

  // Facilities
  const facGrp = L.layerGroup();
  [...iso.reachable_facilities, ...iso.isolated_facilities].forEach((f) => {
    const col = f.is_staging ? "#1d4ed8" : f.reachable ? "#15803d" : "#c81e1e";
    L.circleMarker([f.lat, f.lon], { radius: f.is_staging ? 8 : 6.5, color: "#fff", weight: 2, fillColor: col, fillOpacity: 0.95 })
      .bindPopup(`<strong>${esc(f.name)}</strong><br/>Est. population: ${esc(f.population_est)}<br/><b style="color:${col}">${f.is_staging ? "Staging Base" : f.reachable ? "Reachable" : "Cut Off"}</b>`)
      .addTo(facGrp);
  });
  state.ndrfLayers.facilities = facGrp.addTo(state.ndrfMap);

  // Route dropdowns
  const allFacs = [...iso.reachable_facilities, ...iso.isolated_facilities];
  const fromSel = $("fromFac"), toSel = $("toFac");
  fromSel.innerHTML = ""; toSel.innerHTML = "";
  allFacs.forEach((f) => {
    const suffix = f.is_staging ? " (Staging Base)" : (f.reachable ? "" : " — Cut Off");
    fromSel.add(new Option(f.name + suffix, f.id));
    toSel.add(new Option(f.name + suffix, f.id));
  });
  if (iso.staging_facility) fromSel.value = iso.staging_facility.id;
  if (iso.isolated_facilities.length) toSel.value = iso.isolated_facilities[0].id;
  else if (allFacs.length > 1) toSel.value = allFacs[1].id;
  $("detourResult").classList.add("hidden");

  // Exports
  const exports = [
    ["brief.pdf", "1-Page Situation Brief", "PDF"],
    ["roads_status.kml", "Google Earth Layer", "KML"],
    ["roads_status.gpkg", "QGIS Road Package", "GPKG"],
    ["blocked_and_uncertain_stretches.csv", "Blocked Roads List", "CSV"],
  ];
  $("exportList").innerHTML = exports.map(([fn, lbl, fmt]) =>
    `<a class="export-item" href="/api/missions/${esc(state.missionId)}/download/${esc(fn)}">${esc(lbl)}<span>${fmt}</span></a>`
  ).join("");

  // Plain-English Reliability Note
  const acc = Math.round(rep.georeferencing?.horizontal_accuracy_95_m || 10);
  const cv = rep.copernicus_validation;
  let trust = `Positions are accurate to within about <b>±${acc} m</b> (unverified on the ground). Processed offline in <b>${Math.round(rep.processing_time_s?.total || 0)} s</b>.`;
  if (cv?.available) {
    trust += `<br/><br/>Checked against the official Copernicus flood survey: matched <b>${Math.round(cv.recall_vs_copernicus_damaged * 100)}%</b> of damaged roads (photos only — expect false alarms until a drone height scan is added).`;
  } else if (rep.validation) {
    trust += `<br/><br/>Practice benchmark with height data: all blocked road segments detected.`;
  }
  $("ndrfTrustBox").innerHTML = trust;
}

async function computeDetour() {
  const fromId = $("fromFac").value, toId = $("toFac").value;
  if (!fromId || !toId || fromId === toId) { toast("Choose two different places."); return; }
  try {
    const res = await api(`/api/missions/${state.missionId}/graph/detour`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_facility_id: fromId, to_facility_id: toId, avoid_uncertain: $("avoidUncertain").checked }),
    });
    if (state.ndrfLayers.detour && state.ndrfMap.hasLayer(state.ndrfLayers.detour)) state.ndrfMap.removeLayer(state.ndrfLayers.detour);
    const grp = L.layerGroup();
    if (res.baseline_coords_ll?.length) L.polyline(res.baseline_coords_ll, { color: "#c81e1e", weight: 3.5, dashArray: "6 6", opacity: 0.75 }).addTo(grp);
    if (res.safe_coords_ll?.length) L.polyline(res.safe_coords_ll, { color: "#1d4ed8", weight: 5.5, opacity: 0.98 }).addTo(grp);
    state.ndrfLayers.detour = grp.addTo(state.ndrfMap);

    const box = $("detourResult");
    box.classList.remove("hidden", "ok", "detour", "blocked");
    if (res.verdict === "ISOLATED_NO_SAFE_ROUTE") {
      box.classList.add("blocked");
      box.innerHTML = `<strong>No passable road route.</strong><br/>Blocked along Road ${esc(res.blocked_roads_avoided.join(", ") || "corridor")}. Coordinate with PWD clearance or plan air/foot access.`;
    } else if (res.verdict === "SAFE_DETOUR_FOUND") {
      box.classList.add("detour");
      box.innerHTML = `<strong>Safe detour found (blue line).</strong><br/>Distance: <b>${Math.round(res.safe_distance_m)} m</b> (+${Math.round(res.detour_overhead_m)} m detour avoiding Road ${esc(res.blocked_roads_avoided.join(", "))}).`;
    } else {
      box.classList.add("ok");
      box.innerHTML = `<strong>Route is open (blue line).</strong><br/>Shortest passable distance: <b>${Math.round(res.safe_distance_m)} m</b>.`;
    }
  } catch (e) { toast(e.message); }
}

/* ---------- AUDIENCE B: PWD CLEARANCE RENDER ---------- */
function renderPwdWorkspace() {
  const m = state.missionData, rep = m.summary, iso = state.iso, pq = state.pq;
  const rLen = rep.roads?.length_m_by_status || {};
  const totalRubble = pq.queue.reduce((s, q) => s + (q.currently_blocked ? (q.est_debris_vol_m3 || 0) : 0), 0);
  const clearedCount = pq.queue.filter((q) => q.work_order_status === "CLEARED").length;

  $("pwdKpiBlocked").textContent = `${Math.round(rLen.BLOCKED || 0)} m`;
  $("pwdKpiPop").textContent = `${iso.isolated_population.toLocaleString()} (${iso.isolated_count} sites)`;
  $("pwdKpiRubble").textContent = `~${totalRubble.toLocaleString()} m³`;
  $("pwdKpiCleared").textContent = `${clearedCount} / ${pq.queue.length}`;

  const csvUrl = `/api/missions/${encodeURIComponent(state.missionId)}/work-orders/export.csv`;
  $("btnExportLedger").href = csvUrl;
  $("btnExportLedger2").href = csvUrl;

  // Clearance Priority Cards
  const list = $("cutList");
  if (!pq.queue.length) {
    list.innerHTML = `<div class="panel">No blocked road stretches in this mission.</div>`;
  } else {
    list.innerHTML = pq.queue.map((item, idx) => {
      const st = item.work_order_status || "OPEN";
      const isCleared = st === "CLEARED";
      const impact = item.facilities_reconnected?.length
        ? `Reconnects ~${item.population_reconnected.toLocaleString()} people (${esc(item.facilities_reconnected.join(", "))})`
        : "Secondary road cut";
      return `<div class="cut-card ${idx === 0 && !isCleared ? "priority-top" : ""}" data-lat="${item.lat}" data-lon="${item.lon}" data-key="${esc(item.stretch_key)}">
        <div class="cut-rank">#${esc(item.priority_rank)}</div>
        <div class="cut-meta">
          <h4>Road ${esc(item.road_name)} <span class="mono" style="font-weight:500;font-size:12px;color:var(--muted)">· ${esc(item.length_m)} m · ~${esc(item.est_debris_vol_m3)} m³ rubble</span></h4>
          <div class="cut-impact">${impact}</div>
          <p>${item.assigned_team ? "Crew: " + esc(item.assigned_team) : "No crew assigned yet"}</p>
        </div>
        <div class="cut-actions">
          <span class="badge badge-${esc(st)}">${st === "OPEN" ? "BLOCKED" : st === "DISPATCHED" ? "CREW SENT" : "REOPENED"}</span>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
            <button class="btn btn-sm" data-inspect="1" data-lat="${item.lat}" data-lon="${item.lon}" data-road="${esc(item.road_name)}" data-st="${isCleared ? "CLEAR" : "BLOCKED"}">Inspect (Qwen)</button>
            ${!isCleared
              ? `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="DISPATCHED">Dispatch</button>
                 <button class="btn btn-sm btn-green" data-wo="${esc(item.work_order_id)}" data-act="CLEARED">Mark Open</button>`
              : `<button class="btn btn-sm" data-wo="${esc(item.work_order_id)}" data-act="OPEN">Re-Block</button>`}
          </div>
        </div>
      </div>`;
    }).join("");

    list.querySelectorAll(".cut-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        list.querySelectorAll(".cut-card").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
        const lat = parseFloat(card.dataset.lat), lon = parseFloat(card.dataset.lon);
        if (!Number.isNaN(lat) && state.pwdMap) {
          state.pwdMap.flyTo([lat, lon], 16, { duration: 0.6 });
          if (state.pwdLayers.focus) state.pwdMap.removeLayer(state.pwdLayers.focus);
          state.pwdLayers.focus = L.circleMarker([lat, lon], { radius: 14, color: "#1d4ed8", weight: 3, fillOpacity: 0.15 }).addTo(state.pwdMap);
        }
      });
    });

    list.querySelectorAll("button[data-wo]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        updateWorkOrder(btn.dataset.wo, btn.dataset.act);
      });
    });
    list.querySelectorAll("button[data-inspect]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openVisualInspect(parseFloat(btn.dataset.lat), parseFloat(btn.dataset.lon), btn.dataset.road, btn.dataset.st);
      });
    });
  }

  // Ledger table
  $("ledgerBody").innerHTML = pq.queue.map((item) => {
    const st = item.work_order_status || "OPEN";
    return `<tr>
      <td class="mono"><strong>#${esc(item.priority_rank)}</strong></td>
      <td><strong>Road ${esc(item.road_name)}</strong></td>
      <td class="mono">${esc(item.length_m)} m</td>
      <td class="mono">~${esc(item.est_debris_vol_m3)} m³</td>
      <td>${item.facilities_reconnected?.length ? esc(item.facilities_reconnected.join(", ")) + ` (~${item.population_reconnected})` : "—"}</td>
      <td><span class="badge badge-${esc(st)}">${esc(st)}</span></td>
      <td>${esc(item.assigned_team || "Unassigned")}</td>
    </tr>`;
  }).join("");

  // Synchronized PWD Corridor Map
  clearLayers(state.pwdMap, state.pwdLayers);
  if (m.bounds_wgs84) {
    state.pwdLayers.post = L.imageOverlay(`/api/missions/${state.missionId}/overlay/web_post.png`, m.bounds_wgs84, { opacity: 0.85 }).addTo(state.pwdMap);
    state.pwdMap.fitBounds(m.bounds_wgs84, { padding: [20, 20] });
  }
  state.pwdLayers.roads = L.geoJSON(state.roadsGj, {
    style: (f) => {
      const st = f.properties?.status || "CLEAR";
      return { color: ROAD_COLORS[st] || "#6b7280", weight: st === "BLOCKED" ? 5 : 2.5, opacity: 0.95 };
    },
  }).addTo(state.pwdMap);

  const fGrp = L.layerGroup();
  [...iso.reachable_facilities, ...iso.isolated_facilities].forEach((f) => {
    const col = f.is_staging ? "#1d4ed8" : f.reachable ? "#15803d" : "#c81e1e";
    L.circleMarker([f.lat, f.lon], { radius: 6.5, color: "#fff", weight: 2, fillColor: col, fillOpacity: 0.95 })
      .bindPopup(`<strong>${esc(f.name)}</strong><br/>${f.reachable ? "Connected" : "Cut off (~" + f.population_est + " people)"}`)
      .addTo(fGrp);
  });
  state.pwdLayers.facilities = fGrp.addTo(state.pwdMap);
}

async function updateWorkOrder(woId, newStatus) {
  const team = newStatus === "DISPATCHED" ? "BRO Excavator Unit 04" : newStatus === "CLEARED" ? "BRO Unit 04 (Cleared)" : "";
  try {
    const res = await api(`/api/missions/${state.missionId}/work-orders/${woId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus, assigned_team: team, notes: `Updated to ${newStatus}` }),
    });
    toast(`Status saved (${newStatus}). Cut-off population now: ${res.isolation_after.isolated_population.toLocaleString()}`);
    await loadMission(state.missionId);
  } catch (e) { toast(e.message); }
}

/* ---------- Sarvam AI Multilingual Field Voice Brief ---------- */
async function playSarvamBrief(role, langSelectId, outBoxId) {
  const lang = $(langSelectId).value || "hi-IN";
  const box = $(outBoxId);
  box.classList.remove("hidden", "ok", "detour", "blocked");
  box.innerHTML = `<span style="color:var(--muted)">Translating &amp; generating Sarvam AI voice brief (${esc(lang)})…</span>`;
  try {
    const res = await api(`/api/missions/${state.missionId}/voice-brief`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language_code: lang, role }),
    });
    const audioHtml = res.audio_data_uri
      ? `<audio controls autoplay style="width:100%;margin-top:8px;height:36px" src="${res.audio_data_uri}"></audio>
         <div style="margin-top:6px"><a class="btn btn-sm" download="${esc(state.missionId)}_${esc(role)}_${esc(lang)}.wav" href="${res.audio_data_uri}">Download WAV Radio Clip</a></div>`
      : "";
    const errHtml = res.error ? `<div style="margin-top:6px;font-size:12px;color:var(--signal-amber)">${esc(res.error)}</div>` : "";
    box.classList.add(res.audio_data_uri ? "detour" : "ok");
    box.innerHTML = `
      <div style="font-size:11.5px;font-weight:700;color:var(--muted);margin-bottom:4px">SARVAM AI · ${esc(res.language_name)}</div>
      <div style="font-size:13.5px;font-weight:600;color:var(--ink)">${esc(res.translated_text)}</div>
      ${audioHtml}
      ${errHtml}
    `;
  } catch (e) {
    box.classList.add("blocked");
    box.innerHTML = `<span class="err-text">${esc(e.message)}</span>`;
  }
}

/* ---------- Qwen2.5-VL Before/After Visual Inspection ---------- */
async function openVisualInspect(lat, lon, roadName, mappaStatus) {
  const modal = $("inspectModal");
  modal.classList.remove("hidden");
  $("inspectTitle").textContent = `Visual Check — Road ${roadName}`;
  $("inspectMeta").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)} · Flagged: ${mappaStatus}`;
  $("inspectBody").innerHTML = `<p style="color:var(--muted)">Cropping aligned Before vs. After photos and querying Qwen-VL…</p>`;
  try {
    const res = await api(`/api/missions/${state.missionId}/inspect-point`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon, road_name: roadName, mappa_status: mappaStatus }),
    });
    const verdictLabel = ({
      CONFIRMED_BLOCKED: "CONFIRMED BLOCKED",
      POSSIBLE_DAMAGE: "POSSIBLE DAMAGE",
      FALSE_ALARM_CLEAR: "FALSE ALARM — ROAD CLEAR",
      UNVERIFIED_NO_QWEN: "BEFORE / AFTER CROP READY (QWEN KEY NOT SET)",
      API_ERROR: "QWEN-VL API ERROR (MANUAL CHECK)",
    })[res.verdict] || res.verdict;
    const confStr = res.confidence != null ? ` · Confidence: ${Math.round(res.confidence * 100)}%` : "";
    const modelStr = res.model ? ` · Model: ${esc(res.model)}` : "";
    $("inspectBody").innerHTML = `
      <img class="inspect-img" src="${res.patch_data_uri}" alt="Before and after comparison crop" />
      <div class="verdict-card ${esc(res.verdict)}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <strong>${esc(verdictLabel)}</strong>
          <span class="mono" style="font-size:11.5px;color:var(--muted)">${esc(res.damage_type || "")}${confStr}${modelStr}</span>
        </div>
        <p style="margin-bottom:6px">${esc(res.summary)}</p>
        <p style="font-size:12.5px;color:var(--ink-2)"><strong>Recommended action:</strong> ${esc(res.recommended_action)}</p>
      </div>
    `;
  } catch (e) {
    $("inspectBody").innerHTML = `<p class="err-text">${esc(e.message)}</p>`;
  }
}
window.inspectCut = openVisualInspect;

/* ---------- Flight Planner ---------- */
async function calcFlightPlan() {
  const lat = parseFloat($("fpLat").value), lon = parseFloat($("fpLon").value);
  const width_m = parseFloat($("fpW").value), height_m = parseFloat($("fpH").value), alt_m = parseFloat($("fpAlt").value);
  try {
    const res = await api("/api/tools/flight-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon, width_m, height_m, alt_m }),
    });
    const p = res.plan;
    $("planResult").innerHTML = `<strong>${p.n_lines} straight flight lines</strong> at ${alt_m} m height (camera straight down).<br/>Max speed: <b>${p.max_speed_ms} m/s</b> · Photos: <b>${p.n_images}</b> · Est. flight time: <b>~${Math.round(p.flight_time_min)} min</b>.`;
    $("btnDownloadKml").href = `/api/tools/flight-plan.kml?lat=${lat}&lon=${lon}&width_m=${width_m}&height_m=${height_m}&alt_m=${alt_m}`;
  } catch (e) { toast(e.message); }
}

/* ---------- Boot ---------- */
window.addEventListener("DOMContentLoaded", async () => {
  // Login events
  $("quickNdrf").addEventListener("click", () => loginWith("commander@mappa.org", "MappaCmd#2026"));
  $("quickPwd").addEventListener("click", () => loginWith("engineer@mappa.org", "MappaPwd#2026"));
  $("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    loginWith($("loginEmail").value, $("loginPass").value);
  });
  $("btnSignOut").addEventListener("click", signOut);
  $("btnSwitchRole").addEventListener("click", switchRole);

  // Role navigation
  document.querySelectorAll(".nav-pill").forEach((b) => {
    b.addEventListener("click", () => setActiveRoleView(b.dataset.view));
  });

  // NDRF layer toggles
  [["chkPost", "post"], ["chkPre", "pre"], ["chkChange", "change"], ["chkRoads", "roads"], ["chkCopernicus", "copernicus"]].forEach(([cid, key]) => {
    $(cid).addEventListener("change", () => {
      const l = state.ndrfLayers[key];
      if (!l) return;
      if ($(cid).checked) l.addTo(state.ndrfMap); else state.ndrfMap.removeLayer(l);
    });
  });
  $("postOpacity").addEventListener("input", (e) => {
    if (state.ndrfLayers.post) state.ndrfLayers.post.setOpacity(parseFloat(e.target.value));
  });
  $("btnComputeDetour").addEventListener("click", computeDetour);
  $("btnNdrfVoice").addEventListener("click", () => playSarvamBrief("commander", "ndrfLang", "ndrfVoiceOut"));
  $("btnPwdVoice").addEventListener("click", () => playSarvamBrief("engineer", "pwdLang", "pwdVoiceOut"));
  $("closeInspect").addEventListener("click", () => $("inspectModal").classList.add("hidden"));
  $("inspectModal").addEventListener("click", (e) => { if (e.target === $("inspectModal")) $("inspectModal").classList.add("hidden"); });
  $("btnNdrfInspectTop").addEventListener("click", () => {
    const top = state.pq?.queue?.find((q) => q.currently_blocked) || state.pq?.queue?.[0];
    if (!top) { toast("No blocked cut to inspect."); return; }
    openVisualInspect(top.lat, top.lon, top.road_name, "BLOCKED");
  });

  // Pilot tools & upload
  $("btnCalcPlan").addEventListener("click", calcFlightPlan);
  calcFlightPlan();
  $("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("uploadStatus").textContent = "Processing imagery offline and updating road routes…";
    try {
      const res = await api("/api/missions/run", { method: "POST", body: new FormData(e.target) });
      $("uploadStatus").textContent = `Ready: ${res.report.area_km2} km² mapped in ${Math.round(res.report.processing_time_s.total)} s.`;
      await loadMissionsList(res.mission_id);
      setActiveRoleView("ndrfMapView");
      toast("New map ready.");
    } catch (err) { $("uploadStatus").textContent = err.message; }
  });

  // Check existing session
  try {
    const me = await api("/api/auth/me");
    if (me.authenticated && me.user) {
      state.user = {
        email: me.user.sub,
        full_name: me.user.name,
        organization: me.user.org,
        role: me.user.role,
      };
      await enterWorkspace();
    }
  } catch { /* stay on login gate */ }
});
