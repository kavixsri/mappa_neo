"""Verify live Neo4j AuraDB connectivity, ingest Real 2026 Nepal Flood & Synthetic missions,
and test live Cypher isolation, detour routing, and clearance priority queries."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from mappa.graph_neo4j import MappaGraphService

uri = os.environ["NEO4J_URI"]
user = os.environ["NEO4J_USERNAME"]
pw = os.environ["NEO4J_PASSWORD"]

g = MappaGraphService()
if not g.is_live_neo4j:
    # Try fallback database 'neo4j' or username 'neo4j' if needed
    for u_try, db_try in [(user, user), (user, "neo4j"), ("neo4j", "neo4j")]:
        st = g.connect(uri, u_try, pw, db_try)
        if st["connected"]:
            print(f"Connected with user={u_try}, database={db_try}")
            break
else:
    print(f"Connected from .env -> uri={g.neo4j_uri}, db={g.neo4j_db}")

if not g.is_live_neo4j:
    print("FAILED TO CONNECT:", g.neo4j_error)
    sys.exit(1)

# Ingest BOTH missions LIVE into Neo4j AuraDB!
for mid, name, out_rel in [
    ("nepal_2026", "Nepal Floods 2026 — Syabrubesi Corridor (EMSR927)", "examples/nepal_2026_run/output"),
    ("synthetic_demo", "Himalayan Hill-Road Landslide (Synthetic DSM-DEM Benchmark)", "examples/demo_run/output"),
]:
    out_dir = ROOT / out_rel
    fac_p = out_dir / "facilities.json"
    facs = json.loads(fac_p.read_text(encoding="utf-8")) if fac_p.exists() else None
    res = g.ingest_mission(mid, name, out_dir / "roads_status.geojson", facilities=facs)
    print(f"INGESTED {mid} ->", res)

st = g.status()
print("LIVE NEO4J STATUS:", json.dumps(st, indent=2))

# Run LIVE Cypher isolation + detour + clearance queue on Real 2026 Nepal Flood!
iso = g.compute_isolation("nepal_2026")
print(f"LIVE CYPHER ISOLATION (engine={iso['engine']}): {iso['isolated_count']} cut off ({iso['isolated_population']} pop), {iso['reachable_count']} reachable")

staging_id = iso["staging_facility"]["id"]
bazaar_id = "nepal_2026:syabrubesi_bazar"
det = g.find_safe_detour("nepal_2026", staging_id, bazaar_id, avoid_uncertain=False)
print(f"LIVE CYPHER DETOUR Staging->Syabrubesi Bazaar (engine={det['engine']}): verdict={det['verdict']}, safe={det['safe_distance_m']} m, baseline={det['baseline_distance_m']} m")

pq = g.clearance_priority_queue("nepal_2026")
print(f"LIVE CYPHER CLEARANCE QUEUE (engine={pq['engine']}): {len(pq['queue'])} stretches; #1 = {pq['queue'][0]['road_name']} (+{pq['queue'][0]['population_reconnected']} pop, ~{pq['queue'][0]['est_debris_vol_m3']} m3)")
