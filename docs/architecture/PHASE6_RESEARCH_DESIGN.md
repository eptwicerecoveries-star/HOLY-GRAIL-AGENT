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
5. **Incremental delivery:** 6A → stop → approve 6B separately.

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

## Later phases (not started)

- 6B: cache, rate limits, retries
- 6C: `research_review_items`
- 6D+: live Socrata/ArcGIS/REST
- 6E: local enrichment apply paths
- 6F: skip-trace + Contact materialization when Lead exists
