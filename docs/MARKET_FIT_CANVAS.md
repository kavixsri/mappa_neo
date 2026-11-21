# MAPPA-Neo — One-Page Market Fit Canvas (Challenge 04)

| Parameter | Evidence & Specific Plan |
|---|---|
| **Target customer (Primary user & first payer)** | **Primary User:** Field Incident Commanders & Drone Rescue Teams inside **State Disaster Response Forces (SDRF — Uttarakhand, Himachal Pradesh, Sikkim)** and **NDRF Hill Battalions**.<br>**First Payer:** **State Disaster Management Authorities (SDMA / DDMA)** & **Border Roads Organisation (BRO) / State PWD** procuring district rapid-mapping kits under India's **State Disaster Mitigation Fund (SDMF)** / **NDMA Modernization Grant**. |
| **Problem validation** | During Himalayan monsoon disasters (e.g., **26 Aug 2026 Bhote Koshi / Trishuli flash flood, Rasuwa**, Copernicus EMS `EMSR927`; Joshimath / Wayanad / Sikkim GLOF events), optical satellite passes wait 2–5 days and face **74–85% monsoon cloud cover** (directly observed in the Vantor Aug 2026 Nepal catalog). Local teams already fly consumer DJI/ideaForge drones, but raw video cannot be opened in QGIS or used to plan excavator clearance or safe detours. |
| **Current alternative** | (1) Waiting 24–72 hours for Copernicus EMS / NRSC Bhuvan / UNOSAT rapid-mapping activations; (2) Cloud photogrammetry (Pix4Dcloud / DroneDeploy — unusable when cell towers are knocked out); (3) WhatsApp sharing of raw drone video clips + phone calls to village patwaris to guess which road is cut. **Why they switch:** MAPPA-Neo runs offline on an ordinary field laptop in minutes, needs zero ground control points, and outputs QGIS/KML + Neo4j detour & excavator clearance priorities immediately. |
| **Value proposition** | **From consumer drone photos to an actionable, honest-accuracy map, Neo4j safe detour route, and PWD excavator priority list in under 3 hours — completely offline, zero cloud fees, standard GIS formats.** |
| **Business model** | **Open-Core B2G District Annual Subscription (Software + Offline Pre-Cached District Pack + Training):**<br>• Core offline pipeline (`mappa_neo`) remains MIT open source for NGOs & humanitarian responders.<br>• **District Command License:** **₹2,40,000 / district / year** (~₹20,000/month) covering pre-cached Sentinel-2 / Copernicus DEM / OSM district packs, Neo4j AuraDB / on-prem command graph sync, RBAC multi-agency portal (SDRF + PWD/BRO), and pilot training.<br>• **Target initial pilot:** 5 high-risk Himalayan districts (e.g., Chamoli, Rudraprayag, Uttarkashi, Kinnaur, Kullu) = **₹12 Lakh ARR**. |
| **Market opportunity** | According to the **Geological Survey of India (GSI) Landslide Atlas / National Landslide Susceptibility Mapping**, **~0.42 million km² (12.6% of India's land area)** across **147 districts in 17 states & 2 UTs** is landslide-prone (plus 30+ hill districts in Nepal). At ₹2.4L/district/year across 147 Indian priority districts + ~50 highway/hydropower corridor operators, serviceable addressable market (SAM) in India alone is **~₹4.7 Crore / year** (~$560K ARR), expanding across South Asia & ASEAN mountain corridors. |
| **Competitive landscape** | • **Pix4D / DroneDeploy:** Cloud-heavy or expensive per-seat licenses ($300–$500/mo), no road-connectivity graph, no honest base-bounded error report.<br>• **Raw OpenDroneMap (ODM):** Stitches orthophotos only — no GCP-free base registration, no change detection, no blocked-road scoring, no Neo4j detour/clearance queue.<br>• **Copernicus EMS / NRSC:** High quality, but takes 24–72 hours and fails under heavy monsoon clouds.<br>• **MAPPA-Neo difference:** Offline-first + GCP-free honest error budget + **Neo4j road-connectivity & excavator clearance prioritization** + zero-lock-in QGIS exports. |
| **First 100 users** | (1) **District Pilots (Users 1–30):** Partner with 2 Himalayan DDMAs (Uttarakhand / Himachal) & 1 BRO project task force via disaster-tech hackathons & NIDM workshops.<br>• (2) **Humanitarian & Drone-Pilot Communities (Users 31–70):** Distribute through **HOT Open Mapping Hub Asia-Pacific / NAXA (Nepal)** and Indian university drone clubs (IGDTUW, IITs) supporting SDRF.<br>• (3) **PWD & Hydropower Corridor Engineers (Users 71–100):** Offer free monsoon trial using pre-cached state highway corridors. |

---

## The 3 Customer Test (Finale Validation Log)
> **Important Rule:** Talk to at least **3 real people** from the target segment during the finale (phone, video, or in person — e.g., an SDRF/DDMA officer, a civil/PWD highway engineer, a licensed drone pilot/surveyor, or a GIS humanitarian mapper). Fill in the 3 rows below with real notes from your actual conversations before 12:00 PM on 22 November!

### Interview 1 — *(Fill in real name, role & organisation during finale)*
- **Target Segment:** e.g., Drone Pilot / GIS Disaster Mapper / DDMA Officer
- **What we assumed before talking to them:** *(e.g., Pilots can easily set up interval mode & upload raw TIFFs in a CLI.)*
- **What we found out:** *(Exact quote / feedback from conversation)*
- **What we changed (or will change) in MAPPA-Neo:** *(Concrete product change traced to this conversation)*

### Interview 2 — *(Fill in real name, role & organisation during finale)*
- **Target Segment:** e.g., Civil / Highway / PWD Maintenance Engineer
- **What we assumed before talking to them:** *(e.g., Simply marking blocked roads red on a map is enough to dispatch excavators.)*
- **What we found out:** *(Exact quote / feedback from conversation)*
- **What we changed (or will change) in MAPPA-Neo:** *(Concrete product change traced to this conversation)*

### Interview 3 — *(Fill in real name, role & organisation during finale)*
- **Target Segment:** e.g., Emergency Response Coordinator / NGO Relief Logistics Lead
- **What we assumed before talking to them:** *(e.g., 10 m positional accuracy is unacceptable for first-day rescue.)*
- **What we found out:** *(Exact quote / feedback from conversation)*
- **What we changed (or will change) in MAPPA-Neo:** *(Concrete product change traced to this conversation)*
