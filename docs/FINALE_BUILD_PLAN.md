# MAPPA — Finale Build Plan (Step Zero)
**Problem Statement:** *The First Map After The Landslide* — Offline drone-to-map, GCP-free georeferencing, change detection & blocked-road reporting  
**Team / Repo:** `github.com/kavixsri/MAPPA` (Finale MVP repo in progress) · **Submission Gate:** 4:00 PM, 21 Nov 2026 · **Target:** 12:00 PM, 22 Nov 2026

---

## 1. Where We Are (Honest Prototype Audit)
* **Working today:** End-to-end offline Python CLI (`mappa/`) with SIFT+RANSAC GCP-free registration (recovering 5.7 m injected error → 0.14 m on synthetic scenes), radiometric-normalised colour + DSM−DEM height-anomaly change detection (proved colour-only recall = 0.0 vs 1.0 with height cue), 20 m road-segment scoring (`BLOCKED` / `UNCERTAIN` / `CLEAR`), standard GIS exports (`GeoTIFF`, `GPKG`, `GeoJSON`, `KML`, `CSV`), one-page responder `brief.pdf` with honest `±10 m (95%)` accuracy statement, 30-second pilot capture card PDF, and KML lawnmower mission planner. Zero network imports in `mappa/`; 4 unit tests passing.
* **Mocked, hardcoded, or missing today:**
  * Validated **only on a synthetic landslide scene** — no real disaster pre/post imagery or real ground-truth road grading yet.
  * **CLI-only** — no web UI, no REST API, no persistent database, and no user authentication, so an evaluator cannot open and use it without driving a terminal.
  * **No partner technology** integrated yet.
  * **Single user segment only** (incident commanders) — no second segment workflow.
  * **No ArgosX security scans** run yet and no field-validated Market Fit Canvas / 3-customer interviews yet.

---

## 2. Where We Will Be by 12:00 PM on 22 November
1. **Real 2026 Nepal Flood Dataset Validated End-to-End:** Real pre/post high-resolution imagery and Copernicus EMS (`EMSR927`) road network from the **26 Aug 2026 Bhote Koshi / Trishuli (Rasuwa) flash flood & debris cascade** processed through MAPPA (with memory-safe tiled/windowed execution so ordinary laptops never OOM), alongside our synthetic benchmark and custom imagery upload flow.
2. **Evaluator-Ready Full-Stack Web App & Offline-First Deployment:** FastAPI backend + SQLite/Neo4j persistence + interactive Leaflet GIS dashboard (before/after swipe viewer, change-mask overlay, colour-coded road layers, one-click `brief.pdf` / `GPKG` / `GeoJSON` / `KML` / `CSV` export, live KML mission planner & capture card generator) with **role-based JWT authentication** (`Field Commander` vs `PWD/BRO Clearance Engineer`) — accessible via a live URL and runnable 100% offline locally.
3. **Neo4j Core-Flow Integration:** Live disaster road-connectivity graph powering safe detour routing and isolated-village clearance priority ranking (Section 4).
4. **Two Complete User Segment Journeys:** Both Primary (Rescue Incident Commander) and Segment 2 (PWD/BRO Clearance & DDMA Engineer) working end-to-end in the live build.
5. **Security & Market Validation:** Two **ArgosX** scans (baseline before midnight 21 Nov, remediated final before 11 AM 22 Nov) with remediation log + one-page **Market Fit Canvas** backed by **3 real customer interviews** + **Persona Use Case Matrix**.

---

## 3. User Segments (3 Distinct Segments · 2 Working End-to-End)
| Segment | Their Problem | Use Cases | Live Build Status |
|---|---|---|---|
| **1. Primary:** NDRF / SDRF Incident Commanders & Drone Rescue Teams | Need a georeferenced damage map & passable access routes within 3 hours despite cloud cover and zero connectivity | (a) Offline drone-to-map + blocked-road brief & QGIS/KML export; (b) Neo4j safe detour routing avoiding blocked/uncertain roads | **Working end-to-end** |
| **2. Secondary:** PWD / BRO Highway Clearance & DDMA District Engineers | Multiple cuts along hill corridors; limited excavators; need to know which blockage to clear first and document damage for relief funds | (a) Neo4j **Clearance Priority Queue** ranking blockages by isolated villages/facilities reconnected + debris volume (`m³`) estimate; (b) Auditable damage ledger CSV/GeoJSON export | **Working end-to-end** |
| **3. Tertiary:** Rural Hydropower / Road Corridor Operators & Parametric Insurers | Need repeatable post-monsoon corridor asset exposure screening and community closure alerts | (a) Automated corridor asset exposure overlay; (b) Multilingual (Hindi/Nepali) community closure alerts | **Architecture-ready (Planned)** |

---

## 4. Partner Technology Choice: **Neo4j**
* **Product need solved:** Detecting which 20 m road segments are `BLOCKED` is only half the operational decision — responders and road engineers immediately ask: ***"Which villages and hospitals are now cut off, what is the safest detour around the slide, and which single blockage should excavators clear first to reconnect the most people?"***
* **Core-flow integration (not a token call):** After MAPPA scores road segments, the pipeline ingests the road network into **Neo4j** (`:Junction`, `:Facility` [villages/hospitals/staging bases], `[:ROAD_SEGMENT {status, length_m, changed_frac, height_anom_m, debris_m3}]`). Live **Cypher graph queries** compute:
  1. Shortest **unblocked detour routes** from staging base to target facilities (filtering out `BLOCKED` / `UNCERTAIN` edges).
  2. **Graph reachability / isolation analysis** identifying cut-off communities before vs. after the landslide.
  3. **Clearance-impact ranking** (simulating edge restoration in Cypher to rank which blocked stretch reconnects the highest number of isolated facilities/population first).

---

## 5. Definition of Done
> MAPPA is complete when an evaluator can log into our ArgosX-hardened live web app (or run it offline on a laptop), inspect real 2026 Nepal Bhote Koshi flood pre/post change maps or run a new mapping job, compute Neo4j safe detours and PWD road-clearance priorities across two distinct user roles, and download QGIS-ready GIS layers and an honest-accuracy one-page PDF brief — backed by two ArgosX reports and three real target-user interviews.
