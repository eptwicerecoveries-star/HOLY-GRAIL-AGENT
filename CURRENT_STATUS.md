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
| 6 | Research & Enrichment | **6A+6B+6C implemented; 6D Socrata/live-provider foundation + pinned DNS/SSRF destination enforcement + explicit text/number identity typing implemented; one controlled NYC PLUTO live validation completed 2026-08-15; provider remains disabled; 6D-ArcGIS-A generic offline FeatureServer foundation implemented (not live-validated)** | 6A: research package skeleton, Manual/Null providers, registry, candidate selection, append-only `ResearchResult` persistence, CLI. 6B: fail-closed ResearchResult cache, nested reliability config, in-process token-bucket pacing, bounded transient retries, `--no-cache`. 6C: `research_review_items` human review queue, additive migration, atomic pending-only close, no Contact/Lead/Property/compliance mutations. 6D slice: sync `ResearchHttpClient`, endpoint/host policy, `SocrataOpenDataProvider`, registry/config DTOs, deterministic matching, compact provenance, pinned DNS/SSRF enforcement (TCP only to validated public resolved addresses; TLS/SNI/Host remain the configured hostname), config-driven `parcel_value_type` / `account_value_type` (`text` default or `number`). On 2026-08-15, one authorized unauthenticated SODA 2.x `/resource` lookup of `nyc_dcp_pluto` (`64uk-42ks`, BBL `4142600080`) returned HTTP 200 / `success` / `found` / `result_count` 1 / `match_mode` `exact_parcel` with evidence fields `parcel_id`, `owner_name_on_record`, `current_address`. No token, no persistence, no county pointer, no retry, no v3 fallback. The provider was restored to `verified_for_automated_access: false`; `access_reviewed_on` is absent. No county points to it. Default remains `manual_lookup`. This is not production enablement, not commercial/legal clearance, and not authorization of another live request. 6D-ArcGIS-A: generic FeatureServer adapter, validated domain/service_path/layer construction, text/number identity, one GET per lookup, no pagination, truncation-first `result_incomplete`, `returnGeometry=false`, compact provenance, disabled fake `example_arcgis`. All ArcGIS tests are offline/mocked. No live ArcGIS request occurred. No official ArcGIS candidate is configured. Franklin County remains a 6D-ArcGIS-B candidate only. Phase 6D is still in progress. Generic REST remains after ArcGIS. 6E/6F are not started. See `docs/architecture/PHASE6_RESEARCH_DESIGN.md`. |

**Not implemented (do not treat as present):** production-enabled live government endpoints, official ArcGIS candidates, generic REST providers, Contact materialization, `scoring/`, `crm/`, `reports/`, `integrations/` (Airtable), `pipeline/` orchestration, `dashboard/`. One controlled NYC PLUTO Socrata lookup was live-validated on 2026-08-15; `nyc_dcp_pluto` remains disabled and is not selected by any county. A generic offline ArcGIS FeatureServer adapter exists with disabled fake `example_arcgis` only; it is not live-validated.

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
6. Research & Enrichment — **6A+6B+6C complete; 6D Socrata/live-provider foundation + pinned DNS/SSRF destination enforcement + explicit text/number identity typing implemented; one controlled NYC PLUTO live validation completed 2026-08-15; `nyc_dcp_pluto` remains disabled. 6D-ArcGIS-A generic offline FeatureServer foundation implemented (not live-validated); `example_arcgis` remains disabled. Phase 6D is still in progress. Generic REST is not implemented.**
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

Remaining Phase 6D work is later increments: 6D-ArcGIS-B (official ArcGIS candidate config; Franklin County remains a candidate only), then generic REST. Do not treat 6D-ArcGIS-A as live validation, as completion of all of 6D, or as clearance to begin 6E/6F. Do not treat the Socrata milestone as production enablement of PLUTO.

Do not make another live request. Do not begin 6E/6F. Do not treat empty compliance YAML as a reason to bypass the fail-closed gate.
