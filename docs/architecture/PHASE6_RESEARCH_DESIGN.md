# Phase 6: Research & Enrichment

**Implementation numbering:** Phase 6 (see `CURRENT_STATUS.md`).

**ARCHITECTURE.md** roadmap label for this work is “Phase 4 — Research & Enrichment.”

## Decisions locked for this repo

1. **Contacts (Option A):** Candidate contact facts may appear inside `ResearchResult`
   evidence before a Lead exists. `Contact` rows are materialized only when a Lead exists
   (not in Phase 6A). Discovery of a phone/email alone does not create a Contact.
2. **Human verification:** Confidence and review flags never declare legal entitlement or
   lawful contactability. Ambiguous identity matches require human review.
3. **Providers:** Abstract property providers + `ProviderRegistry`. County variance is
   configuration-only. Prefer official APIs / open data / Socrata / ArcGIS / documented REST.
   If none exist, use `ManualLookupProvider`. No scraping that bypasses auth, CAPTCHA, or
   access controls.
4. **Compliance boundary:** Research never overrides `ComplianceEvaluation`, creates or
   promotes leads, invents statutes, or decides entitlement/contact eligibility.
5. **Incremental delivery:** 6A → 6B → 6C → 6D Socrata/HTTP foundation → stop → approve a real live dataset separately. ArcGIS and generic REST are later 6D increments.

## Phase 6A (implemented)

ResearchResult-only skeleton:

- Package `surplus_ai/research/` with DTOs, provenance, confidence helpers, providers
  (`manual`, `null`, credentials-missing gate), registry, candidate selection, pipeline,
  append-only persistence into existing `research_results`.
- CLI: `surplusai research providers|case|pending|status`
- Config: `config/research/providers.yaml`
- **No** Property updates, **no** `surplus_cases.status` changes, **no** Contacts/Leads,
  **no** compliance writes, **no** migrations, **no** live HTTP providers or new network deps.

### Versioned JSON contract (`schema_version: 1`)

Stored in `research_results.request_payload` / `response_payload`. Evidence atoms carry
original value, normalized value, source, source URL, retrieval timestamp, confidence,
method, and human-verification requirement.

### Status mapping

| ProviderOutcomeStatus | ResearchStatus (DB enum) |
|----------------------|---------------------------|
| success | success |
| not_found | not_found |
| skipped | not_found |
| error / rate_limited / timeout | error |

Missing provider configuration and missing credentials are never stored as `success`.

## Phase 6B (implemented)

Cache + in-process rate limiting + bounded transient retries. Still **no** live HTTP
providers, **no** migrations, **no** new ORM models, **no** Contact/Lead/Property/Compliance
writes, and **no** change to `CandidateSelector.pending()`.

### Reliability config (`config/research/providers.yaml`)

Shipped policy:

```yaml
reliability:
  cache:
    ttl_seconds: 86400
  retry:
    max_attempts: 3
    backoff_seconds: 0.5
  rate_limit:
    per_second: 0
```

Omitted `reliability` on a custom file keeps conservative defaults: TTL 0, max_attempts 1,
nonnegative backoff default 0, per_second 0 (unlimited). Per-provider `reliability` patches
inherit omitted nested fields from the global policy. Unknown keys and malformed types fail
closed (`extra='forbid'`; integers/numbers are not coerced from strings or bools).

### Cache (read-only over existing `ResearchResult` rows)

- Identity: surplus case + provider + request `cache_key`.
- Hit only when `response_payload.cacheable` is JSON `true` and the row is SUCCESS or
  NOT_FOUND with `provider_status` success/not_found.
- Phase 6A rows missing `cacheable` are misses. 6B writes `cacheable` explicitly true or false.
- `ResponsePayload.cacheable` defaults to `None` (not `True`).
- Ordering: `fetched_at DESC`, `id DESC`. Hits return the existing row; no update, no new
  row, no `fetched_at` refresh.
- `--no-cache` bypasses reads only; the provider is still called and a new row is still
  persisted with the normal `cacheable` value.

### Local rate limiter (throttling, not evidence)

Process-local capacity-1 token bucket, initialized with one token. `per_second: 0` is
unlimited. When a token is unavailable, wait via the injected monotonic clock + sleeper.
Local pacing never fabricates or persists RATE_LIMITED / ERROR / SUCCESS. A RATE_LIMITED
outcome **returned by a provider** remains distinct evidence and follows the retry policy.

### Retries

`max_attempts` includes the initial try. Retry only TIMEOUT, provider-returned RATE_LIMITED,
or ERROR with `ProviderOutcome.retryable is True` (default False). Injected sleeper; unit
tests must not really sleep.

## Phase 6C (implemented)

Human research review queue (`research_review_items`). Evidence/workflow only.

- Enqueue only after a **new** persisted `ResearchResult` whose
  `response_payload.requires_human_review` is exactly JSON `true`.
- Cache hits, limiter waits, and unflagged results do not enqueue.
- Open-item identity: `(surplus_case_id, provider, reason)` while `status = pending`.
- Triggering `research_result_id` is frozen. `review show` warns if a newer result exists
  for the same case+provider; it does not retarget or auto-reopen.
- Reuse `review_status`: pending → resolved | rejected. No `IN_REVIEW`.
- Resolutions: `evidence_usable`, `evidence_insufficient`, `needs_additional_research`,
  `conflict_unresolved`, `not_relevant`. `evidence_usable` is not legal entitlement.
- Close is an atomic pending-only UPDATE. A second close fails; it does not overwrite.
- Duplicate enqueue races use a SQLAlchemy savepoint (`begin_nested`) so the outer
  ResearchResult persist is not rolled back.
- No backfill of 6A/6B rows. No Contact/Lead/Property/Compliance writes. No `AuditLog`.
- CLI: `surplusai research review list|show|resolve`

## Phase 6D Socrata / live-provider foundation (implemented; live validation pending)

Offline foundation only. **No real government or open-data endpoint has been called or verified.**

Shipped in this slice:

- Sync `ResearchHttpClient` (`httpx`): HTTPS only, TLS verify on, GET only, redirects off, `trust_env=False`, bounded timeouts, User-Agent `SurplusAI-Research/0.1`, streaming 1 MiB cap, no client retries.
- Endpoint/host policy: reject `http://`, `file://`, `ftp://`, `data:`, `javascript:`, localhost, loopback, private, link-local, and unspecified addresses. Destinations come only from validated provider YAML. Hostname checks are string-level before connect; they do **not** pin the resolved IP. DNS rebinding / private resolution after validation is a **pre-live blocker** — do not enable a real Socrata provider until resolved-IP enforcement (or equivalent) is implemented and reviewed.
- `SocrataOpenDataProvider` builds `https://{domain}/resource/{dataset_id}.json` from structured config (`extra='forbid'`). No arbitrary request URL. Conservative SoQL identifier validation; runtime values escaped then passed as httpx query params.
- `verified_for_automated_access` is an internal operational gate. Unverified providers remain listable; selecting them performs no HTTP (`error_code=automated_access_not_verified`). The shipped `example_socrata` provider is unverified. Default remains `manual_lookup`. No county YAML selects a live provider.
- YAML stores credential **environment variable names** only. Optional Socrata app token is sent as `X-App-Token` when present; never persisted.
- Matching is exact parcel/account only. Zero rows → `NOT_FOUND`. One exact match → `SUCCESS`. Multiple matches → `SUCCESS` with review required and `cacheable=True`. Owner-of-record text disagreement still keeps the parcel match and requires review.
- Compact provenance only (`schema_version: 1` additive). Canonical `source_url` is the dataset endpoint with no query string.
- Phase 6B order unchanged: cache → limiter → retry wrapper → lookup → persist → 6C enqueue. HTTP client and adapter do not retry.
- Phase 6C enums/table/lifecycle unchanged. Live error codes map onto existing `provider_unavailable` / `provider_failure` reasons.

**Not in this slice:** ArcGIS, ConfigurableREST / generic REST, county-specific provider classes, a verified live dataset, opt-in live tests.

## Later phases (not started)

- 6D later increments: ArcGIS, generic REST, approved live dataset validation
- 6E: local enrichment apply paths
- 6F: skip-trace + Contact materialization when Lead exists
