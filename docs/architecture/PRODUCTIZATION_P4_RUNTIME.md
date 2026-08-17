# Productization P4 — Always-on runtime foundation

P4 is split. This document describes **P4-A only**.

| Increment | Status |
|-----------|--------|
| P1 | **COMPLETE** |
| P2 | **COMPLETE FOR LOCAL READ-ONLY V1** |
| P3 | **COMPLETE FOR LOCAL AUTHENTICATED V1** |
| P4 | **IN PROGRESS** |
| P4-A | LOCAL CONTAINER/RUNTIME FOUNDATION IMPLEMENTED AND TESTED |
| P4-B | **NOT STARTED** |
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

That runs existing `surplusai db migrate` (upgrade to head). It is idempotent if already at `d4c8a1b9e703`.

## Restart and logs

`app` and `postgres` use `restart: unless-stopped`. `migrate` does not.

File logs go to named volume `surplus_ai_logs` (`SURPLUS_AI_LOG_DIR=/app/logs`). Stdout remains available via `docker compose logs app`.

## What P4-A does not include

- TLS / reverse proxy / TrustedHost / forwarded headers
- Configurable production Origin (still `http://127.0.0.1:8000`)
- Login rate limiting
- Production backups / managed PostgreSQL
- Cloud accounts, domains, public bind
- Workers / schedulers
- Business write HTTP APIs
- CSRF framework for future writes
- Live research providers or skip-trace vendors

## P4-B (later)

- Configuration-based production Origin allowlist
- Trusted host / proxy posture
- Login throttling
- TLS deployment contract

Do not expose the app beyond loopback before P4-B is complete and reviewed.

## P4-C (later, separately approved)

Hosting provider/account, managed database, domain/TLS, automatic backups, real deployment.

Preferred eventual architecture: managed app/container + managed PostgreSQL. Fallback: single VPS + Docker Compose + Caddy.
