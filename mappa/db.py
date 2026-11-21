"""SQLite persistence for MAPPA-Neo: users (RBAC), mapping missions, PWD/BRO clearance work orders, and audit log.
All SQL queries are strictly parameterized to prevent SQL injection (ArgosX security compliance)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "mappa_neo.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._seed_demo_users()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute("PRAGMA journal_mode = WAL;")
        return con

    def _init_schema(self) -> None:
        with self._conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    organization TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('commander', 'engineer', 'admin')),
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    simulated INTEGER NOT NULL DEFAULT 0,
                    output_dir TEXT NOT NULL,
                    bounds_wgs84_json TEXT,
                    summary_json TEXT NOT NULL,
                    neo4j_synced INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    stretch_key TEXT NOT NULL,
                    road_name TEXT NOT NULL,
                    segment_ids_json TEXT NOT NULL,
                    length_m REAL NOT NULL,
                    est_debris_vol_m3 REAL NOT NULL,
                    population_reconnected INTEGER NOT NULL DEFAULT 0,
                    priority_rank INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL CHECK(status IN ('OPEN', 'DISPATCHED', 'CLEARED')) DEFAULT 'OPEN',
                    assigned_team TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    updated_by TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(mission_id, stretch_key),
                    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _seed_demo_users(self) -> None:
        defaults = [
            ("commander@mappa.org", "Maj. Arjun Rawat", "NDRF 8th Battalion (Field Command)", "commander", "MappaCmd#2026"),
            ("engineer@mappa.org", "Er. Kavita Sharma", "PWD / BRO Highway Clearance Division", "engineer", "MappaPwd#2026"),
            ("admin@mappa.org", "Dr. Neeraj Joshi", "DDMA State Emergency Operations Centre", "admin", "MappaAdm#2026"),
        ]
        with self._conn() as con:
            for email, name, org, role, pw in defaults:
                row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                if row is None:
                    pw_hash = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
                    con.execute(
                        "INSERT INTO users(email, full_name, organization, role, password_hash, created_at) VALUES(?,?,?,?,?,?)",
                        (email, name, org, role, pw_hash, _utc_now()),
                    )

    # --- Users ---
    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email.strip(),)).fetchone()
            return dict(row) if row else None

    def create_user(self, email: str, full_name: str, organization: str, role: str, password: str) -> dict[str, Any]:
        if role not in ("commander", "engineer"):
            role = "commander"
        pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        with self._conn() as con:
            con.execute(
                "INSERT INTO users(email, full_name, organization, role, password_hash, created_at) VALUES(?,?,?,?,?,?)",
                (email.strip().lower(), full_name.strip(), organization.strip(), role, pw_hash, _utc_now()),
            )
            row = con.execute("SELECT id, email, full_name, organization, role, created_at FROM users WHERE email = ?",
                              (email.strip().lower(),)).fetchone()
            return dict(row)

    def verify_password(self, plain: str, pw_hash: str) -> bool:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), pw_hash.encode("utf-8"))
        except Exception:
            return False

    # --- Missions ---
    def upsert_mission(self, mission_id: str, name: str, location: str, event_date: str, simulated: bool,
                       output_dir: str, bounds_wgs84: list[float] | None, summary: dict[str, Any],
                       neo4j_synced: bool = False, created_by: str = "system") -> None:
        with self._conn() as con:
            con.execute(
                """
                INSERT INTO missions(id, name, location, event_date, simulated, output_dir, bounds_wgs84_json,
                                     summary_json, neo4j_synced, created_by, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    location=excluded.location,
                    event_date=excluded.event_date,
                    simulated=excluded.simulated,
                    output_dir=excluded.output_dir,
                    bounds_wgs84_json=excluded.bounds_wgs84_json,
                    summary_json=excluded.summary_json,
                    neo4j_synced=excluded.neo4j_synced
                """,
                (
                    mission_id, name, location, event_date, int(simulated), str(output_dir),
                    json.dumps(bounds_wgs84) if bounds_wgs84 else None,
                    json.dumps(summary, default=str), int(neo4j_synced), created_by, _utc_now(),
                ),
            )

    def list_missions(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM missions ORDER BY simulated ASC, created_at DESC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["simulated"] = bool(d["simulated"])
                d["neo4j_synced"] = bool(d["neo4j_synced"])
                d["bounds_wgs84"] = json.loads(d["bounds_wgs84_json"]) if d["bounds_wgs84_json"] else None
                d["summary"] = json.loads(d["summary_json"])
                d.pop("bounds_wgs84_json", None)
                d.pop("summary_json", None)
                out.append(d)
            return out

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            r = con.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["simulated"] = bool(d["simulated"])
            d["neo4j_synced"] = bool(d["neo4j_synced"])
            d["bounds_wgs84"] = json.loads(d["bounds_wgs84_json"]) if d["bounds_wgs84_json"] else None
            d["summary"] = json.loads(d["summary_json"])
            d.pop("bounds_wgs84_json", None)
            d.pop("summary_json", None)
            return d

    # --- Work Orders (Segment 2: PWD / BRO Clearance Dispatch) ---
    def sync_work_orders(self, mission_id: str, queue_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._conn() as con:
            for item in queue_items:
                con.execute(
                    """
                    INSERT INTO work_orders(mission_id, stretch_key, road_name, segment_ids_json, length_m,
                                            est_debris_vol_m3, population_reconnected, priority_rank, status, updated_at)
                    VALUES(?,?,?,?,?,?,?,?, 'OPEN', ?)
                    ON CONFLICT(mission_id, stretch_key) DO UPDATE SET
                        length_m=excluded.length_m,
                        est_debris_vol_m3=excluded.est_debris_vol_m3,
                        population_reconnected=excluded.population_reconnected,
                        priority_rank=excluded.priority_rank
                    """,
                    (
                        mission_id, item["stretch_key"], item["road_name"],
                        json.dumps(item["segment_ids"]), item["length_m"],
                        item["est_debris_vol_m3"], item["population_reconnected"],
                        item["priority_rank"], _utc_now(),
                    ),
                )
        return self.list_work_orders(mission_id)

    def list_work_orders(self, mission_id: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM work_orders WHERE mission_id = ? ORDER BY priority_rank ASC, population_reconnected DESC",
                (mission_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["segment_ids"] = json.loads(d["segment_ids_json"])
                d.pop("segment_ids_json", None)
                out.append(d)
            return out

    def update_work_order(self, order_id: int, status: str, assigned_team: str, notes: str, updated_by: str) -> dict[str, Any] | None:
        if status not in ("OPEN", "DISPATCHED", "CLEARED"):
            raise ValueError("Invalid work order status")
        with self._conn() as con:
            con.execute(
                """
                UPDATE work_orders
                SET status = ?, assigned_team = ?, notes = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, assigned_team[:120], notes[:500], updated_by, _utc_now(), order_id),
            )
            r = con.execute("SELECT * FROM work_orders WHERE id = ?", (order_id,)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["segment_ids"] = json.loads(d["segment_ids_json"])
            d.pop("segment_ids_json", None)
            return d

    # --- Audit Log ---
    def log_audit(self, user_email: str, role: str, action: str, target: str, detail: str = "") -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO audit_logs(user_email, role, action, target, detail, created_at) VALUES(?,?,?,?,?,?)",
                (user_email, role, action, target, detail[:400], _utc_now()),
            )

    def list_audit_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
