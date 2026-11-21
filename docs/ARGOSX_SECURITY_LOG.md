# MAPPA-Neo — ArgosX Security Scan & Remediation Log (Challenge 03)

> **Mandatory Submission Gate:** Run Baseline scan on ArgosX before **midnight 21 Nov**, remediate Critical/High findings, run Final scan before **11:00 AM 22 Nov**, and attach both PDF/JSON reports alongside this log.

## Built-In Security Controls Already Implemented in `mappa_neo`
1. **Authentication & RBAC (`mappa/security.py`, `mappa/db.py`):**
   - Passwords hashed with **bcrypt** (12 rounds, unique per-user salt) — never stored in plaintext.
   - Stateless **HS256 JWT** tokens stored in `HttpOnly`, `SameSite=Lax` cookies (`Secure` flag automatically enabled over HTTPS) with 12-hour expiration.
   - Strict Role-Based Access Control (`require_roles("engineer", "admin")`) protecting PWD work-order mutations and Neo4j connection endpoints; verified in `tests/test_web_and_neo4j.py`.
2. **HTTP Security Headers Middleware (`SecurityHeadersMiddleware`):**
   - `Content-Security-Policy` (restricted script/style/img/connect origins, `frame-ancestors 'none'`)
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (on HTTPS)
   - `Cache-Control: no-store` on all `/api/*` endpoints.
3. **Injection & File-Upload Hardening:**
   - **SQL Injection prevention:** 100% parameterized SQLite queries (`?` placeholders) in `mappa/db.py`.
   - **Cypher Injection prevention:** 100% parameterized Neo4j queries (`$mission_id`, `$stretch_key`, `$rows`) in `mappa/graph_neo4j.py`.
   - **XSS prevention:** All dynamic values escaped via `esc()` in `mappa/static/app.js`.
   - **Path Traversal & File Upload Guard:** Allowlisted export filenames (`ALLOWED_DOWNLOADS`), `safe_resolve_child()` canonical path containment check, file-extension allowlist (`.tif`, `.tiff`, `.geojson`, `.gpkg`), and hard 25 MB upload size cap.
   - **Rate Limiting:** Sliding-window per-IP / per-user rate limits on `/api/auth/*` (25 req/min) and `/api/missions/run` (10 req/5 min).
   - **CORS Allowlist:** Explicit `MAPPA_CORS_ORIGINS` allowlist — no wildcard `*` with credentials.

---

## ArgosX Baseline Scan (Run Before 11:59 PM, 21 Nov 2026)
- **Scan Target URL / Repo:** *(paste deployed Render URL or GitHub repo URL)*
- **Timestamp:** *(fill after scan)*
- **Summary of Findings:** *(e.g. X Critical, Y High, Z Medium, W Low)*

| # | Severity | Finding | Remediation Action Taken | Status |
|---|---|---|---|---|
| 1 | | *(record ArgosX baseline finding)* | | |
| 2 | | *(record ArgosX baseline finding)* | | |
| 3 | | *(record ArgosX baseline finding)* | | |

---

## ArgosX Final Scan (Run Before 11:00 AM, 22 Nov 2026)
- **Timestamp:** *(fill after final scan)*
- **Summary of Findings:** *(e.g. 0 Critical, 0 High, ...)*
- **Accepted Residual Risks & Rationale:** *(explain any remaining Low/Info findings, e.g., Leaflet CDN script-src or demo credential notice for hackathon evaluators)*
