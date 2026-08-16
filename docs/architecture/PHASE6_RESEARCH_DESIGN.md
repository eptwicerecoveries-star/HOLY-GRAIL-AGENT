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
5. **Incremental delivery:** 6A → 6B → 6C → 6D Socrata/HTTP foundation → one controlled NYC PLUTO live validation (2026-08-15; provider remains disabled) → 6D-ArcGIS-A generic offline FeatureServer foundation (not live-validated) → stop. Official ArcGIS candidate config is 6D-ArcGIS-B. Generic REST remains after ArcGIS. Production county activation is a separate human decision.

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
- `ttl_seconds <= 0` is always a miss: `ResearchResultCache.get` returns `None` before any
  row or timestamp comparison (`test_ttl_zero_is_always_miss`). `ProviderOutcome.cacheable=True`
  only means the result *may* be reused if TTL is positive; it does not create a hit by itself.
  Direct `provider.lookup` never consults this cache.

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

## Phase 6D Socrata / live-provider foundation (implemented; one controlled live validation completed 2026-08-15)

Socrata adapter, HTTP client, identity typing, and pinned DNS/SSRF enforcement are implemented.
One authorized NYC PLUTO live lookup was performed on 2026-08-15. The provider remains disabled.
This is not production enablement.

Shipped in this slice:

- Sync `ResearchHttpClient` (`httpx` + explicit `httpcore`): HTTPS only, TLS verify on, GET only, redirects off, `trust_env=False` including the transport SSL context, bounded timeouts, User-Agent `SurplusAI-Research/0.1`, streaming 1 MiB cap, no client retries, HTTP/2 off, keepalive off.
- Endpoint/host policy: reject `http://`, `file://`, `ftp://`, `data:`, `javascript:`, localhost, loopback, private, link-local, unspecified, multicast, CGNAT, and other non-global destinations. Destinations come only from validated provider YAML.
- Pinned DNS/SSRF enforcement: each HTTP attempt resolves the configured hostname, fail-closes if any address is unsafe, then TCP-connects only to validated numeric addresses. TLS certificate checks, SNI, and the HTTP Host header remain the original hostname. Automated tests are offline and do not call public DNS. This does not claim to eliminate every conceivable DNS/network attack.
- `SocrataOpenDataProvider` builds `https://{domain}/resource/{dataset_id}.json` from structured config (`extra='forbid'`). No arbitrary request URL. Conservative SoQL identifier validation; runtime values escaped then passed as httpx query params.
- Identity fields may be configured as `parcel_value_type` / `account_value_type` of `text` (default, quoted SoQL string) or `number` (unquoted canonical decimal literal after a conservative non-negative digit grammar). The type is explicit config, never inferred from a live API. Invalid number-mode input fails closed before HTTP (`invalid_identity_format`). Number comparison is offline-tested. No NYC/PLUTO-specific Python branches.
- `verified_for_automated_access` is an internal operational gate. Unverified providers remain listable; selecting them performs no HTTP (`error_code=automated_access_not_verified`). The shipped `example_socrata` and `nyc_dcp_pluto` providers are unverified. Default remains `manual_lookup`. No county YAML selects a live provider.
- YAML stores credential **environment variable names** only. Optional Socrata app token is sent as `X-App-Token` when present; never persisted.
- Matching is exact parcel/account only. Zero rows → `NOT_FOUND`. One exact match → `SUCCESS`. Multiple matches → `SUCCESS` with review required and `cacheable=True`. Owner-of-record text disagreement still keeps the parcel match and requires review. Number-mode local matching uses Decimal equality so JSON strings and JSON numbers can represent the same identity without mutating stored evidence originals.
- Compact provenance only (`schema_version: 1` additive). Canonical `source_url` is the dataset endpoint with no query string.
- Phase 6B order unchanged: cache → limiter → retry wrapper → lookup → persist → 6C enqueue. HTTP client and adapter do not retry.
- Phase 6C enums/table/lifecycle unchanged. Live error codes map onto existing `provider_unavailable` / `provider_failure` reasons (`unsafe_resolved_address` → unavailable; `dns_resolution_failed` → failure, retryable).

**Not in this slice:** ConfigurableREST / generic REST, county-specific provider classes, production enablement of any live provider, opt-in live tests, county selection of PLUTO. `nyc_dcp_pluto` remains `verified_for_automated_access: false` with no `access_reviewed_on`. No county points to it. Default remains `manual_lookup`. Commercial-use/attribution judgment is a separate human decision. Generic ArcGIS FeatureServer support is in 6D-ArcGIS-A below; no official ArcGIS candidate is configured.

### Controlled NYC PLUTO live validation (2026-08-15)

One human-authorized application-level lookup. No second request, no retry campaign, no SODA v3 fallback, no app token, no county pointer, no database persistence. After the request, the temporary activation was restored.

| Item | Value |
|------|--------|
| Date | 2026-08-15 |
| Provider | `nyc_dcp_pluto` |
| Dataset | `64uk-42ks` |
| Test parcel | `4142600080` |
| HTTP status | 200 |
| Outcome status | `success` |
| found | true |
| result_count | 1 |
| match_mode | `exact_parcel` |
| evidence fields | `parcel_id`, `owner_name_on_record`, `current_address` |

Owner-name and address *values* were not recorded. The BBL was not added to active provider configuration.

**Narrow meaning of “live validated”:** NYC PLUTO `64uk-42ks` accepted one SODA 2.x `/resource` request; unauthenticated access succeeded for that request; BBL `4142600080` returned one exact numeric match; expected minimum fields were usable; production DNS/SSRF/TLS protections permitted the request; adapter mapping produced the expected evidence fields.

**Do not claim:** every PLUTO parcel works; all NYC failure behaviors are verified; redirects were tested; 429 was tested; authentication will never be required; commercial/legal use is settled; the provider is production-enabled; any county is using PLUTO; Phase 6D as a whole is complete.

`cacheable=True` on that outcome means the adapter permits caching. Shipped `ttl_seconds: 0` still forces Phase 6B cache GET to miss. Direct `provider.lookup` bypassed the cache. No cache code change is required for this result.

No additional live request is authorized.

## Phase 6D-ArcGIS-A generic offline FeatureServer foundation (implemented; not live-validated)

A county-agnostic ArcGIS FeatureServer adapter is implemented. All ArcGIS tests are offline/mocked. No live ArcGIS HTTP request occurred. No official ArcGIS dataset is configured. This is not live validation and not production enablement.

Shipped in this increment:

- Generic `type: arcgis` provider (`ArcGISFeatureServerProvider`) with strict `extra='forbid'` options. Endpoints are constructed from validated `domain` + `service_path` + `layer_id` only. No full URL, no caller `where`, no token field, no MapServer mode.
- Safe FeatureServer path rules: starts with `/`, no trailing slash, contains `/rest/services/`, ends with `/FeatureServer`, narrow segment grammar, no query/fragment/encoding/traversal.
- Identity types `text` and `number` (`ArcGISIdentityValueType`). Text literals use SQL-92 apostrophe doubling. Number identities reuse the existing conservative Decimal grammar (no float). Invalid numbers fail closed before HTTP (`invalid_identity_format`).
- One GET per `lookup()` through `ResearchHttpClient`: `where`, explicit `outFields` (never `*`), `returnGeometry=false`, `f=json`, `resultRecordCount`. No pagination, no `resultOffset`, no application retry.
- Truncation-first incomplete results: if `exceededTransferLimit` is JSON `true` or `len(features) > query_limit`, return `error_code=result_incomplete` (`cacheable=False`, `retryable=False`, `requires_human_review=True`, empty evidence) before local matching. A non-boolean transfer-limit flag is `malformed_provider_response`.
- Geometry, if returned anyway, is ignored. Compact provenance only. Canonical `source_url` is `https://{domain}{service_path}/{layer_id}` with no query string.
- Disabled fake `example_arcgis` (`gis.example.gov`, `verified_for_automated_access: false`, no `access_reviewed_on`). Default remains `manual_lookup`. No county selects ArcGIS.
- Existing Phase 6C review reasons remain sufficient (`automated_access_not_verified` / `http_401` / `http_403` / `unsafe_resolved_address` → unavailable; `result_incomplete` → provider failure).

**Not in this increment:** official ArcGIS candidate configuration, Franklin County, Lake County, Shasta County, credentials/tokens, MapServer fallback, generic REST, live ArcGIS requests, 6E/6F.

Franklin County remains a candidate for **6D-ArcGIS-B** only.

## Later phases (not started)

- 6D-ArcGIS-B: official ArcGIS candidate config + terms review (no live query until separately authorized)
- 6D later increment: generic REST
- 6E: local enrichment apply paths
- 6F: skip-trace + Contact materialization when Lead exists
