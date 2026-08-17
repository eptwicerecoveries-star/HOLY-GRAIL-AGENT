# Productization P3 — Authentication foundation

Local authentication is split across P3-A (passwords/CLI), P3-B1 (server-side session rows), and P3-B2 (HTTP cookies/login).

## Status

| Increment | Status |
|-----------|--------|
| P1 | **COMPLETE** |
| P2 | **COMPLETE FOR LOCAL READ-ONLY V1** |
| P3 | **IN PROGRESS** |
| P3-A | **COMPLETE** — password hash + local user CLI |
| P3-B1 | server-side session model/token service implemented and tested |
| P3-B2 | **NOT STARTED** |

This is **not** “authentication complete.”

## P3-A (COMPLETE)

- Nullable `users.password_hash` (Argon2 via optional `pwdlib[argon2]`)
- Local Typer: `surplusai users create` / `surplusai users reset-password`
- Login identifier: exact unique `User.email`
- Optional extra: `auth = ["pwdlib[argon2]>=0.3.1,<0.4"]` (not required for P1/P2 or P3-B1 session primitives)

## P3-B1 (this increment)

- Table `auth_sessions`: `id`, `user_id` (FK → `users.id` ON DELETE CASCADE), `token_digest` (unique SHA-256 hex), `created_at`, `expires_at`
- Service `surplus_ai.auth.sessions`: `generate_session_token` (`secrets.token_urlsafe(32)`), `digest_session_token`, `create_auth_session`, `resolve_auth_session`, `revoke_auth_session`
- Raw bearer token is ephemeral only; PostgreSQL stores the digest
- Absolute lifetime: **12 hours**; no idle timeout; expired rows may remain
- Multiple concurrent sessions per user allowed
- Caller owns DB transactions (flush only; no service commit)
- **Zero new Python dependencies**; no session signing secret
- Auth sessions are **not** written to `AuditLog`; no automatic model snapshot includes `token_digest`

## What is still true after P3-B1

- Dashboard and API are **STILL anonymous**
- No HTTP login / logout / `/auth/me`
- No cookie issuance or parsing
- No route protection
- No Origin / CSRF implementation yet
- No JWT / OAuth
- Still bound to **127.0.0.1**
- **NOT internet safe**
- No business write HTTP API
- `Contact.value` still omitted
- Research raw payloads still omitted

**UNAUTHENTICATED P1/P2 HTTP MUST NOT BE INTERNET-FACING.**

## P3-B2 remaining scope (not started)

- HttpOnly cookie (`surplus_ai_session`)
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`
- Protect dashboard and `/api/v1/*` (except health/login)
- Login page + dashboard 401/logout handling
- Origin allowlist for auth POSTs
- CSRF before future cookie-authenticated business writes
- Rate limiting before any non-local exposure

Even after P3-B2, internet exposure still requires TLS, Secure cookies, reverse proxy, production runtime/DB, backups, and a security review.
