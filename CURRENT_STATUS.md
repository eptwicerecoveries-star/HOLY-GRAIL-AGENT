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
| 6 | Research & Enrichment | **6A+6B+6C implemented; 6D Socrata/live-provider foundation + pinned DNS/SSRF destination enforcement + explicit text/number identity typing implemented; one controlled NYC PLUTO live validation completed 2026-08-15; provider remains disabled; 6D-ArcGIS-A generic offline FeatureServer foundation complete/committed (not live-validated); 6D-ArcGIS-B disabled official Franklin County candidate config present (not live-validated); 6D-ArcGIS-C NOT authorized; Phase 6D IN PROGRESS** | 6A: research package skeleton, Manual/Null providers, registry, candidate selection, append-only `ResearchResult` persistence, CLI. 6B: fail-closed ResearchResult cache, nested reliability config, in-process token-bucket pacing, bounded transient retries, `--no-cache`. 6C: `research_review_items` human review queue, additive migration, atomic pending-only close, no Contact/Lead/Property/compliance mutations. 6D slice: sync `ResearchHttpClient`, endpoint/host policy, `SocrataOpenDataProvider`, registry/config DTOs, deterministic matching, compact provenance, pinned DNS/SSRF enforcement (TCP only to validated public resolved addresses; TLS/SNI/Host remain the configured hostname), config-driven `parcel_value_type` / `account_value_type` (`text` default or `number`). On 2026-08-15, one authorized unauthenticated SODA 2.x `/resource` lookup of `nyc_dcp_pluto` (`64uk-42ks`, BBL `4142600080`) returned HTTP 200 / `success` / `found` / `result_count` 1 / `match_mode` `exact_parcel` with evidence fields `parcel_id`, `owner_name_on_record`, `current_address`. No token, no persistence, no county pointer, no retry, no v3 fallback. The provider was restored to `verified_for_automated_access: false`; `access_reviewed_on` is absent. No county points to it. Default remains `manual_lookup`. This is not production enablement, not commercial/legal clearance, and not authorization of another live request. 6D-ArcGIS-A: generic FeatureServer adapter, validated domain/service_path/layer construction, text/number identity, one GET per lookup, no pagination, truncation-first `result_incomplete`, `returnGeometry=false`, compact provenance, disabled fake `example_arcgis`. At completion of 6D-ArcGIS-A, no official ArcGIS dataset was configured. 6D-ArcGIS-B adds Franklin County Auditor as a disabled official validation candidate `franklin_county_oh_auditor_parcels` (`verified_for_automated_access: false`; no `access_reviewed_on`; no token; no county pointer). Field types/lengths, parcel-id representation caveat, terms/access classifications, owner-data boundary, and static endpoints are documented in the 6D-ArcGIS-B section below and in `docs/architecture/PHASE6_RESEARCH_DESIGN.md`. Anonymous `/query` is **NOT VERIFIED**. **REDIRECT BEHAVIOR: NOT VERIFIED**. No live ArcGIS request occurred. Phase 6D is still **in progress**. Generic REST remains after ArcGIS. 6E/6F are **not started**. 6D-ArcGIS-C is **not authorized**. |

**Not implemented (do not treat as present):** production-enabled live government endpoints, live-validated ArcGIS access, generic REST providers, Contact materialization, `scoring/`, `crm/`, `reports/`, `integrations/` (Airtable), `pipeline/` orchestration, `dashboard/`. One controlled NYC PLUTO Socrata lookup was live-validated on 2026-08-15; `nyc_dcp_pluto` remains disabled and is not selected by any county. A generic offline ArcGIS FeatureServer adapter exists. Disabled fake `example_arcgis` remains. Disabled official Franklin County candidate `franklin_county_oh_auditor_parcels` is configured but not live-validated and is not selected by any county.

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
6. Research & Enrichment — **6A+6B+6C complete; 6D Socrata/live-provider foundation + pinned DNS/SSRF destination enforcement + explicit text/number identity typing implemented; one controlled NYC PLUTO live validation completed 2026-08-15; `nyc_dcp_pluto` remains disabled. 6D-ArcGIS-A generic offline FeatureServer foundation is complete/committed (not live-validated); `example_arcgis` remains disabled. 6D-ArcGIS-B disabled official Franklin County candidate config is present; not live-validated. 6D-ArcGIS-C is NOT authorized / not live-validated. Phase 6D is still in progress. Generic REST remains after ArcGIS and is not implemented. 6E/6F are not started.**
7+ Scoring, CRM/Airtable, reports, orchestration, multi-county hardening, dashboard, etc. (see README and `ARCHITECTURE.md` §10 status note)

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

**Phase 6D Socrata live-provider validation is complete for one controlled request only.** On 2026-08-15, `nyc_dcp_pluto` (`data.cityofnewyork.us` / `64uk-42ks`, BBL `4142600080`) accepted one unauthenticated SODA 2.x `/resource` lookup (HTTP 200, `success`, `found`, `result_count` 1, `match_mode` `exact_parcel`). Production DNS/SSRF/TLS protections permitted that request. The provider remains **disabled** (`verified_for_automated_access: false`; `access_reviewed_on` absent). No county points to it. Default remains `manual_lookup`. Commercial-use/attribution judgment, county activation, production TTL/rate-limit/token policy, and any additional live request remain **not authorized**.

Remaining Phase 6D work is later increments: a separately authorized 6D-ArcGIS-C controlled live ArcGIS request (**NOT authorized** here; not live-validated), then generic REST after ArcGIS. 6D-ArcGIS-A is a complete/committed offline foundation. 6D-ArcGIS-B added disabled official Franklin County Auditor config only. Phase 6D is still **in progress**. Phase 6E and Phase 6F are **not started**. Do not treat Franklin API access as working. Do not treat 6D-ArcGIS-A/B as live validation, as completion of all of 6D, or as clearance to begin 6E/6F. Do not treat the Socrata milestone as production enablement of PLUTO.

Do not make another live request. Do not begin 6E/6F. Do not treat empty compliance YAML as a reason to bypass the fail-closed gate.

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
