"""MAPPA-Neo FastAPI Application: Full-stack offline-first disaster mapping platform with
SQLite persistence, Neo4j spatial road-connectivity graph, RBAC JWT auth, and ArgosX security hardening."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, pipeline, planner
from .db import Database
from .graph_neo4j import MappaGraphService
from .security import (
    COOKIE_NAME,
    SecurityHeadersMiddleware,
    auth_limiter,
    create_access_token,
    get_current_user,
    get_optional_user,
    require_roles,
    safe_resolve_child,
    upload_limiter,
    validate_safe_id,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

STATIC_DIR = ROOT / "mappa" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_RUNS_DIR = ROOT / "data" / "runs"
UPLOAD_RUNS_DIR.mkdir(parents=True, exist_ok=True)

db = Database()
graph_svc = MappaGraphService()

ALLOWED_DOWNLOADS = {
    "brief.pdf",
    "roads_status.gpkg",
    "roads_status.geojson",
    "roads_status.kml",
    "change_polygons.gpkg",
    "change_polygons.geojson",
    "change_polygons.kml",
    "blocked_and_uncertain_stretches.csv",
    "report.json",
    "change_mask.tif",
    "height_anomaly_m.tif",
    "map_preview.png",
}

ALLOWED_OVERLAYS = {"web_post.png", "web_pre.png", "web_change.png", "map_preview.png"}


def _bootstrap_missions() -> None:
    """Register pre-computed real Nepal 2026 flood & synthetic validation missions into SQLite & Neo4j."""
    presets = [
        {
            "id": "nepal_2026",
            "name": "Nepal Floods 2026 — Syabrubesi Corridor (EMSR927)",
            "location": "Syabrubesi (Shyaphru), Rasuwa District, Nepal",
            "event_date": "2026-08-26",
            "simulated": False,
            "output_dir": ROOT / "examples" / "nepal_2026_run" / "output",
        },
        {
            "id": "synthetic_demo",
            "name": "Himalayan Hill-Road Landslide (Synthetic DSM-DEM Benchmark)",
            "location": "Simulated NH-7 / Hill Track Corridor (EPSG:32644)",
            "event_date": "2026-11-21",
            "simulated": True,
            "output_dir": ROOT / "examples" / "demo_run" / "output",
        },
    ]
    for p in presets:
        rep_path = p["output_dir"] / "report.json"
        roads_gj = p["output_dir"] / "roads_status.geojson"
        if not rep_path.exists() or not roads_gj.exists():
            continue
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        fac_path = p["output_dir"] / "facilities.json"
        facs = json.loads(fac_path.read_text(encoding="utf-8")) if fac_path.exists() else None
        ing = graph_svc.ingest_mission(p["id"], p["name"], roads_gj, facilities=facs)
        db.upsert_mission(
            mission_id=p["id"],
            name=p["name"],
            location=p["location"],
            event_date=p["event_date"],
            simulated=p["simulated"],
            output_dir=str(p["output_dir"]),
            bounds_wgs84=rep.get("bounds_wgs84"),
            summary=rep,
            neo4j_synced=ing["neo4j_live"],
            created_by="system",
        )
        # Seed / sync PWD clearance work orders
        pq = graph_svc.clearance_priority_queue(p["id"])
        db.sync_work_orders(p["id"], pq["queue"])
        # Re-apply any previously CLEARED work orders from SQLite onto graph
        for wo in db.list_work_orders(p["id"]):
            if wo["status"] == "CLEARED":
                try:
                    graph_svc.set_stretch_status(p["id"], wo["stretch_key"], cleared=True)
                except KeyError:
                    pass

    # Also reload any user-uploaded custom missions from SQLite
    for m in db.list_missions():
        if m["id"] not in graph_svc._mirrors:
            roads_gj = Path(m["output_dir"]) / "roads_status.geojson"
            if roads_gj.exists():
                graph_svc.ingest_mission(m["id"], m["name"], roads_gj)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_missions()
    yield


app = FastAPI(
    title="MAPPA-Neo — Offline Disaster Mapping & Neo4j Road Connectivity API",
    version=__version__,
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Security middlewares (ArgosX hardened)
app.add_middleware(SecurityHeadersMiddleware)
_allowed_origins = [
    o.strip()
    for o in os.environ.get("MAPPA_CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Pydantic Schemas ---
class LoginRequest(BaseModel):
    email: str = Field(..., min_length=4, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=120)
    full_name: str = Field(..., min_length=2, max_length=100)
    organization: str = Field(..., min_length=2, max_length=120)
    role: str = Field(default="commander")
    password: str = Field(..., min_length=8, max_length=128)


class DetourRequest(BaseModel):
    from_facility_id: str = Field(..., min_length=2, max_length=120)
    to_facility_id: str = Field(..., min_length=2, max_length=120)
    avoid_uncertain: bool = False


class WorkOrderUpdate(BaseModel):
    status: str = Field(..., pattern="^(OPEN|DISPATCHED|CLEARED)$")
    assigned_team: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=500)


class Neo4jConnectRequest(BaseModel):
    uri: str = Field(..., min_length=8, max_length=200)
    username: str = Field(default="neo4j", max_length=80)
    password: str = Field(..., min_length=4, max_length=160)
    database: str = Field(default="neo4j", max_length=64)


class FlightPlanRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    width_m: float = Field(default=600, ge=50, le=5000)
    height_m: float = Field(default=500, ge=50, le=5000)
    alt_m: float = Field(default=90, ge=30, le=200)


# --- Frontend Entry ---
@app.get("/", response_class=HTMLResponse)
def serve_index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>MAPPA-Neo API is running</h1><p>Frontend assets missing.</p>", status_code=200)
    return HTMLResponse(idx.read_text(encoding="utf-8"))


# --- Health ---
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": __version__,
        "offline_capable": True,
        "missions_count": len(db.list_missions()),
        "neo4j": graph_svc.status(),
    }


# --- Authentication ---
@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    auth_limiter.check(f"login:{request.client.host if request.client else 'local'}")
    user = db.get_user_by_email(body.email)
    if not user or not db.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_access_token(user)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=43200,
    )
    db.log_audit(user["email"], user["role"], "LOGIN", "session", "User authenticated")
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "email": user["email"],
            "full_name": user["full_name"],
            "organization": user["organization"],
            "role": user["role"],
        },
    }


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterRequest, request: Request, response: Response):
    auth_limiter.check(f"reg:{request.client.host if request.client else 'local'}")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if db.get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    role = body.role if body.role in ("commander", "engineer") else "commander"
    user = db.create_user(body.email, body.full_name, body.organization, role, body.password)
    token = create_access_token(user)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=43200,
    )
    db.log_audit(user["email"], user["role"], "REGISTER", "users", f"New {role} registered")
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"status": "logged_out"}


@app.get("/api/auth/me")
def me(user: dict[str, Any] | None = Depends(get_optional_user)):
    return {"authenticated": user is not None, "user": user}


# --- Missions ---
@app.get("/api/missions")
def list_missions():
    items = db.list_missions()
    return {"missions": items}


@app.get("/api/missions/{mission_id}")
def get_mission(mission_id: str):
    validate_safe_id(mission_id, "mission_id")
    m = db.get_mission(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found.")
    return m


@app.get("/api/missions/{mission_id}/geojson/{layer}")
def get_mission_geojson(mission_id: str, layer: str):
    validate_safe_id(mission_id, "mission_id")
    m = db.get_mission(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found.")
    out_dir = Path(m["output_dir"])
    layer_map = {
        "roads_status": out_dir / "roads_status.geojson",
        "change_polygons": out_dir / "change_polygons.geojson",
        "copernicus_roads": ROOT / "datasets" / "nepal_2026_syabrubesi" / "copernicus_ems_roads.geojson",
        "copernicus_buildings": ROOT / "datasets" / "nepal_2026_syabrubesi" / "copernicus_ems_buildings.geojson",
    }
    if layer not in layer_map:
        raise HTTPException(status_code=400, detail="Unsupported layer.")
    if layer.startswith("copernicus_") and mission_id != "nepal_2026":
        return JSONResponse({"type": "FeatureCollection", "features": []})
    path = layer_map[layer]
    if not path.exists():
        return JSONResponse({"type": "FeatureCollection", "features": []})

    data = json.loads(path.read_text(encoding="utf-8"))
    # Reflect any live PWD CLEARED overrides in roads_status layer
    if layer == "roads_status" and mission_id in graph_svc._mirrors:
        cleared_keys = {wo["stretch_key"] for wo in db.list_work_orders(mission_id) if wo["status"] == "CLEARED"}
        if cleared_keys:
            G = graph_svc._mirrors[mission_id]["G"]
            cleared_segs = set()
            for _, _, ed in G.edges(data=True):
                if ed.get("stretch_key") in cleared_keys:
                    cleared_segs.add((ed.get("road_name"), ed.get("length_m")))
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                if (props.get("name"), props.get("length_m")) in cleared_segs and props.get("status") == "BLOCKED":
                    props["status"] = "CLEAR"
                    props["cleared_by_pwd"] = True
    return JSONResponse(data)


@app.get("/api/missions/{mission_id}/overlay/{name}")
def get_mission_overlay(mission_id: str, name: str):
    validate_safe_id(mission_id, "mission_id")
    if name not in ALLOWED_OVERLAYS:
        raise HTTPException(status_code=400, detail="Invalid overlay name.")
    m = db.get_mission(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found.")
    target = safe_resolve_child(Path(m["output_dir"]), name)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Overlay not available.")
    return FileResponse(str(target), media_type="image/png")


@app.get("/api/missions/{mission_id}/download/{filename}")
def download_mission_file(mission_id: str, filename: str):
    validate_safe_id(mission_id, "mission_id")
    if filename not in ALLOWED_DOWNLOADS:
        raise HTTPException(status_code=400, detail="File type not permitted for download.")
    m = db.get_mission(mission_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mission not found.")
    target = safe_resolve_child(Path(m["output_dir"]), filename)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Requested export does not exist for this mission.")
    return FileResponse(str(target), filename=f"{mission_id}_{filename}")


@app.post("/api/missions/run", status_code=201)
async def run_custom_mission(
    request: Request,
    name: str = Form(..., min_length=3, max_length=100),
    location: str = Form(default="Field Survey Area", max_length=120),
    grid_res: float = Form(default=1.5),
    base_accuracy: float = Form(default=10.0),
    ortho: UploadFile = File(...),
    base: UploadFile = File(...),
    roads: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_roles("commander", "engineer", "admin")),
):
    upload_limiter.check(f"upload:{user['sub']}")
    if not (0.5 <= grid_res <= 20.0) or not (0.5 <= base_accuracy <= 50.0):
        raise HTTPException(status_code=400, detail="Invalid grid resolution or accuracy parameter.")

    # Validate extensions & size caps (max 25 MB per raster, 5 MB roads)
    for f_obj, exts, max_mb in ((ortho, (".tif", ".tiff"), 25), (base, (".tif", ".tiff"), 25), (roads, (".geojson", ".json", ".gpkg"), 5)):
        ext = Path(f_obj.filename or "").suffix.lower()
        if ext not in exts:
            raise HTTPException(status_code=400, detail=f"Invalid file extension '{ext}' for {f_obj.filename}.")

    mid = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = UPLOAD_RUNS_DIR / mid
    in_dir = run_dir / "input"
    out_dir = run_dir / "output"
    in_dir.mkdir(parents=True, exist_ok=True)

    async def save_upload(up: UploadFile, dst: Path, max_bytes: int):
        data = await up.read()
        if len(data) == 0 or len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"File {up.filename} is empty or exceeds size limit.")
        dst.write_bytes(data)

    ortho_p = in_dir / "ortho.tif"
    base_p = in_dir / "base.tif"
    roads_p = in_dir / f"roads{Path(roads.filename or '.geojson').suffix.lower()}"
    await save_upload(ortho, ortho_p, 25 * 1024 * 1024)
    await save_upload(base, base_p, 25 * 1024 * 1024)
    await save_upload(roads, roads_p, 5 * 1024 * 1024)

    try:
        rep = pipeline.run(
            ortho=str(ortho_p),
            base=str(base_p),
            roads_path=str(roads_p),
            out=str(out_dir),
            grid_res=grid_res,
            base_acc95=base_accuracy,
            simulated=False,
            provenance=f"Uploaded via MAPPA-Neo web app by {user['sub']} ({location}).",
        )
    except SystemExit as se:
        raise HTTPException(status_code=400, detail=str(se))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Pipeline failed to process uploaded files: {type(exc).__name__}")

    ing = graph_svc.ingest_mission(mid, name, out_dir / "roads_status.geojson")
    db.upsert_mission(
        mission_id=mid,
        name=name,
        location=location,
        event_date="2026-11-21",
        simulated=False,
        output_dir=str(out_dir),
        bounds_wgs84=rep.get("bounds_wgs84"),
        summary=rep,
        neo4j_synced=ing["neo4j_live"],
        created_by=user["sub"],
    )
    pq = graph_svc.clearance_priority_queue(mid)
    db.sync_work_orders(mid, pq["queue"])
    db.log_audit(user["sub"], user["role"], "RUN_PIPELINE", mid, f"Processed {rep['area_km2']} km2 in {rep['processing_time_s']['total']} s")
    return {"mission_id": mid, "report": rep, "graph_ingest": ing}


# --- Neo4j Core-Flow Endpoints ---
@app.get("/api/neo4j/status")
def neo4j_status():
    return graph_svc.status()


@app.post("/api/neo4j/connect")
def neo4j_connect(body: Neo4jConnectRequest, user: dict[str, Any] = Depends(require_roles("admin", "commander", "engineer"))):
    if not (body.uri.startswith("neo4j+s://") or body.uri.startswith("bolt://") or body.uri.startswith("neo4j://")):
        raise HTTPException(status_code=400, detail="URI must start with neo4j+s://, neo4j://, or bolt://")
    st = graph_svc.connect(body.uri, body.username, body.password, body.database)
    if st["connected"]:
        # Re-ingest all loaded missions into the newly connected live Neo4j instance
        for m in db.list_missions():
            roads_gj = Path(m["output_dir"]) / "roads_status.geojson"
            fac_p = Path(m["output_dir"]) / "facilities.json"
            facs = json.loads(fac_p.read_text(encoding="utf-8")) if fac_p.exists() else None
            if roads_gj.exists():
                graph_svc.ingest_mission(m["id"], m["name"], roads_gj, facilities=facs)
        db.log_audit(user["sub"], user["role"], "NEO4J_CONNECT", body.uri, "Synced all missions to live Neo4j")
        return graph_svc.status()
    raise HTTPException(status_code=502, detail=f"Could not connect to Neo4j: {st.get('error')}")


@app.get("/api/missions/{mission_id}/graph/isolation")
def mission_isolation(mission_id: str, staging_id: str | None = None):
    validate_safe_id(mission_id, "mission_id")
    try:
        return graph_svc.compute_isolation(mission_id, staging_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/missions/{mission_id}/graph/detour")
def mission_detour(mission_id: str, body: DetourRequest):
    validate_safe_id(mission_id, "mission_id")
    try:
        return graph_svc.find_safe_detour(mission_id, body.from_facility_id, body.to_facility_id, body.avoid_uncertain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/missions/{mission_id}/graph/clearance-queue")
def mission_clearance_queue(mission_id: str, staging_id: str | None = None):
    validate_safe_id(mission_id, "mission_id")
    try:
        pq = graph_svc.clearance_priority_queue(mission_id, staging_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    orders = {wo["stretch_key"]: wo for wo in db.list_work_orders(mission_id)}
    merged = []
    for item in pq["queue"]:
        wo = orders.get(item["stretch_key"])
        merged.append({
            **item,
            "work_order_id": wo["id"] if wo else None,
            "work_order_status": wo["status"] if wo else ("OPEN" if item["currently_blocked"] else "CLEARED"),
            "assigned_team": wo["assigned_team"] if wo else "",
            "notes": wo["notes"] if wo else "",
            "updated_by": wo["updated_by"] if wo else None,
            "updated_at": wo["updated_at"] if wo else None,
        })
    pq["queue"] = merged
    return pq


# --- Segment 2: PWD / BRO Clearance Work Orders ---
@app.patch("/api/missions/{mission_id}/work-orders/{order_id}")
def update_work_order(
    mission_id: str,
    order_id: int,
    body: WorkOrderUpdate,
    user: dict[str, Any] = Depends(require_roles("engineer", "admin")),
):
    validate_safe_id(mission_id, "mission_id")
    updated = db.update_work_order(order_id, body.status, body.assigned_team, body.notes, user["sub"])
    if not updated or updated["mission_id"] != mission_id:
        raise HTTPException(status_code=404, detail="Work order not found.")
    # Live Neo4j graph mutation: CLEARED re-opens the edge; OPEN/DISPATCHED keeps it BLOCKED
    graph_mut = graph_svc.set_stretch_status(mission_id, updated["stretch_key"], cleared=(body.status == "CLEARED"))
    iso_after = graph_svc.compute_isolation(mission_id)
    db.log_audit(
        user["sub"], user["role"], "UPDATE_WORK_ORDER", f"{mission_id}#{order_id}",
        f"{updated['road_name']} -> {body.status} (team: {body.assigned_team or 'unassigned'})",
    )
    return {
        "work_order": updated,
        "graph_mutation": graph_mut,
        "isolation_after": {
            "isolated_count": iso_after["isolated_count"],
            "isolated_population": iso_after["isolated_population"],
            "reachable_count": iso_after["reachable_count"],
        },
    }


@app.get("/api/missions/{mission_id}/work-orders/export.csv")
def export_work_orders_csv(mission_id: str):
    validate_safe_id(mission_id, "mission_id")
    orders = db.list_work_orders(mission_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["priority_rank", "road_name", "status", "length_m", "est_debris_vol_m3", "population_reconnected", "assigned_team", "notes", "updated_by", "updated_at"])
    for o in orders:
        w.writerow([o["priority_rank"], o["road_name"], o["status"], o["length_m"], o["est_debris_vol_m3"],
                    o["population_reconnected"], o["assigned_team"], o["notes"], o["updated_by"] or "", o["updated_at"]])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{mission_id}_pwd_clearance_ledger.csv"'},
    )


# --- Pilot Tools (Flight Planner + Capture Card) ---
@app.post("/api/tools/flight-plan")
def compute_flight_plan(body: FlightPlanRequest):
    stats = planner.plan(body.width_m, body.height_m, alt_m=body.alt_m)
    return {"plan": stats, "center": {"lat": body.lat, "lon": body.lon}}


@app.get("/api/tools/flight-plan.kml")
def download_flight_plan_kml(lat: float, lon: float, width_m: float = 600, height_m: float = 500, alt_m: float = 90):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 50 <= width_m <= 5000 and 50 <= height_m <= 5000 and 30 <= alt_m <= 200):
        raise HTTPException(status_code=400, detail="Flight plan parameters out of safe bounds.")
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        planner.write_mission_kml(lat, lon, width_m, height_m, str(tmp_path), alt_m=alt_m)
        kml_text = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)
    return PlainTextResponse(
        kml_text,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": 'attachment; filename="mappa_mission_grid.kml"'},
    )


@app.get("/api/tools/capture-card.pdf")
def download_capture_card():
    card = ROOT / "capture_card.pdf"
    if not card.exists():
        planner.capture_card(str(card), planner.plan(600, 500, 90))
    return FileResponse(str(card), filename="MAPPA_30s_Capture_Card.pdf", media_type="application/pdf")


@app.get("/api/audit-logs")
def get_audit_logs(user: dict[str, Any] = Depends(get_current_user)):
    return {"logs": db.list_audit_logs(40)}
