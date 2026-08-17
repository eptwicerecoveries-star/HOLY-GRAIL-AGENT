# CURRENT_STATUS

PROJECT: SurplusAI (Holy Grail Agent)

SOURCE OF TRUTH: This GitHub repository (code, tests, migrations, and design notes under `docs/`).

AI DEVELOPMENT RULE: Read `PROJECT.md`, `ARCHITECTURE.md`, `AI_DEVELOPER_GUIDE.md`, and this file before modifying code. Prefer the implemented tree and phase design docs over any stale roadmap wording.

---

## Implementation status (as of the current repository)

| Phase | Name | Status | Notes |
|-------|------|--------|--------|
| 1 | Foundations | **Implemented** | Scaffolding, config, logging, Postgres/Alembic schema + `surplusai db` CLI, CI/pre-commit, docker-compose. |
| 2 | County-agnostic parser pipeline | **Implemented** | 2A extraction, 2B interpretation (incl. surplus rule), 2C OCR + append-only county profile learning. See `docs/architecture/PHASE2_PARSER_DESIGN.md`. There is no separate `surplus_ai/normalizer/` package; normalization lives in parser interpretation. |
| 3 | Owner classifier | **Implemented** | Rule-based owner typing; pursuable types are config-driven. See `docs/architecture/PHASE3_CLASSIFIER_DESIGN.md`. |
| 4 | Compliance framework | **Implemented (engine only)** | Engine and YAML loaders exist and are fail-closed. Statutory values for shipped states are **not** recorded. See below and `docs/architecture/PHASE4_COMPLIANCE_DESIGN.md`. |
| 5 | Lead creation | **Implemented** | Counties, cases, properties, owners, compliance evaluations, and leads; promotion path when a state is brought online. See `docs/architecture/PHASE5_LEAD_DESIGN.md`. |
| 6 | Research & Enrichment | **6A–6E COMPLETE; Phase 6F COMPLETE FOR OFFLINE/CORE V1** | Core research/enrichment/skip-trace offline path complete. **P1:** COMPLETE. **P2:** COMPLETE FOR LOCAL READ-ONLY V1. **P3:** IN PROGRESS (**P3-A** local password/user CLI foundation; **P3-B** HTTP session auth NOT STARTED). Dashboard/API remain unauthenticated and 127.0.0.1-only. |

**Not implemented (do not treat as present):** production-enabled live government endpoints, ongoing production ArcGIS automation, an official live generic REST candidate, **live skip-trace / people-search vendors**, scoring CRM automation, Airtable sync, pipeline orchestration, dashboard. Offline Lead-gated Contact materialization exists (Phase 6F V1). One controlled NYC PLUTO Socrata lookup was live-validated on 2026-08-15; `nyc_dcp_pluto` remains disabled and is not selected by any county. A generic offline ArcGIS FeatureServer adapter exists. Disabled fake `example_arcgis` remains. Disabled official Franklin County candidate `franklin_county_oh_auditor_parcels` remains configured, not live-validated, and is not selected by any county. One controlled Lake County ArcGIS happy-path lookup was live-validated on 2026-08-16; `lake_county_fl_pa_tax_parcels` was restored disabled and is not selected by any county. A generic offline REST JSON adapter exists; disabled fake `example_rest_json` only; no official REST candidate configured or live-tested.

---

## Compliance: intentionally fail-closed

The compliance **engine** is complete. Shipped state files `config/compliance/states/md.yaml` and `config/compliance/states/in.yaml` exist with:

- `verified: false`
- statutory fields set to `null` (waiting period, deadlines, fee caps, licence flags, etc.)

Until a state's statutes are researched, cited, filled in, and marked `verified: true`, the engine must not clear cases for that state. The corpus therefore produces **zero legally qualified leads** under current MD/IN configs. That is by design, not a parser or leads bug. After a state is verified, `surplusai leads promote` can create leads from stored cases without re-parsing PDFs.

Do not invent or guess statutory numbers.

Research (Phase 6) collects evidence only. It must not override compliance eligibility or invent statutory rules.

---

## Phase-numbering discrepancy (read this)

Two numbering systems appear in the docs. **Use the implementation numbering below for “what is next.”**

**Implementation / README / phase design docs (authoritative for progress):**

1. Foundations
2. Parser (2A / 2B / 2C)
3. Classifier
4. Compliance framework
5. Lead creation
6. Research & Enrichment — **6A–6E COMPLETE. Phase 6F COMPLETE FOR OFFLINE/CORE V1 (6F-A + 6F-B).**
7+ Scoring, CRM/Airtable, reports, orchestration, multi-county hardening, dashboard, live skip-trace vendor selection, etc. (see README and `ARCHITECTURE.md` §10 status note)

**Original `ARCHITECTURE.md` §10 roadmap (design-era numbering):**

- Phase 0 ≈ Foundations (implementation Phase 1)
- Phase 1 ≈ Parser + Normalizer (implementation Phase 2)
- Phase 2 ≈ Classifier (implementation Phase 3)
- Phase 3 ≈ Compliance (implementation Phase 4)
- Phase 4 ≈ Research & Enrichment (implementation Phase 6)
- Later roadmap phases (scoring, CRM, reports, …) shift accordingly; lead creation was folded into research-era work in the original roadmap and is **implementation Phase 5** in this repo.

`ARCHITECTURE.md` still contains an older header (“design only”) and a full target folder tree; treat §10’s **Implementation status** callout and the files under `docs/architecture/PHASE*_*.md` as the correction for what has already shipped. Do not re-implement Phase 2–5.

---

## Next objective

**Phase 6D–6E are COMPLETE.**

**Phase 6F is COMPLETE FOR OFFLINE/CORE V1** (6F-A + 6F-B).

**Holy Grail CORE PIPELINE** is functionally end-to-end for **OFFLINE/CONTROLLED V1**.

That claim does **not** mean live people-search, outreach automation, CRM/Airtable, or production deployment are complete.

---

## PRODUCTIZATION

**Productization has started. The product is NOT yet deployed or productionized.**

### P1 — Application / API foundation (COMPLETE)

Local-only FastAPI read shell under `surplus_ai/api/`.

- Entry: `surplus_ai.api.app:app`
- Start: `.\.venv\Scripts\python.exe -m uvicorn surplus_ai.api.app:app --host 127.0.0.1 --port 8000`
- Optional deps: `pip install -e ".[dev,api]"` (`fastapi`, plain `uvicorn` — not `[standard]`). The optional `auth` extra is **not** required to start P1/P2.
- OpenAPI (local): `http://127.0.0.1:8000/docs`

**UNAUTHENTICATED P1 API MUST NOT BE INTERNET-FACING.** Default bind is `127.0.0.1` only. No auth, no wildcard CORS, no cloud deploy.

Read-only routes: `/health`, `/api/v1/status`, cases/leads/research reviews/contacts list+detail. Contact responses omit `Contact.value`. Review responses omit research payloads. No write endpoints; no research/skip-trace/outreach invocation.

### P2 — Local browser dashboard (COMPLETE FOR LOCAL READ-ONLY V1)

Vanilla HTML/CSS/JS operator dashboard served by the same FastAPI app.

- Open: `http://127.0.0.1:8000/`
- Same-origin `fetch()` to `/api/v1/*` only
- Read-only: cases, leads, research reviews, contact **metadata** (no `Contact.value`)
- No Node/npm, no frontend framework, no CDN, no CORS, no HTTP authentication, no deployment

**UNAUTHENTICATED P2 DASHBOARD MUST NOT BE INTERNET-FACING.**

P3-A does **not** protect this dashboard. It remains anonymous.

### P3 — Authentication (IN PROGRESS)

See `docs/architecture/PRODUCTIZATION_P3_AUTH.md`.

**P3-A (this increment):** nullable `User.password_hash`, Argon2 via optional `pwdlib[argon2]`, local `surplusai users create` / `surplusai users reset-password`. Existing seed users keep `password_hash = NULL` (not authenticatable until a CLI reset). No HTTP login, cookies, sessions, JWT, public signup, or email reset.

**P3-B:** NOT STARTED (server-side session cookie, login/logout routes, dashboard/API protection).

Do **not** claim authentication is complete until P3-B exists. The product is still **not** internet-safe.

**Live skip-trace vendor / credentials / terms:** NOT STARTED.

Do not make live government API or people-search requests in this task. Do not treat empty compliance YAML as a reason to bypass the fail-closed gate.

---

## Phase 6 core (COMPLETE FOR OFFLINE/CORE V1)

**Phase 6F-A:** Contact dedupe concurrency hardening — unique constraint `uq_contacts_lead_contact_type_value` on `(lead_id, contact_type, value)` plus nested-savepoint IntegrityError race recovery in `materialize_contact_candidate`.

**Phase 6F-B:** Explicit Lead-bound offline orchestration — `run_skip_trace_for_lead(session, lead_id=..., provider=...)` invokes one supplied offline provider once, then routes eligible candidates through Phase 6F-A materialization. Lead gate runs before provider invocation. No automatic Lead-create hook.

---

## Phase 6D-ArcGIS-B (disabled official Franklin candidate; not live-validated)

At completion of 6D-ArcGIS-A, no official ArcGIS dataset was configured. 6D-ArcGIS-B adds Franklin County Auditor as a **disabled** official validation candidate (`franklin_county_oh_auditor_parcels`). The provider is not enabled and is not live-validated. Default remains `manual_lookup`. No county points to it. `verified_for_automated_access: false`. `access_reviewed_on` is absent.

This section does **not** claim legal permission to automate, commercial-use approval, production access, proven anonymous `/query`, or proven token-free operation.

### Official layer metadata

Verified from the official Franklin County ArcGIS layer metadata (documentation only; types/lengths are not stored in `providers.yaml`):

| Field | Esri type | Length |
|-------|-----------|--------|
| `PARCELID` | `esriFieldTypeString` | 11 |
| `OWNERNME1` | `esriFieldTypeString` | 250 |
| `SITEADDRESS` | `esriFieldTypeString` | 70 |
| `OBJECTID` | `esriFieldTypeOID` | (object ID; no string length) |

Layer 0 identity **Tax Parcel**: **VERIFIED**.

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

See also `docs/architecture/PHASE6_RESEARCH_DESIGN.md`.

## Phase 6D-ArcGIS-C (one controlled Lake County happy-path live validation; provider restored disabled)

Franklin County remains disabled and is **not** the ArcGIS-C live-validation candidate because exact Tax Parcel `PARCELID` representation is unresolved.

Lake County Property Appraiser Tax Parcels (`lake_county_fl_pa_tax_parcels`) was the selected ArcGIS-C candidate because official PA/GIS pages supply stronger `AltKey` identity evidence. On **2026-08-16**, one separately human-approved controlled FeatureServer `/query` completed successfully. The provider was immediately restored to **disabled**. Default remains `manual_lookup`. No county points to it. `verified_for_automated_access: false`. `access_reviewed_on` is absent. No token. `ParcelNumber` is not selected.

This section does **not** claim legal permission to automate, commercial-use approval, bulk collection approval, ongoing production access, or that anonymous/token-free access will always succeed.

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
- Official PA page used for identity selection: `https://www.lakecopropappr.com/property-details.aspx?AltKey=1213401`
- Official Map of Property link on that card used the same AltKey: `https://gis.lakecountyfl.gov/gisweb/?query=1213401`

Owner-name and address **values** are not documented. Earlier technical AltKey example `2866713` is superseded as the live-test input; it was not used for the authorized request.

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

Official source: [Site Notice](https://www.lakecopropappr.com/site-notice.aspx). Public-service review/retrieval of publicly available government information; users must comply with applicable law; scraping that blocks or slows access is unauthorized; GIS/property data is for ad valorem / informational purposes and may not be compatible with other uses; no warranties; public-record/privacy principles apply.

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

### WAF / network

Some earlier cloud-origin metadata fetches were WAF-blocked. This one local controlled production-stack `/query` **succeeded**. That does **not** establish that all networks or future requests will succeed. No WAF bypass, no alternate host/IP, no browser emulation, no token workaround, and **no second live request** occurred.

### What ArcGIS-C validated / did not validate

**Validated (happy path, this one request):** production DNS/SSRF validation; HTTPS/TLS; one FeatureServer layer query; controlled query construction; response-envelope parsing; exact local identity match; minimal evidence mapping; fail-closed config restoration.

**Not validated:** redirects; HTTP 400/401/403/404/429/498/499; WAF rejection handling; malformed JSON; ArcGIS top-level error response; `result_incomplete`; `exceededTransferLimit=true`; response-too-large handling; multiple matches; every parcel; every ArcGIS service; production-scale usage. **REDIRECT BEHAVIOR: NOT VERIFIED**.

Canonical source URL (no query string):

`https://gis.lakecountyfl.gov/lakegis/rest/services/OpenData/OpenData1/FeatureServer/12`

See also `docs/architecture/PHASE6_RESEARCH_DESIGN.md`.

---

## Phase 6D generic REST JSON foundation (offline; no live REST candidate)

Generic HTTPS GET JSON adapter (`RestJsonProvider`, config `type: rest_json`) is implemented and offline-tested. It reuses the existing hardened research transport (HTTPS only, redirects disabled, trust_env false, TLS verify, hardened DNS/SSRF, response-size cap, Phase 6B retry ownership only).

**V1 supports:** fixed configured host + path; query-string parcel/account identity; text/number identity types; one GET per `provider.lookup`; JSON object or array; typed `records_path` key list; configured field mapping; exact local identity matching; zero/one/many classification per existing Socrata/ArcGIS convention; compact provenance; fail-closed `verified_for_automated_access`.

**V1 does not support:** POST/PUT/PATCH/DELETE; credentials/API keys/OAuth/cookies; arbitrary headers or caller query dictionaries; URL templates; pagination; redirects; GraphQL/XML/HTML scraping; JSONPath/JMESPath/eval.

Disabled fake example only: `example_rest_json` (`domain: api.example.gov`, `verified_for_automated_access: false`, no `access_reviewed_on`, no county pointer). No official generic REST provider is configured or live-tested. No Lake / Franklin / NYC live traffic occurred for this increment.

---

## Phase 6E-A explicit enrichment apply service (COMPLETE)

`surplus_ai/research/enrichment.py` provides `apply_research_result(session, case_id=..., research_result_id=...)`.

**Eligibility:** persisted row; exact case association; `ResearchStatus.SUCCESS`; `found=true`; non-empty evidence. NOT_FOUND/ERROR/empty evidence blocked.

**Human review:** if `requires_human_review=false`, apply may proceed. If true: only review items for that exact `research_result_id`; pending → `human_review_required`; any non-`evidence_usable` resolution → `human_review_not_usable`; need ≥1 resolved `evidence_usable`.

**Fill-missing only:** `parcel_id` → `SurplusCase.parcel_id` and, when a Property already exists, `Property.parcel_id`. `current_address` → `SurplusCase.property_address_raw`. Never creates Property; if none exists, `property.parcel_id` is reported unsupported. Identical → no-op. Conflicting non-empty local → skip (no overwrite). `Property.last_researched_at` updates only when an existing Property's empty `parcel_id` is newly filled.

**Not applied (unsupported / semantic):** `owner_name_on_record` (Owner is published surplus-list party, not GIS owner-of-record), `account_id`, `mailing_address`, `property_record_id`. No migration.

---

## Phase 6E-B controlled enrichment workflow integration (COMPLETE)

Orchestration only — reuses Phase 6E-A; does not duplicate eligibility.

**New non-review result:** `ResearchPipeline._run_one` after `ResearchResultWriter.persist` + `enqueue_for_result`: if `requires_human_review` is not true, calls `attempt_workflow_enrichment(..., trigger="new_result")` → `apply_research_result`. Same caller-owned session; flush only; no commit.

**Review-required result:** persist + enqueue; **no** apply until review.

**Review resolve:** after conditional pending→closed UPDATE succeeds, if resolution is `evidence_usable`, calls `attempt_workflow_enrichment(..., trigger="review_resolved")`. Non-usable resolutions / reject do **not** invoke apply. Aggregate 6E-A rules still gate multi-item results.

**Cache hit:** return existing `ResearchResult` only — no persist, no enqueue, **no enrichment attempt**.

**Blocked apply:** controlled business outcome; ResearchResult remains valid. Unexpected exceptions propagate for caller rollback (same transaction).

**Boundaries:** No Lead/Contact creation from research enrichment. Compliance untouched. No provider HTTP/DNS for research enrichment. Phase 6F Contact work is separate (`surplus_ai/skip_trace/`; COMPLETE FOR OFFLINE/CORE V1).