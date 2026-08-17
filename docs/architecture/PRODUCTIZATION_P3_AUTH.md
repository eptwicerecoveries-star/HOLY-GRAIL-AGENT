# Productization P3 — Authentication foundation

Local authentication work is split. **P3-A is the data/password/CLI foundation only.** HTTP login is P3-B and is **not** implemented.

## Status

| Increment | Status |
|-----------|--------|
| P1 | **COMPLETE** |
| P2 | **COMPLETE FOR LOCAL READ-ONLY V1** |
| P3 | **IN PROGRESS** |
| P3-A | authentication data/password foundation implemented locally |
| P3-B | **NOT STARTED** |

This is **not** “authentication complete.”

## P3-A what shipped

- Nullable `users.password_hash` (encoded Argon2 hash only; no plaintext password column)
- Optional extra `auth = ["pwdlib[argon2]>=0.3.1,<0.4"]` using `pwdlib.PasswordHash.recommended()`
- Local Typer commands:
  - `surplusai users create --name ... --email ... --role {admin,manager,agent}`
  - `surplusai users reset-password --email ...`
- Hidden confirmation prompts for passwords (not CLI arguments)
- Length policy: 15–128 Unicode code points; spaces allowed; no composition rules; no truncation/stripping
- Existing users (including `db seed` users) remain valid with `password_hash = NULL` and are **not authenticatable** until a hash is set via CLI
- Login identifier: existing unique `User.email` (exact match as stored; no case-folding in P3-A)

Install the extra before using the users commands:

```text
.\.venv\Scripts\python.exe -m pip install -e ".[dev,api,auth]"
```

Unrelated CLI (`surplusai --help`, `db`, `leads`, …) and the P1/P2 API process do **not** require the auth extra. Auth commands fail with an install message if pwdlib is missing.

## What is still true after P3-A

- Dashboard and API are **STILL anonymous**
- Still bound to **127.0.0.1**
- **NOT internet safe**
- No HTTP login / logout
- No cookies / session table / session secret
- No JWT / OAuth
- No public signup
- No email password reset
- No business write HTTP API
- `Contact.value` still omitted from the API and dashboard
- Research raw payloads / provider responses still omitted

**UNAUTHENTICATED P1/P2 HTTP MUST NOT BE INTERNET-FACING.** P3-A does not change route protection.

## Authorization (unchanged domain metadata)

Existing `UserRole` (`admin`, `manager`, `agent`) and `is_active` are preserved. P3-A does not invent HTTP roles or permissions. Future P3-B gate (not implemented): authenticated user **and** `is_active = true`.

## Explicitly out of P3-A

HTTP authentication, CSRF, sessions, cookies, JWT, OAuth, public registration, email reset, compromised-password blocklists, write APIs, research/skip-trace/outreach/Airtable/agents, cloud deploy.

Password length policy is a local minimum. It is **not** a claim of full NIST compliance. Compromised-password blocklist checking is a future hardening item.

## P3-B remaining scope (not started)

- Server-side session authentication
- HttpOnly cookie (Secure when HTTPS exists; SameSite; restricted Path)
- Login/logout HTTP routes
- Protect dashboard and `/api/v1/*`
- Dashboard 401 / login / logout handling
- CSRF before cookie-authenticated writes
- Rate limiting before any non-local exposure

Even after P3-B, internet exposure still requires TLS, secret management, reverse proxy, production runtime/DB, backups, and a security review. Do not bind to `0.0.0.0` or add wildcard CORS by default.
