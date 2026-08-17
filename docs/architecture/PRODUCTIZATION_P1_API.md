# Productization P1 — Application / API foundation

Local-development **read-only** HTTP shell around the existing Holy Grail engine.

## Security boundary

**UNAUTHENTICATED P1 API MUST NOT BE INTERNET-FACING.**

- Default bind: `127.0.0.1` (never default `0.0.0.0` in P1 docs/commands)
- No authentication/authorization system yet
- No wildcard CORS
- No cloud/LAN deployment in this increment

## Start (local)

```text
.\.venv\Scripts\python.exe -m pip install -e ".[dev,api]"
.\.venv\Scripts\python.exe -m uvicorn surplus_ai.api.app:app --host 127.0.0.1 --port 8000
```

OpenAPI (local only): `http://127.0.0.1:8000/docs`

## Routes (GET only)

| Path | Purpose |
|------|---------|
| `GET /health` | Process alive (no DB) |
| `GET /api/v1/status` | Env/version + DB readiness (no secrets) |
| `GET /api/v1/cases` | Bounded case list |
| `GET /api/v1/cases/{case_id}` | Case detail |
| `GET /api/v1/leads` | Bounded lead list |
| `GET /api/v1/leads/{lead_id}` | Lead detail |
| `GET /api/v1/research/reviews` | Review operational metadata |
| `GET /api/v1/research/reviews/{review_id}` | Review detail (no payloads) |
| `GET /api/v1/contacts` | Contact metadata (no `value`) |
| `GET /api/v1/contacts/{contact_id}` | Contact metadata (no `value`) |

Pagination: `limit` default 50, max 100; `offset` default 0. Order: `created_at DESC`, `id DESC`.

## Explicitly out of scope

Dashboard UI, auth, write APIs, research/skip-trace execution, outreach, Airtable, agents/LLMs, Docker API service, public deploy.
