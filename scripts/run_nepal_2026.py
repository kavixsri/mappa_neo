"""Run the offline MAPPA-Neo pipeline on REAL 2026 Nepal Bhote Koshi / Trishuli Flood data
(Syabrubesi, Rasuwa District, Copernicus EMS Activation EMSR927) and also refresh the synthetic
validation benchmark with web map overlays."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mappa import demo_data, pipeline
from mappa.graph_neo4j import MappaGraphService

DATA = ROOT / "datasets" / "nepal_2026_syabrubesi"
OUT_NEPAL = ROOT / "examples" / "nepal_2026_run" / "output"
OUT_DEMO = ROOT / "examples" / "demo_run"


def run_real_nepal():
    print("== [1/2] Running MAPPA-Neo OFFLINE on REAL 2026 Nepal Bhote Koshi Flood (Syabrubesi) ==")
    post_tif = DATA / "post_vantor_2026_09_06.tif"
    pre_tif = DATA / "pre_vantor_2021_10_16.tif"
    roads = DATA / "osm_roads.geojson"
    cop_truth = DATA / "copernicus_ems_roads.geojson"

    rep = pipeline.run(
        ortho=str(post_tif),
        base=str(pre_tif),
        roads_path=str(roads),
        out=str(OUT_NEPAL),
        grid_res=1.5,
        base_acc95=5.0,
        simulated=False,
        provenance=(
            "REAL EVENT: 26 Aug 2026 Nepal Bhote Koshi / Trishuli flash flood (Syabrubesi, Rasuwa). "
            "Post: Vantor/Maxar 2026-09-06 via OpenAerialMap; Pre: Vantor/Maxar 2021-10-16 via OpenAerialMap; "
            "Roads: OpenStreetMap; Ground-truth reference: Copernicus EMS Activation EMSR927."
        ),
        thresholds={
            "k": 2.4,
            "floor": 26.0,
            "min_area_m2": 180.0,
            "seg_len_m": 25.0,
            "buffer_m": 8.0,
            "blocked_frac_no_dsm": 0.45,
            "uncertain_frac": 0.18,
        },
        copernicus_truth=str(cop_truth) if cop_truth.exists() else None,
    )
    print(f"  Area: {rep['area_km2']} km2 | Time: {rep['processing_time_s']['total']} s")
    print(f"  Georef: {rep['georeferencing']['method']} | inliers={rep['georeferencing']['n_inliers']} | rms={rep['georeferencing']['registration_rms_m']} m | acc95=+/-{rep['georeferencing']['horizontal_accuracy_95_m']} m")
    print(f"  Changed area: {rep['change']['changed_area_m2']:,} m2 in {rep['change']['n_change_polygons']} polygons")
    print(f"  Roads by status (m): {rep['roads']['length_m_by_status']}")
    print(f"  Blocked stretches: {len(rep['roads']['blocked_stretches'])}")
    if "copernicus_validation" in rep:
        print("  Copernicus EMS EMSR927 check:", json.dumps(rep["copernicus_validation"], indent=2))

    # Build real Syabrubesi facilities list for Neo4j graph
    facilities = [
        {"id": "nepal_2026:staging_south", "name": "Dhunche–Syabrubesi Staging Base (South Highway)", "category": "staging_base", "population_est": 0, "lat": 28.1545, "lon": 85.3340, "is_staging": True},
        {"id": "nepal_2026:syabrubesi_bazar", "name": "Syabrubesi Bazaar & Bus Park", "category": "village", "population_est": 1250, "lat": 28.1618, "lon": 85.3346, "is_staging": False},
        {"id": "nepal_2026:old_syabru", "name": "Old Syabrubesi / Bhote Koshi Bridge", "category": "bridge", "population_est": 640, "lat": 28.1636, "lon": 85.3375, "is_staging": False},
        {"id": "nepal_2026:langtang_trailhead", "name": "Langtang Valley Trailhead & Health Post", "category": "clinic", "population_est": 480, "lat": 28.1652, "lon": 85.3412, "is_staging": False},
        {"id": "nepal_2026:timure_highway_north", "name": "Pasang Lhamu Highway North (Toward Timure)", "category": "village", "population_est": 920, "lat": 28.1702, "lon": 85.3398, "is_staging": False},
        {"id": "nepal_2026:khwang_west", "name": "West Slope Settlement (Khwang / Chilime Rd)", "category": "village", "population_est": 510, "lat": 28.1645, "lon": 85.3278, "is_staging": False},
    ]
    (OUT_NEPAL / "facilities.json").write_text(json.dumps(facilities, indent=2))

    g = MappaGraphService()
    ing = g.ingest_mission("nepal_2026", "Nepal Floods 2026 — Syabrubesi (EMSR927)", OUT_NEPAL / "roads_status.geojson", facilities)
    print("  Graph ingest:", ing)
    iso = g.compute_isolation("nepal_2026")
    print(f"  Isolation: {iso['isolated_count']} facilities cut off ({iso['isolated_population']} est. pop) | reachable: {iso['reachable_count']}")
    pq = g.clearance_priority_queue("nepal_2026")
    print(f"  Clearance priority queue: {len(pq['queue'])} stretches; top 3:")
    for item in pq["queue"][:3]:
        print(f"    #{item['priority_rank']} {item['road_name']} ({item['length_m']} m, ~{item['est_debris_vol_m3']} m3) -> reconnects {item['population_reconnected']} pop ({item['facilities_reconnected']})")


def run_synthetic_demo():
    print("\n== [2/2] Refreshing Synthetic Landslide Validation Scene (with DSM-DEM & web overlays) ==")
    paths = demo_data.generate(str(OUT_DEMO / "input"), base_res_m=10.0)
    rep = pipeline.run(
        ortho=paths["ortho"],
        base=paths["base"],
        roads_path=paths["roads"],
        out=str(OUT_DEMO / "output"),
        dsm=paths["dsm"],
        dem=paths["dem"],
        simulated=True,
        truth=paths["truth"],
        provenance="Synthetic Himalayan hill-road landslide scene with known injected GPS error and DSM-DEM ground truth.",
    )
    print(f"  Synthetic done in {rep['processing_time_s']['total']} s | roads: {rep['roads']['length_m_by_status']} | validation: {rep['validation']['roads_vs_truth']}")


if __name__ == "__main__":
    run_real_nepal()
    run_synthetic_demo()
