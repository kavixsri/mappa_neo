"""Authentication (JWT + bcrypt), RBAC, rate limiting, safe file handling, and ArgosX security headers."""
from __future__ import annotations

import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

# Stable per-install secret (override via MAPPA_JWT_SECRET env var in production)
_SECRET_FILE = Path(__file__).resolve().parents[1] / "data" / ".jwt_secret"


def _load_secret() -> str:
    env = os.environ.get("MAPPA_JWT_SECRET")
    if env and len(env) >= 32:
        return env
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text(encoding="utf-8").strip()
    val = secrets.token_urlsafe(48)
    _SECRET_FILE.write_text(val, encoding="utf-8")
    return val


JWT_SECRET = _load_secret()
JWT_ALG = "HS256"
JWT_TTL_HOURS = 12
COOKIE_NAME = "mappa_session"

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(user: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["email"],
        "name": user["full_name"],
        "org": user["organization"],
        "role": user["role"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    token = None
    if creds and creds.credentials:
        token = creds.credentials
    elif COOKIE_NAME in request.cookies:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return decode_token(token)


def get_optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any] | None:
    token = None
    if creds and creds.credentials:
        token = creds.credentials
    elif COOKIE_NAME in request.cookies:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


def require_roles(*roles: str):
    def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if user.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.get('role')}' does not have permission for this action (requires {', '.join(roles)}).",
            )
        return user
    return _dep


# --- Rate Limiter (in-memory sliding window) ---
class RateLimiter:
    def __init__(self, max_requests: int = 30, window_sec: int = 60):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_sec
        arr = [t for t in self._hits[key] if t > cutoff]
        if len(arr) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Too many requests. Please retry shortly.")
        arr.append(now)
        self._hits[key] = arr


auth_limiter = RateLimiter(max_requests=25, window_sec=60)
upload_limiter = RateLimiter(max_requests=10, window_sec=300)


# --- Safe Filename / Path Sanitization ---
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def validate_safe_id(val: str, label: str = "id") -> str:
    if not SAFE_ID_RE.match(val):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: only alphanumeric, dash, underscore allowed.")
    return val


def safe_resolve_child(base_dir: Path, filename: str) -> Path:
    """Prevent directory traversal (../) when serving mission export files."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    base_resolved = base_dir.resolve()
    target = (base_resolved / filename).resolve()
    if base_resolved not in target.parents:
        raise HTTPException(status_code=400, detail="Path outside allowed export directory.")
    return target


# --- ArgosX-Hardened HTTP Security Headers Middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' https://unpkg.com https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://server.arcgisonline.com https://*.basemaps.cartocdn.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
