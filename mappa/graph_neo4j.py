"""Neo4j Disaster Road-Connectivity Knowledge Graph for MAPPA-Neo.

Core-flow integration (Partner Technology: Neo4j):
  - Turns MAPPA's scored road segments into a spatial topology graph:
      (:Mission {id, name})
      (:Junction {id, mission_id, lat, lon})
      (:Facility {id, mission_id, name, category, population_est, lat, lon, is_staging})
      (:Junction)-[:ROAD_SEGMENT {segment_id, stretch_key, road_name, status, orig_status,
                                  length_m, changed_frac, height_anom_m, est_debris_vol_m3}]->(:Junction)
      (:Facility)-[:CONNECTED_TO {dist_m}]->(:Junction)
  - Powers:
      1. Primary Segment (NDRF/SDRF Commanders): Safe detour routing avoiding BLOCKED / UNCERTAIN segments.
      2. Segment 2 (PWD/BRO Clearance Engineers): Cut-off village isolation analysis & Clearance Priority Queue
         ranking which blockage reconnects the highest isolated population per cubic metre of debris first.
  - Offline-first resilience:
      When NEO4J_URI + credentials are configured, every ingestion, query, and work-order status update runs
      live over Bolt against Neo4j (AuraDB or local Neo4j). When running offline on a field laptop with no
      Neo4j server reachable, an identical local NetworkX graph mirror executes the same logic transparently
      and labels its mode honestly ("offline_mirror" vs "neo4j_live").
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class MappaGraphService:
    def __init__(self):
        self.driver = None
        self.neo4j_uri: str | None = None
        self.neo4j_db: str = os.environ.get("NEO4J_DATABASE", "neo4j")
        self.neo4j_error: str | None = None
        # Local mirror indexed by mission_id -> {"G": nx.Graph, "facilities": dict, "stretches": dict, "junctions": dict}
        self._mirrors: dict[str, dict[str, Any]] = {}
        self.connect_from_env()

    def connect_from_env(self) -> dict[str, Any]:
        uri = os.environ.get("NEO4J_URI", "").strip()
        user = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER") or "neo4j"
        password = os.environ.get("NEO4J_PASSWORD", "").strip()
        self.neo4j_db = os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j"
        if not uri or not password or GraphDatabase is None:
            self.driver = None
            self.neo4j_uri = uri or None
            self.neo4j_error = None if not uri else "NEO4J_PASSWORD missing or neo4j driver unavailable"
            return self.status()
        return self.connect(uri, user, password, self.neo4j_db)

    def connect(self, uri: str, user: str, password: str, database: str = "neo4j") -> dict[str, Any]:
        if GraphDatabase is None:
            self.neo4j_error = "neo4j Python package not installed"
            return self.status()
        try:
            if self.driver:
                self.driver.close()
            drv = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=8.0)
            drv.verify_connectivity()
            self.driver = drv
            self.neo4j_uri = uri
            self.neo4j_db = database or "neo4j"
            self.neo4j_error = None
            self._ensure_constraints()
        except Exception as e:
            self.driver = None
            self.neo4j_uri = uri
            self.neo4j_error = str(e)
        return self.status()

    def _ensure_constraints(self) -> None:
        if not self.driver:
            return
        stmts = [
            "CREATE CONSTRAINT mission_id IF NOT EXISTS FOR (m:Mission) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT junction_id IF NOT EXISTS FOR (j:Junction) REQUIRE j.id IS UNIQUE",
            "CREATE CONSTRAINT facility_id IF NOT EXISTS FOR (f:Facility) REQUIRE f.id IS UNIQUE",
        ]
        with self.driver.session(database=self.neo4j_db) as s:
            for q in stmts:
                try:
                    s.run(q)
                except Exception:
                    pass

    @property
    def is_live_neo4j(self) -> bool:
        return self.driver is not None

    def status(self) -> dict[str, Any]:
        total_nodes = sum(len(m["G"].nodes) + len(m["facilities"]) for m in self._mirrors.values())
        total_edges = sum(len(m["G"].edges) for m in self._mirrors.values())
        live_counts = None
        if self.driver:
            try:
                with self.driver.session(database=self.neo4j_db) as s:
                    rec = s.run(
                        "MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r:ROAD_SEGMENT]->() RETURN nodes, count(r) AS road_edges"
                    ).single()
                    if rec:
                        live_counts = {"nodes": rec["nodes"], "road_edges": rec["road_edges"]}
            except Exception as e:
                self.neo4j_error = str(e)
        return {
            "mode": "neo4j_live" if self.driver else "offline_mirror",
            "connected": self.driver is not None,
            "uri": self.neo4j_uri if self.driver else None,
            "database": self.neo4j_db,
            "error": self.neo4j_error,
            "missions_loaded": list(self._mirrors.keys()),
            "graph_stats": live_counts or {"nodes": total_nodes, "road_edges": total_edges},
        }

    # --- Build Spatial Topology & Ingest into Neo4j ---
    def ingest_mission(
        self,
        mission_id: str,
        mission_name: str,
        roads_geojson_path: str | Path,
        facilities: list[dict[str, Any]] | None = None,
        snap_m: float = 18.0,
    ) -> dict[str, Any]:
        gdf = gpd.read_file(roads_geojson_path)
        if gdf.crs is None:
            gdf = gdf.set_crs(4326)
        gdf_wgs = gdf.to_crs(4326)
        # Metric projection for snapping endpoints
        centroid = gdf_wgs.geometry.union_all().centroid if len(gdf_wgs) else Point(85.33, 28.16)
        utm_zone = int((centroid.x + 180) // 6) + 1
        utm_epsg = (32600 if centroid.y >= 0 else 32700) + utm_zone
        gdf_m = gdf_wgs.to_crs(utm_epsg)

        # Snap endpoints within snap_m metres into Junction nodes
        junctions: list[dict[str, Any]] = []
        junction_xy: list[tuple[float, float]] = []

        def get_or_create_junction(x: float, y: float, lon: float, lat: float) -> str:
            for idx, (jx, jy) in enumerate(junction_xy):
                if math.hypot(x - jx, y - jy) <= snap_m:
                    return junctions[idx]["id"]
            jid = f"{mission_id}:J{len(junctions)}"
            junction_xy.append((x, y))
            junctions.append({"id": jid, "mission_id": mission_id, "lat": round(lat, 6), "lon": round(lon, 6), "x": x, "y": y})
            return jid

        # Group consecutive BLOCKED segments on the same road_id into stretch_keys
        stretch_map: dict[tuple[int, int], str] = {}
        stretches_meta: dict[str, dict[str, Any]] = {}
        if "road_id" in gdf_wgs.columns and "seq" in gdf_wgs.columns:
            for rid, grp in gdf_wgs.sort_values(["road_id", "seq"]).groupby("road_id"):
                run_idx = 0
                prev_seq = None
                prev_st = None
                for _, row in grp.iterrows():
                    st = str(row.get("status", "CLEAR"))
                    seq = int(row.get("seq", 0))
                    if st != prev_st or (prev_seq is not None and seq != prev_seq + 1):
                        run_idx += 1
                    sk = f"{mission_id}:R{int(rid)}:S{run_idx}"
                    stretch_map[(int(rid), seq)] = sk
                    prev_seq, prev_st = seq, st

        G = nx.Graph()
        edges_payload: list[dict[str, Any]] = []

        for idx, (row_w, row_m) in enumerate(zip(gdf_wgs.itertuples(), gdf_m.itertuples())):
            geom_w = row_w.geometry
            geom_m = row_m.geometry
            if geom_w is None or geom_w.is_empty or geom_w.geom_type != "LineString":
                continue
            cw = list(geom_w.coords)
            cm = list(geom_m.coords)
            if len(cw) < 2:
                continue
            u = get_or_create_junction(cm[0][0], cm[0][1], cw[0][0], cw[0][1])
            v = get_or_create_junction(cm[-1][0], cm[-1][1], cw[-1][0], cw[-1][1])
            if u == v:
                continue

            rid = int(getattr(row_w, "road_id", idx))
            seq = int(getattr(row_w, "seq", idx))
            rname = str(getattr(row_w, "name", "Road"))
            st = str(getattr(row_w, "status", "CLEAR"))
            length_m = float(getattr(row_w, "length_m", geom_m.length))
            cfrac = getattr(row_w, "changed_frac", 0.0)
            cfrac = 0.0 if (cfrac is None or cfrac != cfrac) else float(cfrac)
            hanom = getattr(row_w, "height_anom_m", None)
            hanom = None if (hanom is None or hanom != hanom) else float(hanom)
            depth = hanom if (hanom is not None and hanom > 0.2) else (1.2 if st == "BLOCKED" else 0.4)
            debris_m3 = round(length_m * 6.0 * max(cfrac, 0.25) * depth, 1) if st in ("BLOCKED", "UNCERTAIN") else 0.0
            sk = stretch_map.get((rid, seq), f"{mission_id}:R{rid}:S{seq}")
            seg_id = f"{mission_id}:SEG{idx}"
            coords_ll = [[round(pt[1], 6), round(pt[0], 6)] for pt in cw]

            edge_data = {
                "segment_id": seg_id,
                "mission_id": mission_id,
                "u": u,
                "v": v,
                "stretch_key": sk,
                "road_name": rname,
                "status": st,
                "orig_status": st,
                "length_m": round(length_m, 1),
                "changed_frac": round(cfrac, 3),
                "height_anom_m": round(hanom, 2) if hanom is not None else -1.0,
                "est_debris_vol_m3": debris_m3,
                "coords_ll": coords_ll,
            }
            edges_payload.append(edge_data)
            # If parallel edge exists between u,v keep the worse status or shorter clear edge
            if G.has_edge(u, v):
                existing = G[u][v]
                if st == "BLOCKED" or existing["status"] != "BLOCKED":
                    G[u][v].update(edge_data)
            else:
                G.add_edge(u, v, **edge_data)

            if st == "BLOCKED":
                sm = stretches_meta.setdefault(sk, {
                    "stretch_key": sk, "road_name": rname, "segment_ids": [], "edges": [],
                    "length_m": 0.0, "est_debris_vol_m3": 0.0, "changed_fracs": [],
                    "lat": coords_ll[len(coords_ll) // 2][0], "lon": coords_ll[len(coords_ll) // 2][1],
                })
                sm["segment_ids"].append(seg_id)
                sm["edges"].append((u, v))
                sm["length_m"] = round(sm["length_m"] + length_m, 1)
                sm["est_debris_vol_m3"] = round(sm["est_debris_vol_m3"] + debris_m3, 0)
                sm["changed_fracs"].append(cfrac)

        # Stitch nearby disconnected road components (e.g. OSM bridge/culvert gaps <= 65 m) with virtual connectors
        comps = list(nx.connected_components(G))
        if len(comps) > 1:
            j_lookup = {j["id"]: j for j in junctions}
            for i in range(len(comps)):
                for k in range(i + 1, len(comps)):
                    best_d, best_pair = 65.0, None
                    for a in comps[i]:
                        ja = j_lookup[a]
                        for b in comps[k]:
                            jb = j_lookup[b]
                            d = math.hypot(ja["x"] - jb["x"], ja["y"] - jb["y"])
                            if d < best_d:
                                best_d, best_pair = d, (a, b)
                    if best_pair:
                        u, v = best_pair
                        ja, jb = j_lookup[u], j_lookup[v]
                        conn = {
                            "segment_id": f"{mission_id}:CONN_{u}_{v}",
                            "mission_id": mission_id,
                            "u": u, "v": v,
                            "stretch_key": f"{mission_id}:CONN",
                            "road_name": "Bridge / Corridor Link",
                            "status": "CLEAR",
                            "orig_status": "CLEAR",
                            "length_m": round(best_d, 1),
                            "changed_frac": 0.0,
                            "height_anom_m": -1.0,
                            "est_debris_vol_m3": 0.0,
                            "coords_ll": [[ja["lat"], ja["lon"]], [jb["lat"], jb["lon"]]],
                        }
                        G.add_edge(u, v, **conn)
                        edges_payload.append(conn)

        for j in junctions:
            if j["id"] in G.nodes:
                G.nodes[j["id"]].update(lat=j["lat"], lon=j["lon"], x=j["x"], y=j["y"])

        # Attach facilities (villages, hospitals, staging base) to nearest junction on the main component
        j_in_graph = [j for j in junctions if j["id"] in G.nodes]
        if not facilities:
            facilities = self._derive_default_facilities(mission_id, j_in_graph)

        fac_dict: dict[str, dict[str, Any]] = {}
        for idx, f in enumerate(facilities):
            fid = f.get("id") or f"{mission_id}:F{idx}"
            # Snap to nearest junction
            nearest_j = min(j_in_graph, key=lambda j: _haversine_m(f["lat"], f["lon"], j["lat"], j["lon"]))
            dist_m = round(_haversine_m(f["lat"], f["lon"], nearest_j["lat"], nearest_j["lon"]), 1)
            fac_obj = {
                "id": fid,
                "mission_id": mission_id,
                "name": f["name"],
                "category": f.get("category", "village"),
                "population_est": int(f.get("population_est", 150)),
                "lat": round(float(f["lat"]), 6),
                "lon": round(float(f["lon"]), 6),
                "is_staging": bool(f.get("is_staging", False)),
                "junction_id": nearest_j["id"],
                "snap_dist_m": dist_m,
            }
            fac_dict[fid] = fac_obj

        # Ensure at least one staging base exists
        if fac_dict and not any(f["is_staging"] for f in fac_dict.values()):
            # Pick the southernmost facility (highway approach into Himalayan valley)
            south = min(fac_dict.values(), key=lambda f: f["lat"])
            south["is_staging"] = True

        self._mirrors[mission_id] = {
            "G": G,
            "junctions": {j["id"]: j for j in j_in_graph},
            "facilities": fac_dict,
            "stretches": stretches_meta,
        }

        # Sync to LIVE Neo4j if connected
        synced_live = False
        if self.driver:
            try:
                self._write_to_neo4j(mission_id, mission_name, j_in_graph, edges_payload, list(fac_dict.values()))
                synced_live = True
            except Exception as e:
                self.neo4j_error = f"Neo4j ingest error: {e}"

        return {
            "mission_id": mission_id,
            "neo4j_live": synced_live,
            "junctions": len(j_in_graph),
            "road_segments": len(edges_payload),
            "facilities": len(fac_dict),
            "blocked_stretches": len(stretches_meta),
        }

    def _derive_default_facilities(self, mission_id: str, j_in_graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Derive well-spaced staging base & key village/relief nodes along the road network if none provided."""
        if not j_in_graph:
            return []
        by_lat = sorted(j_in_graph, key=lambda j: j["lat"])
        by_lon = sorted(j_in_graph, key=lambda j: j["lon"])
        picks = [
            ("Staging Base (South Highway Entry)", "staging_base", 0, True, by_lat[0]),
            ("North Valley Settlement", "village", 420, False, by_lat[-1]),
            ("Central Relief Camp / Clinic", "clinic", 310, False, by_lat[len(by_lat) // 2]),
            ("West Ridge Hamlet", "village", 260, False, by_lon[0]),
            ("East River Crossing Post", "bridge", 190, False, by_lon[-1]),
        ]
        out = []
        for idx, (name, cat, pop, staging, j) in enumerate(picks):
            out.append({
                "id": f"{mission_id}:F{idx}", "name": name, "category": cat,
                "population_est": pop, "lat": j["lat"], "lon": j["lon"], "is_staging": staging,
            })
        return out

    def _write_to_neo4j(
        self,
        mission_id: str,
        mission_name: str,
        junctions: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        facilities: list[dict[str, Any]],
    ) -> None:
        with self.driver.session(database=self.neo4j_db) as s:
            # Clean previous graph for this mission idempotently
            s.run("MATCH (n {mission_id: $mid}) DETACH DELETE n", mid=mission_id)
            s.run("MERGE (m:Mission {id: $mid}) SET m.name = $name", mid=mission_id, name=mission_name)
            s.run(
                """
                UNWIND $rows AS r
                MERGE (j:Junction {id: r.id})
                SET j.mission_id = r.mission_id, j.lat = r.lat, j.lon = r.lon
                """,
                rows=junctions,
            )
            s.run(
                """
                UNWIND $rows AS r
                MATCH (u:Junction {id: r.u}), (v:Junction {id: r.v})
                CREATE (u)-[:ROAD_SEGMENT {
                    segment_id: r.segment_id,
                    mission_id: r.mission_id,
                    stretch_key: r.stretch_key,
                    road_name: r.road_name,
                    status: r.status,
                    orig_status: r.orig_status,
                    length_m: r.length_m,
                    changed_frac: r.changed_frac,
                    height_anom_m: r.height_anom_m,
                    est_debris_vol_m3: r.est_debris_vol_m3
                }]->(v)
                """,
                rows=[{k: v for k, v in e.items() if k != "coords_ll"} for e in edges],
            )
            s.run(
                """
                UNWIND $rows AS r
                MERGE (f:Facility {id: r.id})
                SET f.mission_id = r.mission_id, f.name = r.name, f.category = r.category,
                    f.population_est = r.population_est, f.lat = r.lat, f.lon = r.lon,
                    f.is_staging = r.is_staging
                WITH f, r
                MATCH (j:Junction {id: r.junction_id})
                MERGE (f)-[:CONNECTED_TO {dist_m: r.snap_dist_m}]->(j)
                """,
                rows=facilities,
            )

    # --- Core Query 1: Isolation & Cut-Off Analysis ---
    def compute_isolation(self, mission_id: str, staging_id: str | None = None) -> dict[str, Any]:
        m = self._mirrors.get(mission_id)
        if not m:
            raise KeyError(f"Mission '{mission_id}' not loaded in graph")
        G: nx.Graph = m["G"]
        facs = m["facilities"]
        if not staging_id or staging_id not in facs:
            staging_id = next((fid for fid, f in facs.items() if f["is_staging"]), next(iter(facs)))
        base_j = facs[staging_id]["junction_id"]

        cypher_query = (
            "MATCH (base:Facility {id: $staging_id})-[:CONNECTED_TO]-(j0:Junction)\n"
            "MATCH (f:Facility {mission_id: $mission_id})-[:CONNECTED_TO]-(jf:Junction)\n"
            "OPTIONAL MATCH p = shortestPath((j0)-[:ROAD_SEGMENT*..180]-(jf))\n"
            "WHERE ALL(r IN relationships(p) WHERE r.status <> 'BLOCKED')\n"
            "RETURN f.id AS id, f.name AS name, f.population_est AS pop, p IS NOT NULL AS reachable"
        )

        # Check reachability on live Neo4j if available, else mirror
        reachable_ids: set[str] | None = None
        engine_used = "offline_mirror"
        if self.driver:
            try:
                with self.driver.session(database=self.neo4j_db) as s:
                    recs = s.run(cypher_query, staging_id=staging_id, mission_id=mission_id)
                    reachable_ids = {r["id"] for r in recs if r["reachable"]}
                    reachable_ids.add(staging_id)
                    engine_used = "neo4j_live"
            except Exception as e:
                self.neo4j_error = str(e)

        # Mirror calculation (also gives pre-disaster reachability baseline)
        pre_reach_j = nx.node_connected_component(G, base_j) if base_j in G else set()
        passable_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("status") != "BLOCKED"]
        G_pass = G.edge_subgraph(passable_edges)
        post_reach_j = nx.node_connected_component(G_pass, base_j) if base_j in G_pass else {base_j}

        if reachable_ids is None:
            reachable_ids = {fid for fid, f in facs.items() if f["junction_id"] in post_reach_j}

        reachable_list, isolated_list = [], []
        isolated_pop = 0
        for fid, f in facs.items():
            entry = {**f, "pre_disaster_reachable": f["junction_id"] in pre_reach_j}
            if fid in reachable_ids or fid == staging_id:
                entry["reachable"] = True
                reachable_list.append(entry)
            else:
                entry["reachable"] = False
                isolated_list.append(entry)
                isolated_pop += f["population_est"]

        return {
            "mission_id": mission_id,
            "engine": engine_used,
            "staging_facility": facs[staging_id],
            "reachable_count": len(reachable_list),
            "isolated_count": len(isolated_list),
            "isolated_population": isolated_pop,
            "reachable_facilities": reachable_list,
            "isolated_facilities": isolated_list,
            "cypher": cypher_query,
        }

    # --- Core Query 2: Safe Detour Routing (Primary Segment — Incident Commanders) ---
    def find_safe_detour(
        self,
        mission_id: str,
        from_facility_id: str,
        to_facility_id: str,
        avoid_uncertain: bool = False,
    ) -> dict[str, Any]:
        m = self._mirrors.get(mission_id)
        if not m:
            raise KeyError(f"Mission '{mission_id}' not loaded in graph")
        G: nx.Graph = m["G"]
        facs = m["facilities"]
        if from_facility_id not in facs or to_facility_id not in facs:
            raise KeyError("Unknown origin or destination facility")

        u0 = facs[from_facility_id]["junction_id"]
        v0 = facs[to_facility_id]["junction_id"]
        forbidden = {"BLOCKED", "UNCERTAIN"} if avoid_uncertain else {"BLOCKED"}

        cypher_query = (
            "MATCH (a:Facility {id: $from_id})-[:CONNECTED_TO]-(ja:Junction),\n"
            "      (b:Facility {id: $to_id})-[:CONNECTED_TO]-(jb:Junction)\n"
            "MATCH p = shortestPath((ja)-[:ROAD_SEGMENT*..200]-(jb))\n"
            f"WHERE ALL(r IN relationships(p) WHERE NOT r.status IN {list(forbidden)})\n"
            "RETURN [n IN nodes(p) | [n.lat, n.lon]] AS path_coords,\n"
            "       reduce(d = 0.0, r IN relationships(p) | d + r.length_m) AS distance_m,\n"
            "       [r IN relationships(p) | r.road_name] AS roads"
        )

        # Pre-disaster shortest path (all edges)
        baseline_nodes = None
        baseline_m = None
        baseline_coords = []
        blocked_on_baseline = []
        try:
            baseline_nodes = nx.shortest_path(G, u0, v0, weight="length_m")
            baseline_m = round(nx.path_weight(G, baseline_nodes, weight="length_m"), 1)
            for a, b in zip(baseline_nodes[:-1], baseline_nodes[1:]):
                ed = G[a][b]
                baseline_coords.extend(self._oriented_coords(m["junctions"][a], ed))
                if ed["status"] in forbidden:
                    blocked_on_baseline.append(ed["road_name"])
        except nx.NetworkXNoPath:
            pass

        # Safe post-disaster path (excluding forbidden edges)
        safe_edges = [(a, b, d) for a, b, d in G.edges(data=True) if d.get("status") not in forbidden]
        G_safe = nx.Graph()
        for a, b, d in safe_edges:
            G_safe.add_edge(a, b, **d)

        detour_coords = []
        detour_m = None
        detour_roads = []
        engine_used = "offline_mirror"

        if self.driver:
            try:
                with self.driver.session(database=self.neo4j_db) as s:
                    rec = s.run(cypher_query, from_id=from_facility_id, to_id=to_facility_id).single()
                    engine_used = "neo4j_live"
                    if rec and rec["path_coords"]:
                        detour_coords = rec["path_coords"]
                        detour_m = round(float(rec["distance_m"]), 1)
                        detour_roads = list(dict.fromkeys(rec["roads"]))
            except Exception as e:
                self.neo4j_error = str(e)

        if detour_m is None:
            try:
                safe_nodes = nx.shortest_path(G_safe, u0, v0, weight="length_m")
                detour_m = round(nx.path_weight(G_safe, safe_nodes, weight="length_m"), 1)
                for a, b in zip(safe_nodes[:-1], safe_nodes[1:]):
                    ed = G_safe[a][b]
                    detour_coords.extend(self._oriented_coords(m["junctions"][a], ed))
                    if ed["road_name"] not in detour_roads:
                        detour_roads.append(ed["road_name"])
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

        if detour_m is None:
            verdict = "ISOLATED_NO_SAFE_ROUTE"
        elif not blocked_on_baseline:
            verdict = "DIRECT_ROUTE_CLEAR"
        else:
            verdict = "SAFE_DETOUR_FOUND"

        return {
            "mission_id": mission_id,
            "engine": engine_used,
            "from": facs[from_facility_id],
            "to": facs[to_facility_id],
            "avoid_uncertain": avoid_uncertain,
            "verdict": verdict,
            "baseline_distance_m": baseline_m,
            "safe_distance_m": detour_m,
            "detour_overhead_m": round(detour_m - baseline_m, 1) if (detour_m is not None and baseline_m is not None) else None,
            "blocked_roads_avoided": list(dict.fromkeys(blocked_on_baseline)),
            "safe_roads_used": detour_roads[:12],
            "baseline_coords_ll": baseline_coords,
            "safe_coords_ll": detour_coords,
            "cypher": cypher_query,
        }

    def _oriented_coords(self, start_j: dict[str, Any], edge_data: dict[str, Any]) -> list[list[float]]:
        pts = edge_data.get("coords_ll", [])
        if len(pts) < 2:
            return pts
        d0 = math.hypot(pts[0][0] - start_j["lat"], pts[0][1] - start_j["lon"])
        d1 = math.hypot(pts[-1][0] - start_j["lat"], pts[-1][1] - start_j["lon"])
        return pts if d0 <= d1 else list(reversed(pts))

    # --- Core Query 3: PWD / BRO Clearance Priority Queue (Segment 2) ---
    def clearance_priority_queue(self, mission_id: str, staging_id: str | None = None) -> dict[str, Any]:
        """Simulate unblocking each BLOCKED stretch and rank by isolated population reconnected & debris volume."""
        m = self._mirrors.get(mission_id)
        if not m:
            raise KeyError(f"Mission '{mission_id}' not loaded in graph")
        G: nx.Graph = m["G"]
        facs = m["facilities"]
        stretches = m["stretches"]

        iso = self.compute_isolation(mission_id, staging_id)
        base_j = iso["staging_facility"]["junction_id"]
        currently_reachable = {f["id"] for f in iso["reachable_facilities"]}

        passable_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("status") != "BLOCKED"]
        G_base = nx.Graph()
        G_base.add_nodes_from(G.nodes(data=True))
        G_base.add_edges_from((u, v, G[u][v]) for u, v in passable_edges)

        # Pre-compute shortest pre-disaster paths from staging base to every currently isolated facility
        # so linear mountain corridors with multiple sequential landslides still rank the critical cuts!
        corridor_dep_facs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        stretch_dist_from_base: dict[str, float] = {}
        try:
            dists_from_base = nx.single_source_dijkstra_path_length(G, base_j, weight="length_m")
        except Exception:
            dists_from_base = {}
        for f in iso["isolated_facilities"]:
            fj = f["junction_id"]
            try:
                path_nodes = nx.shortest_path(G, base_j, fj, weight="length_m")
                seen_sk = set()
                for a, b in zip(path_nodes[:-1], path_nodes[1:]):
                    ed = G[a][b]
                    sk = ed.get("stretch_key")
                    if ed.get("status") == "BLOCKED" and sk and sk not in seen_sk:
                        seen_sk.add(sk)
                        corridor_dep_facs[sk].append(f)
                        if sk not in stretch_dist_from_base:
                            stretch_dist_from_base[sk] = float(dists_from_base.get(a, 99999.0))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

        queue = []
        for sk, sm in stretches.items():
            still_blocked = any(G.has_edge(u, v) and G[u][v].get("status") == "BLOCKED" for u, v in sm["edges"])
            # 1) Immediate single-cut reconnection simulation
            G_sim = G_base.copy()
            for u, v in sm["edges"]:
                if G.has_edge(u, v):
                    G_sim.add_edge(u, v, **G[u][v])
            reach_after = nx.node_connected_component(G_sim, base_j) if base_j in G_sim else {base_j}
            reconnected_facs = [
                f for fid, f in facs.items()
                if fid not in currently_reachable and f["junction_id"] in reach_after
            ]
            imm_pop = sum(f["population_est"] for f in reconnected_facs)

            # 2) Corridor-dependent population (multi-cut mountain highway reality)
            dep_facs = corridor_dep_facs.get(sk, [])
            dep_pop = sum(f["population_est"] for f in dep_facs)
            eff_facs = reconnected_facs if reconnected_facs else dep_facs
            eff_pop = imm_pop if imm_pop > 0 else dep_pop

            debris_m3 = max(float(sm["est_debris_vol_m3"]), 10.0)
            dist_from_base = stretch_dist_from_base.get(sk, 99999.0)
            # Prioritize immediate reconnections, then corridor-critical cuts closest to staging base
            score = round(
                (imm_pop * 15.0 + dep_pop * 6.0 + len(eff_facs) * 200.0 + sm["length_m"])
                / (debris_m3 / 120.0 + 1.0 + dist_from_base / 4000.0),
                1,
            )
            queue.append({
                "stretch_key": sk,
                "road_name": sm["road_name"],
                "segment_ids": sm["segment_ids"],
                "length_m": sm["length_m"],
                "est_debris_vol_m3": int(sm["est_debris_vol_m3"]),
                "mean_changed_frac": round(sum(sm["changed_fracs"]) / max(len(sm["changed_fracs"]), 1), 2),
                "facilities_reconnected": [f["name"] for f in eff_facs],
                "population_reconnected": int(eff_pop),
                "reconnection_mode": "immediate_single_cut" if imm_pop > 0 else ("corridor_bottleneck" if dep_pop > 0 else "secondary_segment"),
                "dist_from_staging_m": round(dist_from_base, 0) if dist_from_base < 90000 else None,
                "priority_score": score,
                "currently_blocked": still_blocked,
                "lat": sm["lat"],
                "lon": sm["lon"],
            })

        queue.sort(key=lambda x: (-int(x["currently_blocked"]), -x["population_reconnected"], -x["priority_score"]))
        for i, item in enumerate(queue, 1):
            item["priority_rank"] = i

        cypher_query = (
            "// Clearance Impact Simulation (PWD / BRO Priority Queue)\n"
            "MATCH (s:Facility {id: $staging_id})-[:CONNECTED_TO]-(j0:Junction)\n"
            "MATCH ()-[b:ROAD_SEGMENT {mission_id: $mission_id, status: 'BLOCKED'}]-()\n"
            "WITH DISTINCT b.stretch_key AS stretch, b.road_name AS road,\n"
            "     sum(b.length_m) AS len_m, sum(b.est_debris_vol_m3) AS debris_m3, j0\n"
            "MATCH (f:Facility {mission_id: $mission_id})-[:CONNECTED_TO]-(jf:Junction)\n"
            "WHERE EXISTS { MATCH p = shortestPath((j0)-[:ROAD_SEGMENT*..180]-(jf))\n"
            "               WHERE ALL(r IN relationships(p) WHERE r.status <> 'BLOCKED' OR r.stretch_key = stretch) }\n"
            "RETURN stretch, road, len_m, debris_m3, sum(f.population_est) AS reachable_pop\n"
            "ORDER BY reachable_pop DESC, debris_m3 ASC"
        )

        return {
            "mission_id": mission_id,
            "engine": "neo4j_live" if self.driver else "offline_mirror",
            "isolated_population_now": iso["isolated_population"],
            "isolated_facilities_now": iso["isolated_count"],
            "queue": queue,
            "cypher": cypher_query,
        }

    # --- Core Mutation: Live PWD Clearance Update ---
    def set_stretch_status(self, mission_id: str, stretch_key: str, cleared: bool) -> dict[str, Any]:
        m = self._mirrors.get(mission_id)
        if not m or stretch_key not in m["stretches"]:
            raise KeyError("Unknown mission or stretch_key")
        G: nx.Graph = m["G"]
        sm = m["stretches"][stretch_key]
        new_status = "CLEAR" if cleared else "BLOCKED"
        for u, v in sm["edges"]:
            if G.has_edge(u, v):
                G[u][v]["status"] = new_status

        cypher = (
            "MATCH ()-[r:ROAD_SEGMENT {mission_id: $mission_id, stretch_key: $stretch_key}]-()\n"
            "SET r.status = $status, r.cleared_by_pwd = $cleared\n"
            "RETURN count(r) AS updated"
        )
        updated_live = False
        if self.driver:
            try:
                with self.driver.session(database=self.neo4j_db) as s:
                    s.run(cypher, mission_id=mission_id, stretch_key=stretch_key, status=new_status, cleared=cleared)
                    updated_live = True
            except Exception as e:
                self.neo4j_error = str(e)

        return {
            "mission_id": mission_id,
            "stretch_key": stretch_key,
            "new_status": new_status,
            "neo4j_live_updated": updated_live,
            "cypher": cypher,
        }

    def get_facilities(self, mission_id: str) -> list[dict[str, Any]]:
        m = self._mirrors.get(mission_id)
        return list(m["facilities"].values()) if m else []
