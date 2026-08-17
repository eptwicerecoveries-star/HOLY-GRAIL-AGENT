# Productization P3 — Authentication foundation

Local authentication spans P3-A (passwords/CLI), P3-B1 (server-side session rows), and P3-B2 (HTTP cookies/login).

## Status

| Increment | Status |
|-----------|--------|
| P1 | **COMPLETE** |
| P2 | **COMPLETE FOR LOCAL READ-ONLY V1** |
| P3 | **COMPLETE FOR LOCAL AUTHENTICATED V1** |
| P3-A | **COMPLETE** — password hash + local user CLI |
| P3-B1 | **COMPLETE** — server-side session model/token service |
| P3-B2 | **LOCAL HTTP SESSION AUTHENTICATION IMPLEMENTED/TESTED** |

Holy Grail local product now has authenticated login, revocable server-side sessions, a protected dashboard, and a protected read API — still **localhost-only**.

This is **not** internet-ready or cloud-deployed.

## P3-A (COMPLETE)

- Nullable `users.password_hash` (Argon2 via optional `pwdlib[argon2]`)
- Local Typer: `surplusai users create` / `surplusai users reset-password`
- Login identifier: exact unique `User.email` (no case-fold)
- Optional extra: `auth = ["pwdlib[argon2]>=0.3.1,<0.4"]` — **not** a core dependency
- P1/P2 API/dashboard foundations do not require `[auth]` to import or start
- P3 HTTP **login** requires `[auth]` for Argon2 verification (pwdlib is imported lazily; missing extra cannot authenticate)

Authenticated local install:

```text
.\.venv\Scripts\python.exe -m pip install -e ".[dev,api,auth]"
.\.venv\Scripts\python.exe -m uvicorn surplus_ai.api.app:app --host 127.0.0.1 --port 8000
```

## P3-B1 (COMPLETE)

- Table `auth_sessions`: digest-only rows; FK CASCADE to `users`
- Service `surplus_ai.auth.sessions` (flush only; caller commits)
- Absolute **12-hour** lifetime; multiple concurrent sessions allowed
- Zero new Python dependencies; no session signing secret

## P3-B2 (this increment)

- Cookie `surplus_ai_session`: raw opaque token only; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200; host-only
- Secure=False for `dev`/`test`; Secure=True for `prod`
- Routes: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`
- Auth POSTs require Origin exactly `http://127.0.0.1:8000` (not CORS; CORS remains disabled)
- Protected: dashboard `GET /` and all business `/api/v1/*` GETs via `require_active_user`
- Public: `/health`, `/static/*`, local `/docs` / `/redoc` / `/openapi.json`
- Login page at `/login`; anonymous `/` → 303 `/login`; dashboard 401 → `/login`
- Generic login failure; unknown-email dummy Argon2 verify for timing
- Reuses P3-A `verify_user_credentials` and P3-B1 session service
- No JWT, OAuth, SessionMiddleware, or `SURPLUS_AI_SESSION_SECRET`
- No business write HTTP API; CSRF token framework **not** implemented (required before any future cookie-authenticated business writes)

## Still true after P3-B2

- Bound to **127.0.0.1**
- **NOT internet safe / NOT cloud deployed**
- No role-based HTTP authorization (only authenticated + `is_active`)
- No public signup / email reset
- `Contact.value` still omitted
- Research raw payloads still omitted
- Business API still GET-only (except auth login/logout)

**SESSION-AUTHENTICATED LOCAL HTTP MUST NOT BE INTERNET-FACING.**

## Remaining before non-local exposure

- HTTPS/TLS
- Secure-cookie production deployment validation
- Reverse proxy / deployment hardening
- Login brute-force / rate limiting
- Production DB/runtime
- Backup/restore strategy
- Secret/config review
- Security review
- CSRF protection before business writes
- Production origin policy

Authentication alone does **not** solve these.
