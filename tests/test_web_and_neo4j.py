"""End-to-end tests for MAPPA-Neo FastAPI backend, RBAC auth, ArgosX security headers,
real 2026 Nepal flood mission, Neo4j graph routing, and PWD clearance work orders."""
from fastapi.testclient import TestClient

from mappa.web_app import app


def test_health_and_security_headers():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["offline_capable"] is True
        assert body["missions_count"] >= 2
        # Verify ArgosX-required security headers
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in r.headers


def test_real_nepal_mission_and_copernicus_validation():
    with TestClient(app) as client:
        r = client.get("/api/missions/nepal_2026")
        assert r.status_code == 200
        m = r.json()
        assert m["simulated"] is False
        rep = m["summary"]
        assert rep["area_km2"] > 4.0
        assert rep["copernicus_validation"]["available"] is True
        assert rep["copernicus_validation"]["matched_damaged_road_m"] > 200

        # GeoJSON + PDF export downloads work & path traversal is blocked
        assert client.get("/api/missions/nepal_2026/geojson/roads_status").status_code == 200
        assert client.get("/api/missions/nepal_2026/download/brief.pdf").status_code == 200
        assert client.get("/api/missions/nepal_2026/download/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)


def test_neo4j_graph_isolation_detour_and_pwd_clearance_flow():
    with TestClient(app) as client:
        # 1. Isolation analysis on Real Nepal 2026
        iso = client.get("/api/missions/nepal_2026/graph/isolation").json()
        assert iso["isolated_count"] >= 1
        assert "shortestPath" in iso["cypher"]

        # 2. Safe detour routing (Primary Segment)
        staging = iso["staging_facility"]["id"]
        target = iso["isolated_facilities"][0]["id"]
        det = client.post(
            "/api/missions/nepal_2026/graph/detour",
            json={"from_facility_id": staging, "to_facility_id": target, "avoid_uncertain": False},
        )
        assert det.status_code == 200
        assert det.json()["verdict"] in ("ISOLATED_NO_SAFE_ROUTE", "SAFE_DETOUR_FOUND", "DIRECT_ROUTE_CLEAR")

        # 3. Clearance priority queue (Segment 2 — PWD / BRO)
        pq = client.get("/api/missions/nepal_2026/graph/clearance-queue").json()
        assert len(pq["queue"]) >= 1
        wo_id = pq["queue"][0]["work_order_id"]

        # 4. RBAC check: unauthenticated -> 401; commander -> 403; engineer -> 200
        r_unauth = client.patch(
            f"/api/missions/nepal_2026/work-orders/{wo_id}",
            json={"status": "DISPATCHED", "assigned_team": "BRO Unit 1", "notes": "test"},
        )
        assert r_unauth.status_code == 401

        cmd_login = client.post("/api/auth/login", json={"email": "commander@mappa.org", "password": "MappaCmd#2026"})
        assert cmd_login.status_code == 200
        r_cmd = client.patch(
            f"/api/missions/nepal_2026/work-orders/{wo_id}",
            json={"status": "DISPATCHED", "assigned_team": "BRO Unit 1", "notes": "test"},
        )
        assert r_cmd.status_code == 403

        eng_login = client.post("/api/auth/login", json={"email": "engineer@mappa.org", "password": "MappaPwd#2026"})
        assert eng_login.status_code == 200
        r_eng = client.patch(
            f"/api/missions/nepal_2026/work-orders/{wo_id}",
            json={"status": "DISPATCHED", "assigned_team": "BRO Unit 04", "notes": "Excavator dispatched"},
        )
        assert r_eng.status_code == 200
        assert r_eng.json()["work_order"]["status"] == "DISPATCHED"

        # Reset back to OPEN for clean demo state
        client.patch(
            f"/api/missions/nepal_2026/work-orders/{wo_id}",
            json={"status": "OPEN", "assigned_team": "", "notes": ""},
        )


def test_pilot_tools_and_kml():
    with TestClient(app) as client:
        r = client.post("/api/tools/flight-plan", json={"lat": 28.1622, "lon": 85.3345, "width_m": 600, "height_m": 500, "alt_m": 90})
        assert r.status_code == 200
        assert r.json()["plan"]["n_images"] > 100
        kml = client.get("/api/tools/flight-plan.kml?lat=28.1622&lon=85.3345&width_m=600&height_m=500&alt_m=90")
        assert kml.status_code == 200
        assert "<kml" in kml.text
        pdf = client.get("/api/tools/capture-card.pdf")
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
