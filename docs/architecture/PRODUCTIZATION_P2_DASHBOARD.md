# Productization P2 — Local Holy Grail browser dashboard

Local-development **read-only** operator dashboard served by the existing FastAPI app.

## Security boundary

**UNAUTHENTICATED P2 DASHBOARD MUST NOT BE INTERNET-FACING.**

- Default bind: `127.0.0.1` (never `0.0.0.0`)
- No authentication
- No wildcard CORS
- No CDN / third-party scripts
- No Node, npm, React, Vue, Svelte, Vite, Tailwind, Bootstrap, Jinja, or HTMX

## Start (local)

```text
.\.venv\Scripts\python.exe -m uvicorn surplus_ai.api.app:app --host 127.0.0.1 --port 8000
```

Then open: `http://127.0.0.1:8000/`

P1 API remains at `/api/v1/`. OpenAPI remains at `/docs`.

## What it shows

- Overview: system status from `GET /api/v1/status` plus first-page row counts (explicitly **not** database totals)
- Cases / Leads / Research reviews / Contacts: P1 list + detail fields only
- Contacts: metadata only — **no** `Contact.value` (phone/email/address)

## Pagination

`limit=50` (never above 100), `offset` via Previous/Next. Next disables when a page returns fewer than 50 rows. No invented total page count.

## Explicitly out of scope

Write actions, research/skip-trace execution, outreach, Airtable, auth, agents/LLMs, cloud deploy.
