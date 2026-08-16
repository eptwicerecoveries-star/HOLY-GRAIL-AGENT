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
5. **Incremental delivery:** 6A → 6B → 6C → 6D Socrata/HTTP foundation → one controlled NYC PLUTO live validation (2026-08-15; provider remains disabled) → 6D-ArcGIS-A generic offline FeatureServer foundation (complete/committed) → 6D-ArcGIS-B disabled official Franklin County candidate config (not live-validated; exact `PARCELID` unresolved) → 6D-ArcGIS-C preparation disabled Lake County candidate → one controlled Lake County ArcGIS happy-path live validation (2026-08-16; provider restored disabled) → 6D generic REST JSON offline foundation (`rest_json`; example only; no live REST candidate) → stop. Phase 6D planned provider-family work is **complete pending staging/commit of the generic REST increment**. Phase 6E and Phase 6F are **not started**. Production county activation is a separate human decision.

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

**Not in this slice:** County-specific provider classes, production enablement of any live provider, opt-in live tests, county selection of PLUTO. `nyc_dcp_pluto` remains `verified_for_automated_access: false` with no `access_reviewed_on`. No county points to it. Default remains `manual_lookup`. Commercial-use/attribution judgment is a separate human decision. Generic ArcGIS FeatureServer support is in 6D-ArcGIS-A below. Official Franklin County candidate config is in 6D-ArcGIS-B below and remains disabled. Official Lake County candidate config and the one controlled ArcGIS-C happy-path live validation are in the 6D-ArcGIS-C section below; Lake remains disabled after restoration. Generic REST JSON offline foundation is in the 6D generic REST section below.

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

A county-agnostic ArcGIS FeatureServer adapter is implemented. All ArcGIS tests are offline/mocked. No live ArcGIS HTTP request occurred. At completion of 6D-ArcGIS-A, no official ArcGIS dataset was configured. 6D-ArcGIS-B adds Franklin County Auditor as a disabled official validation candidate. This is not live validation and not production enablement. The Franklin provider is not enabled.

Shipped in this increment:

- Generic `type: arcgis` provider (`ArcGISFeatureServerProvider`) with strict `extra='forbid'` options. Endpoints are constructed from validated `domain` + `service_path` + `layer_id` only. No full URL, no caller `where`, no token field, no MapServer mode.
- Safe FeatureServer path rules: starts with `/`, no trailing slash, contains `/rest/services/`, ends with `/FeatureServer`, narrow segment grammar, no query/fragment/encoding/traversal.
- Identity types `text` and `number` (`ArcGISIdentityValueType`). Text literals use SQL-92 apostrophe doubling. Number identities reuse the existing conservative Decimal grammar (no float). Invalid numbers fail closed before HTTP (`invalid_identity_format`).
- One GET per `lookup()` through `ResearchHttpClient`: `where`, explicit `outFields` (never `*`), `returnGeometry=false`, `f=json`, `resultRecordCount`. No pagination, no `resultOffset`, no application retry.
- Truncation-first incomplete results: if `exceededTransferLimit` is JSON `true` or `len(features) > query_limit`, return `error_code=result_incomplete` (`cacheable=False`, `retryable=False`, `requires_human_review=True`, empty evidence) before local matching. A non-boolean transfer-limit flag is `malformed_provider_response`.
- Geometry, if returned anyway, is ignored. Compact provenance only. Canonical `source_url` is `https://{domain}{service_path}/{layer_id}` with no query string.
- Disabled fake `example_arcgis` (`gis.example.gov`, `verified_for_automated_access: false`, no `access_reviewed_on`). Default remains `manual_lookup`. No county selects ArcGIS.
- Existing Phase 6C review reasons remain sufficient (`automated_access_not_verified` / `http_401` / `http_403` / `unsafe_resolved_address` → unavailable; `result_incomplete` → provider failure).

**Not in this increment:** official ArcGIS candidate configuration, Franklin County, Lake County, Shasta County, credentials/tokens, MapServer fallback, live ArcGIS requests, 6E/6F. Generic REST is a separate 6D increment.

## Phase 6D-ArcGIS-B disabled official Franklin County candidate (config only; not live-validated)

6D-ArcGIS-A is a complete/committed offline FeatureServer foundation. 6D-ArcGIS-B configures Franklin County Auditor as a **disabled** official validation candidate. `franklin_county_oh_auditor_parcels` is present in `config/research/providers.yaml` and remains **disabled** (`verified_for_automated_access: false`; no `access_reviewed_on`; no county pointer). Franklin is **not** the ArcGIS-C live-validation candidate: exact Tax Parcel `PARCELID` representation remains unresolved. The one controlled ArcGIS-C Lake County happy-path live validation is documented in the 6D-ArcGIS-C section below; Lake was restored disabled afterward. Generic REST JSON offline foundation is documented below. Phase 6E and Phase 6F are not started.

This increment does **not** claim legal permission to automate, commercial-use approval, production access, proven anonymous `/query`, or proven token-free operation.

### Official layer metadata

The following field types and lengths were verified from the official Franklin County ArcGIS layer metadata. They are documentation only; they are not stored in `providers.yaml`.

| Field | Esri type | Length |
|-------|-----------|--------|
| `PARCELID` | `esriFieldTypeString` | 11 |
| `OWNERNME1` | `esriFieldTypeString` | 250 |
| `SITEADDRESS` | `esriFieldTypeString` | 70 |
| `OBJECTID` | `esriFieldTypeOID` | (object ID; no string length) |

Layer 0 identity **Tax Parcel**: **VERIFIED**.

Shipped config maps the minimum fields only: `PARCELID` (`parcel_value_type: text`), `OWNERNME1`, `SITEADDRESS`, `OBJECTID`. No mailing fields, no `account_field`, no geometry, no token, no county pointer.

### Parcel-id representation caveat

Official published parcel identifier: `010-016668-00`.

Official source: Franklin County Auditor Weekly Commercial Sales Report dated June 14, 2026.

- It is a real parcel identifier published by the Franklin County Auditor.
- The published representation is hyphenated: `010-016668-00`.
- The ArcGIS `PARCELID` field is `esriFieldTypeString` length 11.
- The published hyphenated string contains 13 characters.
- The exact representation stored in the ArcGIS `PARCELID` field is **NOT VERIFIED**.
- `01001666800` is only a plausible 11-character representation, **not** a verified transformation.
- No automatic de-hyphenation rule has been approved.
- No code performs that rewrite.
- No FeatureServer `/query` has tested either representation.
- ArcGIS-C must separately approve the exact **one** lookup string before any live request.
- If the approved live lookup later returns `NOT_FOUND`, there must be no automatic second representation attempt.
- `010-016668-00` is **not** the definite final ArcGIS-C request value.

### Terms / access classifications

| Item | Classification |
|------|----------------|
| Official government ownership | **VERIFIED** |
| Public catalog/service listing | **VERIFIED** |
| FeatureServer availability | **VERIFIED** |
| Layer 0 Tax Parcel identity | **VERIFIED** |
| Anonymous `/query` access | **NOT VERIFIED** |
| Authentication/token requirement for `/query` | **NOT VERIFIED** |
| Commercial-use permission | **NOT EXPLICITLY ADDRESSED / REQUIRES HUMAN JUDGMENT** |
| Attribution | **REQUIRES HUMAN JUDGMENT** |
| Client request-rate policy | **NOT EXPLICITLY ADDRESSED** |
| Dataset-specific API automation permission | **NOT EXPLICITLY ADDRESSED** |
| Owner-name business/privacy use | **REQUIRES HUMAN JUDGMENT** |
| Explicit prohibition against ordinary low-rate API access | **NONE FOUND IN REVIEWED OFFICIAL SOURCES**, but silence is not permission |

Do **not** claim: legal to automate; commercial use approved; production access approved; anonymous data queries proven; token-free operation proven.

`verified_for_automated_access: false` is an internal operational gate only. It is not legal permission.

### Owner-data boundary

`OWNERNME1` is technically available as published property-record evidence.

That does **not** mean Contact may be created, Lead may be created, outreach is authorized, Compliance is satisfied, skip tracing is authorized, or entitlement is established.

Research remains **evidence only**. Business/privacy use of owner names remains a separate human judgment.

### Static endpoint review (not called)

Statically expected future query endpoint:

`https://gis.franklincountyohio.gov/hosting/rest/services/ParcelFeatures/Parcel_Features/FeatureServer/0/query`

Canonical source URL:

`https://gis.franklincountyohio.gov/hosting/rest/services/ParcelFeatures/Parcel_Features/FeatureServer/0`

- HTTPS
- FeatureServer
- explicit layer 0
- no credential embedded
- canonical source URL contains no query string
- production DNS/SSRF was not run
- `/query` was not called

**REDIRECT BEHAVIOR: NOT VERIFIED**

### Operational gate

- `verified_for_automated_access: false`
- No `access_reviewed_on`
- Conservative reliability override (`ttl_seconds: 0`, `max_attempts: 1`, `backoff_seconds: 0`, `per_second: 1`, `query_limit: 5`)
- Default remains `manual_lookup`
- `example_arcgis` and `nyc_dcp_pluto` remain disabled
- No county selects the Franklin provider
- Offline config tests prove registry listing and that unverified lookup fails before HTTP (`automated_access_not_verified`)

**Not in this increment:** live ArcGIS query, `verified_for_automated_access: true`, county activation, tokens, MapServer, 6E/6F. Generic REST is a separate 6D increment.

## Phase 6D-ArcGIS-C (one controlled Lake County happy-path live validation; provider restored disabled)

Franklin County remains a valid disabled official candidate. It is **not** used for ArcGIS-C live validation because exact Tax Parcel `PARCELID` identity mapping is unresolved.

Lake County, Florida was the selected ArcGIS-C candidate because official Property Appraiser / GIS pages supply substantially stronger `AltKey` identity evidence. `lake_county_fl_pa_tax_parcels` is present in `config/research/providers.yaml`. On **2026-08-16**, one separately human-approved controlled FeatureServer `/query` completed successfully. The provider was immediately restored to **disabled** (`verified_for_automated_access: false`; no `access_reviewed_on`; no token; no county pointer). Default remains `manual_lookup`.

Intended text identity field: `AltKey` (`parcel_value_type: text`). `ParcelNumber` is not selected and is not the lookup identity.

Shipped config maps the minimum fields only: `AltKey`, `OwnerName`, `PropertyAddress`, `OBJECTID`. No mailing fields, no `account_field`, no geometry, no `ParcelNumber`, no token, no county pointer.

### Official layer metadata

Verified from the official Lake County Tax Parcels FeatureServer layer page (documentation only; types/lengths are not stored in `providers.yaml`):

| Field | Esri type | Length / notes |
|-------|-----------|----------------|
| `AltKey` | `esriFieldTypeString` | 7; Display Field: `AltKey` |
| `OwnerName` | `esriFieldTypeString` | 100 |
| `PropertyAddress` | `esriFieldTypeString` | 100 |
| `OBJECTID` | `esriFieldTypeOID` | (object ID; no string length) |

Layer 12 identity **Tax Parcels**: **VERIFIED**. Advertised capabilities include **Query**.

### Live-test identity

- Provider: `lake_county_fl_pa_tax_parcels`
- AltKey: `1213401`
- Selection: lower-PII **corporation/entity** record (not a natural-person test record)
- Official PA page: `https://www.lakecopropappr.com/property-details.aspx?AltKey=1213401`
- Official Map of Property link on that card: `https://gis.lakecountyfl.gov/gisweb/?query=1213401`

Owner-name and address **values** are not documented. Do not use `ParcelNumber` as the lookup identity.

### Controlled live validation result (2026-08-16; safe metadata only)

- Production hardened DNS validation **succeeded** (`hostname=gis.lakecountyfl.gov`, `address_count=4`; resolved IP addresses are not documented)
- TLS/HTTPS through the production network stack **succeeded**
- HTTP status: **200**
- Provider status: **SUCCESS**
- `found`: **true**
- `result_count`: **1**
- Local match mode: **exact_parcel**
- `requires_human_review`: **false**
- `retryable`: **false**
- `error_code`: **none**
- `evidence_count`: **4**
- Evidence field names only: `parcel_id`, `owner_name_on_record`, `current_address`, `property_record_id`
- `arcgis_error_code`: **none**
- `exceededTransferLimit`: **absent / not reported true**
- `cacheable`: **true** (provider permits the successful result to be marked cacheable; configured cache TTL remains **0**; this direct `provider.lookup` did **not** use the Phase 6B pipeline cache; do **not** claim the result will be reused from cache)

### One-request control

Exactly one `provider.lookup` was executed. Exactly one AltKey was used (`1213401`). No second parcel, no fallback identifier, no `ParcelNumber` lookup, no pagination, no `resultOffset`, no MapServer fallback, no token fallback, no alternate host, no syntax rewrite, no retry campaign, no persistence, no `ResearchPipeline`, no county selection.

### Provider restoration

Immediately after the request, `lake_county_fl_pa_tax_parcels` was restored to `verified_for_automated_access: false` and `access_reviewed_on` was removed. Post-restore `git diff -- config/research/providers.yaml` was empty and `git status --short` was empty.

### Terms / access classifications

Official source: Lake County Property Appraiser [Site Notice](https://www.lakecopropappr.com/site-notice.aspx). The system is offered as a public service for review/retrieval of publicly available government information. Users must comply with applicable law. Automated scripts/programs that scrape the site **and** result in blocking or slowing access are described as unauthorized. Property/GIS data is for ad valorem assessment / informational purposes and may not be compatible with other uses. No warranties are provided. Public-record/privacy principles apply.

| Item | Classification |
|------|----------------|
| Official government source | **VERIFIED** |
| Public FeatureServer / GeoHub listing | **VERIFIED** |
| One controlled anonymous `/query` | **NOW TECHNICALLY VERIFIED FOR THIS ONE REQUEST** |
| Token required for this one request | **NO TOKEN WAS REQUIRED FOR THIS ONE SUCCESSFUL REQUEST** |
| One low-rate API validation specifically prohibited | **NOT EXPLICITLY ESTABLISHED** |
| Broad scraping that blocks/slows service | **EXPLICITLY DISALLOWED** |
| Commercial/business use | **REQUIRES HUMAN JUDGMENT** |
| Attribution | **REQUIRES HUMAN JUDGMENT / NOT EXPLICITLY ADDRESSED** as a license condition |
| Owner-name use | **BUSINESS / PRIVACY HUMAN JUDGMENT** |

Do **not** claim: anonymous access always works; token will never be required; legal to automate; commercial use approved; bulk collection approved; ongoing production use approved.

`verified_for_automated_access: false` is an internal operational gate only. It is not legal permission.

### WAF / network

Some earlier cloud-origin metadata fetches were WAF-blocked. This one local controlled production-stack `/query` **succeeded**. That does **not** establish that all networks or future requests will succeed. No WAF bypass, no alternate host/IP, no browser emulation, no token workaround, and **no second live request** occurred.

### What ArcGIS-C validated / did not validate

**Validated (happy path, this one request):** production DNS/SSRF validation; HTTPS/TLS; one FeatureServer layer query; controlled query construction; response-envelope parsing; exact local identity match; minimal evidence mapping; fail-closed config restoration.

**Not validated:** redirects; HTTP 400/401/403/404/429/498/499; WAF rejection handling; malformed JSON; ArcGIS top-level error response; `result_incomplete`; `exceededTransferLimit=true`; response-too-large handling; multiple matches; every parcel; every ArcGIS service; production-scale usage.

Canonical source URL (no query string):

`https://gis.lakecountyfl.gov/lakegis/rest/services/OpenData/OpenData1/FeatureServer/12`

**REDIRECT BEHAVIOR: NOT VERIFIED**

### Operational gate (restored)

- `verified_for_automated_access: false`
- No `access_reviewed_on`
- Conservative reliability override (`ttl_seconds: 0`, `max_attempts: 1`, `backoff_seconds: 0`, `per_second: 1`, `query_limit: 5`)
- Default remains `manual_lookup`
- Franklin, `example_arcgis`, and `nyc_dcp_pluto` remain disabled
- No county selects the Lake provider

**Not authorized by this validation:** ongoing production enablement, county activation, tokens, MapServer, 6E/6F, or another live request. Generic REST is a separate 6D increment.

## Phase 6D generic REST JSON foundation (implemented offline; no live REST candidate)

Generic HTTPS GET JSON adapter (`RestJsonProvider`, registry `type: rest_json`) reuses `ResearchHttpClient` and the existing hardened DNS/SSRF/TLS stack. Config model `RestJsonProviderOptions` is frozen with `extra="forbid"`.

**V1 request shape:** `GET https://{domain}{path}` with configured identity query param only (optional configured `limit_query_param`). One request per lookup. No POST, credentials, arbitrary headers, URL templates, pagination, or caller query dictionaries.

**Response handling:** JSON object or array; optional typed `records_path` key list (max depth 5); enforce `query_limit` before local matching (`result_incomplete` when exceeded); exact local identity match; evidence mapping only; compact provenance (`provider_id`, `source_organization`, `domain`, `path`, `http_status`, `retrieved_at`, `result_count`, `match_mode`, `fields_inspected`, `record_ids`).

**Access gate:** `verified_for_automated_access=false` returns `automated_access_not_verified` before DNS/HTTP. Disabled fake example only: `example_rest_json` (`api.example.gov`). No official generic REST candidate configured or live-tested. No Lake / Franklin / NYC traffic for this increment.

**Not in this increment:** official REST candidate selection, credentials/auth, live REST requests, county pointers, Lead/Contact creation, 6E/6F.

## Later phases / remaining Phase 6D

- 6D Socrata: complete happy-path controlled validation (2026-08-15; provider remains disabled)
- 6D-ArcGIS-A: complete generic offline FeatureServer foundation
- 6D-ArcGIS-B: complete disabled official candidates (Franklin remains unresolved for exact `PARCELID` live-test identity)
- 6D-ArcGIS-C: complete one controlled Lake County happy-path live validation (2026-08-16; Lake restored disabled)
- 6D generic REST JSON: complete offline foundation (`example_rest_json` only; no live REST candidate)
- 6E: local enrichment apply paths (**not started**)
- 6F: skip-trace + Contact materialization when Lead exists (**not started**)

Phase 6D planned provider-family work is **complete pending staging/commit of the generic REST increment**. After that commit, Phase 6D may close; next roadmap step is Phase 6E (not started).
