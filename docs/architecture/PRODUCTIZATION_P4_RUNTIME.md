# Productization P4 — Always-on runtime foundation

P4 is split. This document describes **P4-A only**.

| Increment | Status |
|-----------|--------|
| P1 | **COMPLETE** |
| P2 | **COMPLETE FOR LOCAL READ-ONLY V1** |
| P3 | **COMPLETE FOR LOCAL AUTHENTICATED V1** |
| P4 | **IN PROGRESS** |
| P4-A | **COMPLETE** |
| P4-B | **IN PROGRESS** |
| P4-B1 | ORIGIN + TRUSTED HOST PRODUCTION CONFIG IMPLEMENTED/TESTED |
| P4-B2 | **COMPLETE** |
| P4-B2-A | THROTTLE DATA/SERVICE FOUNDATION IMPLEMENTED/TESTED |
| P4-B2-B | LOGIN HTTP THROTTLE INTEGRATION IMPLEMENTED/TESTED |
| P4-B3 | SECURITY HEADERS / READINESS / TLS CONTRACT IMPLEMENTED AND TESTED |
| P4-C | **NOT STARTED** |

P4-A is **not** production deployed, internet-ready, TLS-enabled, or cloud-hosted.

## What P4-A is

Local, loopback-only Docker Compose stack:

- `app` — Python 3.12 image, `pip install -e ".[api,auth]"`, one Uvicorn worker
- `postgres` — `postgres:16`, named volume `surplus_ai_pgdata`
- `migrate` — one-shot Alembic upgrade (Compose profile `migrate`)

Host URL remains:

```text
http://127.0.0.1:8000
```

That preserves the P3 Origin allowlist (`http://127.0.0.1:8000`). Binding `0.0.0.0` **inside** the app container is Docker networking only. Host ports are `127.0.0.1:8000` and `127.0.0.1:5432`.

## Container image scope

The P4-A image is scoped to the authenticated web/API runtime plus these approved operator commands:

```text
surplusai db status
surplusai db migrate
surplusai users create
surplusai users reset-password
```

Top-level `config/` YAML is intentionally **not** copied into the P4-A image. Config-dependent parser, classifier, compliance, and research CLI workflows remain host-side and outside P4-A container-runtime scope. Those workflows are **not** removed from the repository.

This does **not** change provider enablement. No live research provider is enabled. No live skip-trace vendor is enabled.

## Local workflow

```text
docker compose build app
docker compose up -d postgres
docker compose --profile migrate run --rm migrate
docker compose up -d app
docker compose ps
docker compose logs app
```

Open `http://127.0.0.1:8000` (anonymous `/` redirects to `/login`).

**Do not** run `docker compose down -v` unless you intend to delete the local PostgreSQL and log volumes.

## First operator user (no default password)

Passwords are still hidden prompts. No `--password` flag. No default admin.

```text
docker compose exec app surplusai users create --name "Operator" --email you@example.invalid --role admin
docker compose exec app surplusai users reset-password --email you@example.invalid
```

Host-side CLI against the same loopback Postgres still works with the existing `.venv` and `.env` (`localhost:5432`).

## Migrations

The long-running `app` command is **only** Uvicorn. It does **not** run Alembic.

Explicit one-shot:

```text
docker compose --profile migrate run --rm migrate
```

That runs existing `surplusai db migrate` (upgrade to head). It is idempotent if already at `e1f4a8c92b03`.

## Restart and logs

`app` and `postgres` use `restart: unless-stopped`. `migrate` does not.

File logs go to named volume `surplus_ai_logs` (`SURPLUS_AI_LOG_DIR=/app/logs`). Stdout remains available via `docker compose logs app`.

## What P4-A does not include

- TLS / reverse proxy / forwarded headers
- Login rate limiting
- Production backups / managed PostgreSQL
- Cloud accounts, domains, public bind
- Workers / schedulers
- Business write HTTP APIs
- CSRF framework for future writes
- Live research providers or skip-trace vendors

P4-B1 adds configurable Origin/Host **settings** only. It does **not** authorize non-loopback bind.

## P4-B1 — Origin and Trusted Host

Environment (comma-separated; not secrets):

```text
SURPLUS_AI_AUTH_ORIGINS=https://app.example.invalid
SURPLUS_AI_ALLOWED_HOSTS=app.example.invalid
```

Local `dev` defaults (no extra Compose env): Origin `http://127.0.0.1:8000`, Host `127.0.0.1`. `test` Host defaults also include `testserver` (Starlette TestClient). `localhost` is not auto-allowed.

`SURPLUS_AI_ENV=prod` requires both variables. Missing/blank/invalid values fail Settings load (`ConfigurationError`) before requests. Production Origins must be canonical `https://host[:non-default-port]` (no path/query/fragment/userinfo/trailing slash). Hosts are exact lowercase hostnames (no scheme, port, or `*`).

Auth POST Origin checks (login/logout only) use exact membership in the Settings allowlist. 403 `origin_not_allowed`. No CORS.

Starlette `TrustedHostMiddleware` (`www_redirect=False`) uses the validated host list. Invalid Host is Starlette's 400. Middleware order: TrustedHost → dashboard security headers → routes.

**P4-B1 does not authorize internet exposure.** Still required: P4-B2-B login HTTP throttling integration, P4-B3 HSTS/readiness/TLS contract, TLS edge, private production DB, real credentials, automatic backups, proxy trust, security review.

## P4-B2-A — Throttle foundation (no HTTP integration)

PostgreSQL table `login_throttle_buckets` stores digest-only keys for two scopes:

- `ip` — login **attempt** gate (20 / 15 min; counts all origin-valid attempts including successes)
- `credential` — failed verification counter (5 / 15 min; IP + exact submitted email)

Service module: `surplus_ai/auth/login_throttle.py`. Caller-owned commits. B2-A does **not** wire the login route; HTTP behavior remains P4-B1 until P4-B2-B.

Logical per-key expiry/reset on use. Stale physical rows may remain until explicit P4-C maintenance if production policy requires physical cleanup.

## P4-B2-B — Login HTTP throttle integration

`POST /api/v1/auth/login` integrates the B2-A throttle service:

1. Origin validation (unchanged; wrong/missing Origin → 403, zero throttle state)
2. Canonical peer IP from `request.client.host` only (no forwarded headers)
3. `consume_ip_login_attempt` — atomic IP attempt gate
4. **COMMIT #1** — releases row locks before expensive password hashing; makes IP budget visible to concurrent requests
5. IP decision — blocked → 429 `login_throttled` with `Retry-After`; skips User lookup and Argon2
6. `precheck_credential_throttle` — read-only credential check; blocked → 429
7. User lookup + Argon2 verification (or dummy Argon2 for unknown users)
8. Invalid credentials → `record_credential_failure` + **COMMIT #2A** + 401
9. Valid credentials → `clear_credential_bucket_on_success` + `create_auth_session` + **COMMIT #2B** + Set-Cookie

IP gate: 20 origin-valid login attempts / 15 min. Credential gate: 5 failures / 15 min per IP+email. Blocks: 15 min. Successful login clears credential bucket only; IP bucket persists (no email-only global lockout). No raw IP/email in throttle table. `unknown-client` sentinel for missing/invalid peer host; no throttle bypass.

Failure states (unknown email, wrong password, inactive user, NULL hash) all record credential failure and return identical 401. DB/commit failures fail closed (500, no cookie, no session).

429 JSON envelope: `{"code": "login_throttled", "message": "Too many login attempts. Try again later."}` with `Retry-After` header (positive integer seconds). No identifying information exposed.

Throttle applies only to `POST /api/v1/auth/login`. No throttle on `/auth/me`, `/auth/logout`, health, status, dashboard, or other routes. No global rate limiter middleware.

**P4-B2-B does not authorize internet exposure.** Still required: P4-B3 HSTS/readiness/TLS contract, TLS edge, private production DB, real credentials, automatic backups, proxy trust, security review.

## P4-B3 — Security headers, readiness, and TLS contract

Browser-facing Holy Grail UI responses (`/`, `GET /login`, `/static/*`) send:

- `Content-Security-Policy` — existing directives preserved, plus `frame-ancestors 'none'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`

No `'unsafe-inline'`, `'unsafe-eval'`, wildcard script origins, or CORS.

**Production HSTS:** when `SURPLUS_AI_ENV=prod`, responses that pass TrustedHost and reach the security-header middleware include `Strict-Transport-Security: max-age=31536000`. Authority is `Settings.env` only — never `request.url.scheme`, `X-Forwarded-Proto`, or `Forwarded`. Dev/test emit no HSTS. No `includeSubDomains` / `preload` yet. No in-app HTTPS redirect middleware.

**`GET /ready`:** unauthenticated database connectivity check (`SELECT 1`). Success `200 {"status":"ready"}`. Failure `503 {"status":"not_ready"}` with no DB URL, credentials, or exception details. Does not check Alembic head, providers, or external services. Distinct from `GET /health` (liveness only; no PostgreSQL).

TrustedHost and Origin validation remain unchanged. Forwarded headers remain untrusted. Login throttling still uses `request.client.host` only. HTTPException header passthrough (Retry-After) is preserved.

### Production TLS / reverse-proxy contract (documentation only)

P4-B3 does **not** terminate public TLS inside the ASGI app and does **not** provision certificates or reverse-proxy infrastructure.

Production deployment must:

- place the app behind a TLS-terminating edge / reverse proxy
- redirect public HTTP → HTTPS at that edge (not inside the app)
- keep the app private behind the edge (not bound for public/LAN exposure from P4-B3)
- rely on production `Settings.env` for app-emitted HSTS and Secure session cookies
- **not** treat request scheme or forwarded proto as HSTS authority

Forwarded headers are **not** trusted in P4-B3. P4-C must configure trusted proxy addresses explicitly and must never blindly trust all forwarded-header sources. TLS certificate/domain provisioning is outside P4-B3. **No public exposure is authorized yet.**

## P4-C (later, separately approved)

Hosting provider/account, managed/private production PostgreSQL, domain, TLS certificate provisioning, TLS reverse proxy / load balancer, HTTP → HTTPS redirect at the edge, explicit trusted-proxy configuration, production secrets, managed DB/network rules, automatic backups, production migrations/bootstrap, deployment/runbook, production operator creation, and physical stale `login_throttle_buckets` row cleanup/maintenance if production policy requires it (B2-A uses logical per-key expiry only; stale digest rows may remain).

Preferred eventual architecture: managed app/container + managed PostgreSQL. Fallback: single VPS + Docker Compose + Caddy.

**P4-B3 does not authorize internet exposure.** Holy Grail remains loopback-only until P4-C is separately completed and reviewed.
