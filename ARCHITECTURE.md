# SurplusAI — Software Architecture

Status: design only, no implementation yet. This document is the contract for all future
implementation phases. It follows PROJECT.md's mandate: nothing county-specific or
state-specific is ever hardcoded — all of it lives in `config/`.

---

## 0. Domain Model

Surplus funds ("overages") are created when a tax deed or foreclosure sale generates more
proceeds than the debt owed. The excess is owed back to the former owner (subject to
lienholder priority and state claim procedures) and is held by the county until claimed or
escheated. SurplusAI finds these cases, extracts them from published county lists, filters
to individual owners who are realistically contactable, checks state-specific compliance
constraints, enriches with contact data, scores the opportunity, and feeds a CRM (Airtable)
+ daily call sheets for a recovery team.

Core entities: `County`, `IngestionJob`, `RawSurplusRow`, `SurplusCase`, `Owner`, `Property`,
`ComplianceEvaluation`, `Lead`, `Contact`, `ResearchResult`, `LeadScore`, `Interaction`,
`Deal`, `User`, `CallSheet`, `ExportBatch`, `AirtableSyncRecord`, `AuditLog`.

Pipeline shape (each stage is a separate, independently testable module):

```
PDF acquisition -> Parsing -> Normalization -> Deduplication -> Classification
  -> Compliance filtering -> Research/Enrichment -> Scoring -> Lead qualification
  -> CSV export -> Airtable sync -> Call sheet generation -> CRM activity logging
```

Nothing in this chain may branch on `if county == "..."` or `if state == "..."`. Behavior
differences are expressed as data: a `CountyParsingProfile` YAML file and a
`StateComplianceRules` YAML file, both loaded and validated through Pydantic models.

---

## 1. Complete Folder Structure

```
surplus_ai/
    __init__.py
    cli/
        __init__.py
        main.py
        commands/
            __init__.py
            ingest.py
            parse.py
            classify.py
            research.py
            score.py
            export.py
            airtable_sync.py
            callsheet.py
            county.py
            compliance.py
            pipeline.py
            db.py
    parser/
        __init__.py
        base.py
        profile.py
        profile_loader.py
        table_extractor.py
        text_extractor.py
        ocr_extractor.py
        column_mapper.py
        row_builder.py
        heuristics.py
        llm_assist.py
        validators.py
        exceptions.py
    normalizer/
        __init__.py
        pipeline.py
        name_normalizer.py
        address_normalizer.py
        currency_normalizer.py
        date_normalizer.py
        dedupe.py
        schema.py
        exceptions.py
    classifier/
        __init__.py
        owner_type_classifier.py
        entity_keywords.py
        ml_classifier.py
        rules.py
        exceptions.py
    compliance/
        __init__.py
        state_rules.py
        rules_loader.py
        engine.py
        waiting_period.py
        fee_cap.py
        disclosure.py
        exceptions.py
    research/
        __init__.py
        base.py
        provider_registry.py
        providers/
            __init__.py
            generic_socrata.py
            generic_arcgis.py
            county_api_provider.py
            manual_lookup_provider.py
        skip_trace/
            __init__.py
            base.py
            client.py
            models.py
        enrichment/
            __init__.py
            usps_validator.py
            geocoder.py
            phone_validator.py
            email_finder.py
        cache.py
        exceptions.py
    scoring/
        __init__.py
        engine.py
        weights.py
        factors/
            __init__.py
            surplus_amount_factor.py
            time_since_sale_factor.py
            contactability_factor.py
            compliance_factor.py
            property_value_factor.py
            owner_location_factor.py
        exceptions.py
    crm/
        __init__.py
        models.py
        repository.py
        pipeline_stages.py
        activity_logger.py
        assignment.py
        exceptions.py
    reports/
        __init__.py
        csv_export.py
        call_sheet_generator.py
        summary_report.py
        templates/
            call_sheet.html.j2
            daily_summary.html.j2
            csv_export_header.j2
        exceptions.py
    integrations/
        __init__.py
        airtable/
            __init__.py
            client.py
            schema.py
            sync_engine.py
            mappers.py
            rate_limiter.py
            exceptions.py
        county_portals/
            __init__.py
            bulk_downloader.py
            sftp_downloader.py
            email_ingest.py
    database/
        __init__.py
        engine.py
        base.py
        models/
            __init__.py
            county.py
            parsing_profile.py
            ingestion_job.py
            raw_surplus_row.py
            surplus_case.py
            owner.py
            property.py
            compliance_evaluation.py
            lead.py
            contact.py
            research_result.py
            lead_score.py
            interaction.py
            deal.py
            user.py
            call_sheet.py
            export_batch.py
            airtable_sync_record.py
            audit_log.py
        migrations/
            env.py
            script.py.mako
            versions/
        repositories/
            __init__.py
            county_repository.py
            case_repository.py
            lead_repository.py
            contact_repository.py
            interaction_repository.py
            deal_repository.py
            export_repository.py
        seed.py
    pipeline/
        __init__.py
        orchestrator.py
        stages.py
        context.py
        scheduler.py
        exceptions.py
    dashboard/
        __init__.py
        app.py
        pages/
            __init__.py
            overview.py
            leads.py
            call_sheet.py
            county_manager.py
            compliance_status.py
            analytics.py
        components/
            __init__.py
            charts.py
            tables.py
            filters.py
    utils/
        __init__.py
        config.py
        logging_config.py
        exceptions.py
        retry.py
        rate_limiter.py
        file_utils.py
        hashing.py
        money.py
        dates.py
        strings.py
        validation.py
        secrets.py

tests/
    conftest.py
    unit/
        parser/
        normalizer/
        classifier/
        compliance/
        research/
        scoring/
        crm/
        reports/
        integrations/
        database/
        pipeline/
        utils/
    integration/
        test_full_pipeline.py
        test_airtable_sync.py
        test_county_bulk_download.py
    fixtures/
        pdfs/
        profiles/
        airtable_responses/

config/
    settings.yaml
    settings.dev.yaml
    settings.prod.yaml
    logging.yaml
    counties/
        _template.yaml
    compliance/
        states/
            _template.yaml
    classification/
        entity_keywords.yaml
    scoring/
        weights.yaml
    airtable/
        schema_mapping.yaml
    research/
        providers.yaml

docs/
    architecture/
        ARCHITECTURE.md
        adr/
            0001-postgres-as-system-of-record.md
            0002-airtable-as-crm-mirror-not-source-of-truth.md
            0003-config-driven-county-profiles.md
    runbooks/
        onboarding_a_county.md
        onboarding_a_state_compliance_profile.md
        incident_response.md
        rotating_secrets.md

data/
    raw_pdfs/
    processed/
    cache/

logs/

exports/
    csv/

scripts/
    bootstrap_db.sh
    run_daily_pipeline.sh
    backup_db.sh

.env.example
.pre-commit-config.yaml
pyproject.toml
alembic.ini
Makefile
docker-compose.yml
Dockerfile
README.md
PROJECT.md
CLAUDE.md
```

---

## 2–4. Modules, Classes, and Functions

Every module below is listed with its classes and the functions/methods on each class.
Free functions (not attached to a class) are listed under "module-level functions."

### 2.1 `surplus_ai/cli/`

**`main.py`**
- `build_app() -> typer.Typer` — assembles the root Typer app and registers every command
  group in `commands/`.
- module-level: `app = build_app()`, `if __name__ == "__main__": app()`

**`commands/ingest.py`**
- `ingest_county(county_slug: str, file_path: Path | None, since: date | None) -> None` —
  runs acquisition + parsing for one county.
- `ingest_all(active_only: bool = True) -> None` — runs ingestion for every configured county.

**`commands/parse.py`**
- `parse_file(county_slug: str, file_path: Path) -> None` — parses one PDF without touching
  live acquisition, for debugging a profile.
- `validate_profile(county_slug: str) -> None` — dry-run profile validation against a sample.

**`commands/classify.py`**
- `classify_pending(batch_size: int = 500) -> None` — runs classifier over unclassified owners.
- `reclassify_all() -> None` — re-runs classification after a rules/config change.

**`commands/research.py`**
- `research_case(case_id: UUID) -> None`
- `research_pending(county_slug: str | None, limit: int) -> None`

**`commands/score.py`**
- `score_leads(county_slug: str | None) -> None`
- `rescore_all() -> None`

**`commands/export.py`**
- `export_csv(county_slug: str | None, output_dir: Path) -> None`

**`commands/airtable_sync.py`**
- `sync_push(table: str | None) -> None`
- `sync_pull(table: str | None) -> None`
- `sync_status() -> None`

**`commands/callsheet.py`**
- `generate(run_date: date | None, assigned_user: str | None) -> None`
- `show(run_date: date | None) -> None`

**`commands/county.py`**
- `add(slug: str) -> None` — scaffolds a new county YAML from `_template.yaml`.
- `list_counties(state: str | None, active_only: bool) -> None`
- `validate(slug: str) -> None`

**`commands/compliance.py`**
- `add_state(state_code: str) -> None`
- `validate_state(state_code: str) -> None`
- `list_states() -> None`

**`commands/pipeline.py`**
- `run(county_slug: str | None, dry_run: bool) -> None` — full end-to-end orchestrated run.

**`commands/db.py`**
- `migrate() -> None`
- `init() -> None`
- `seed_dev_data() -> None`
- `backup(output_path: Path) -> None`

### 2.2 `surplus_ai/parser/`

**`base.py`**
- `class ParsedRow` (dataclass): `raw_fields: dict[str, str]`, `page_number: int`,
  `extraction_method: ExtractionMethod`, `source_row_index: int`
- `class AbstractPDFParser(ABC)`
  - `parse(self, file_path: Path, profile: CountyParsingProfile) -> list[ParsedRow]`
  - `supports(self, file_path: Path) -> bool`
- `class ExtractionMethod(str, Enum)`: `TABLE`, `TEXT`, `OCR`, `LLM_ASSIST`

**`profile.py`**
- `class ColumnMapping(BaseModel)`: `source_header: str`, `canonical_field: str`,
  `regex_override: str | None`
- `class CountyParsingProfile(BaseModel)`: `county_slug: str`, `extraction_strategy:
  Literal["table","text","ocr","auto"]`, `column_mappings: list[ColumnMapping]`,
  `header_row_index: int | None`, `skip_footer_rows: int`, `date_format: str | None`,
  `currency_locale: str`, `page_range: tuple[int,int] | None`, `notes: str | None`

**`profile_loader.py`**
- `class ProfileLoader`
  - `load(self, county_slug: str) -> CountyParsingProfile`
  - `load_all(self) -> dict[str, CountyParsingProfile]`
  - `validate(self, profile: CountyParsingProfile) -> list[str]` — returns validation warnings

**`table_extractor.py`**
- `class TableExtractor`
  - `extract(self, file_path: Path, profile: CountyParsingProfile) -> list[ParsedRow]`
  - `_extract_with_pdfplumber(self, file_path: Path, page_range) -> list[list[str]]`
  - `_extract_with_camelot(self, file_path: Path, page_range) -> list[list[str]]`

**`text_extractor.py`**
- `class TextExtractor`
  - `extract(self, file_path: Path, profile: CountyParsingProfile) -> list[ParsedRow]`
  - `_split_lines_to_fields(self, line: str, profile: CountyParsingProfile) -> dict[str,str]`

**`ocr_extractor.py`**
- `class OCRExtractor`
  - `extract(self, file_path: Path, profile: CountyParsingProfile) -> list[ParsedRow]`
  - `_rasterize_pages(self, file_path: Path) -> list[Image]`
  - `_ocr_page(self, image: Image) -> str`

**`column_mapper.py`**
- `class ColumnMapper`
  - `map_row(self, raw_row: dict[str,str], profile: CountyParsingProfile) -> dict[str,str]`
  - `detect_headers(self, table: list[list[str]]) -> list[str]`

**`row_builder.py`**
- `class RowBuilder`
  - `build(self, parsed_rows: list[ParsedRow], profile: CountyParsingProfile,
    ingestion_job_id: UUID) -> list[RawSurplusRowDTO]`

**`heuristics.py`**
- module-level: `detect_column_by_keywords(headers: list[str], candidates: list[str]) -> int | None`
- module-level: `guess_currency_columns(sample_rows: list[dict]) -> list[str]`
- module-level: `guess_date_columns(sample_rows: list[dict]) -> list[str]`

**`llm_assist.py`**
- `class LLMAssistExtractor`
  - `extract(self, page_text: str) -> list[dict[str,str]]` — calls the configured LLM
    provider to propose a row structure; result is always tagged `LLM_ASSIST` and routed to
    a human-review queue, never auto-committed to `SurplusCase`.
  - `_build_prompt(self, page_text: str, expected_fields: list[str]) -> str`

**`validators.py`**
- module-level: `require_fields(row: dict, required: list[str]) -> list[str]`
- module-level: `validate_row_structure(row: ParsedRow, profile: CountyParsingProfile) -> list[str]`

**`exceptions.py`**
- `ParserError`, `UnsupportedPDFError`, `ColumnMappingError`, `ProfileValidationError`

### 2.3 `surplus_ai/normalizer/`

**`schema.py`**
- `class CanonicalSurplusRecord(BaseModel)`: `case_number: str`, `parcel_id: str | None`,
  `owner_raw_name: str`, `property_address: NormalizedAddress`, `sale_date: date | None`,
  `judgment_amount: Decimal | None`, `sale_amount: Decimal | None`,
  `surplus_amount: Decimal`, `distribution_deadline: date | None`
- `class NormalizedAddress(BaseModel)`: `street`, `city`, `state`, `zip_code`, `raw`

**`pipeline.py`**
- `class NormalizationPipeline`
  - `run(self, raw_rows: list[RawSurplusRowDTO]) -> list[CanonicalSurplusRecord]`
  - `_normalize_one(self, raw_row: RawSurplusRowDTO) -> CanonicalSurplusRecord`

**`name_normalizer.py`**
- `class NameNormalizer`
  - `split_person_name(self, raw: str) -> PersonName`
  - `clean_entity_name(self, raw: str) -> str`
- `class PersonName(BaseModel)`: `first`, `middle`, `last`, `suffix`

**`address_normalizer.py`**
- `class AddressNormalizer`
  - `normalize(self, raw_address: str) -> NormalizedAddress`
  - `_parse_with_usaddress(self, raw_address: str) -> dict`

**`currency_normalizer.py`**
- `class CurrencyNormalizer`
  - `parse(self, raw_value: str, locale: str = "en_US") -> Decimal | None`

**`date_normalizer.py`**
- `class DateNormalizer`
  - `parse(self, raw_value: str, expected_format: str | None) -> date | None`

**`dedupe.py`**
- `class DedupeService`
  - `compute_hash(self, record: CanonicalSurplusRecord, county_slug: str) -> str`
  - `find_duplicate(self, record_hash: str) -> SurplusCase | None`
  - `fuzzy_match_owner(self, name_a: str, name_b: str) -> float` — RapidFuzz-based score

**`exceptions.py`**
- `NormalizationError`, `AddressParseError`, `CurrencyParseError`, `DateParseError`

### 2.4 `surplus_ai/classifier/`

**`owner_type_classifier.py`**
- `class OwnerTypeClassifier`
  - `classify(self, raw_name: str) -> ClassificationResult`
  - `_apply_rules(self, raw_name: str) -> ClassificationResult | None`
  - `_apply_ml_fallback(self, raw_name: str) -> ClassificationResult`
- `class ClassificationResult(BaseModel)`: `owner_type: OwnerType`, `confidence: float`,
  `method: Literal["rule","ml","manual"]`
- `class OwnerType(str, Enum)`: `INDIVIDUAL`, `COMPANY`, `TRUST`, `ESTATE`, `GOVERNMENT`, `UNKNOWN`

**`entity_keywords.py`**
- `class EntityKeywordSet(BaseModel)`: `company_suffixes: list[str]`, `trust_markers:
  list[str]`, `estate_markers: list[str]`, `government_markers: list[str]`
- module-level: `load_entity_keywords() -> EntityKeywordSet`

**`rules.py`**
- `class RuleBasedClassifier`
  - `matches_company(self, name: str, keywords: EntityKeywordSet) -> bool`
  - `matches_trust(self, name: str, keywords: EntityKeywordSet) -> bool`
  - `matches_estate(self, name: str, keywords: EntityKeywordSet) -> bool`
  - `matches_government(self, name: str, keywords: EntityKeywordSet) -> bool`
  - `looks_like_person(self, name: str) -> bool`

**`ml_classifier.py`**
- `class MLOwnerTypeClassifier`
  - `load_model(self, model_path: Path) -> None`
  - `predict(self, name: str) -> ClassificationResult`
  - `train(self, labeled_examples: list[tuple[str, OwnerType]], output_path: Path) -> None`

**`exceptions.py`**
- `ClassificationError`, `ModelNotLoadedError`

### 2.5 `surplus_ai/compliance/`

**`state_rules.py`**
- `class StateComplianceRules(BaseModel)`: `state_code: str`, `max_contingency_fee_pct:
  float`, `waiting_period_days: int`, `required_disclosures: list[str]`,
  `requires_notarized_contract: bool`, `requires_locator_license: bool`,
  `escheatment_period_days: int | None`, `notes: str | None`

**`rules_loader.py`**
- `class ComplianceRulesLoader`
  - `load(self, state_code: str) -> StateComplianceRules`
  - `load_all(self) -> dict[str, StateComplianceRules]`

**`engine.py`**
- `class ComplianceEngine`
  - `evaluate(self, case: SurplusCase, rules: StateComplianceRules) -> ComplianceResult`
- `class ComplianceResult(BaseModel)`: `is_eligible: bool`, `earliest_contact_date: date`,
  `fee_cap_pct: float`, `disclosures_required: list[str]`, `blocking_reasons: list[str]`

**`waiting_period.py`**
- module-level: `compute_earliest_contact_date(sale_date: date, waiting_period_days: int) -> date`

**`fee_cap.py`**
- module-level: `validate_fee_pct(proposed_pct: float, cap_pct: float) -> bool`

**`disclosure.py`**
- module-level: `get_required_disclosure_text(disclosure_key: str) -> str`

**`exceptions.py`**
- `ComplianceError`, `StateRulesNotFoundError`, `ComplianceValidationError`

### 2.6 `surplus_ai/research/`

**`base.py`**
- `class AbstractPropertyRecordProvider(ABC)`
  - `supports(self, county_slug: str) -> bool`
  - `lookup(self, parcel_id: str | None, owner_name: str | None) -> PropertyRecordResult`
- `class PropertyRecordResult(BaseModel)`: `found: bool`, `assessed_value: Decimal | None`,
  `mailing_address: NormalizedAddress | None`, `legal_description: str | None`, `raw: dict`

**`provider_registry.py`**
- `class ProviderRegistry`
  - `register(self, provider: AbstractPropertyRecordProvider) -> None`
  - `resolve(self, county_slug: str) -> AbstractPropertyRecordProvider`

**`providers/generic_socrata.py`**
- `class SocrataProvider(AbstractPropertyRecordProvider)` — implements `supports`, `lookup`
  against a Socrata `resource_id`/domain read from `research/providers.yaml`.

**`providers/generic_arcgis.py`**
- `class ArcGISProvider(AbstractPropertyRecordProvider)` — queries an ArcGIS REST
  FeatureServer layer URL from config.

**`providers/county_api_provider.py`**
- `class ConfigurableRESTProvider(AbstractPropertyRecordProvider)` — generic
  request-template-driven provider (URL template, query param mapping, response JSONPath
  mapping), all from config, for one-off county REST APIs.

**`providers/manual_lookup_provider.py`**
- `class ManualLookupProvider(AbstractPropertyRecordProvider)` — always returns
  `found=False` and enqueues the case into a human-research queue table.

**`skip_trace/base.py`**
- `class AbstractSkipTraceProvider(ABC)`
  - `trace(self, name: PersonName, last_known_address: NormalizedAddress | None) ->
    SkipTraceResult`

**`skip_trace/client.py`**
- `class SkipTraceClient`
  - `trace(self, lead: Lead) -> SkipTraceResult` — resolves the configured vendor
    implementation via `provider_registry`-style lookup and delegates.

**`skip_trace/models.py`**
- `class SkipTraceResult(BaseModel)`: `phones: list[str]`, `emails: list[str]`,
  `current_address: NormalizedAddress | None`, `confidence: float`, `provider: str`

**`enrichment/usps_validator.py`**
- `class USPSAddressValidator`
  - `validate(self, address: NormalizedAddress) -> NormalizedAddress`

**`enrichment/geocoder.py`**
- `class Geocoder`
  - `geocode(self, address: NormalizedAddress) -> tuple[float, float] | None`

**`enrichment/phone_validator.py`**
- `class PhoneValidator`
  - `validate(self, phone: str) -> PhoneValidationResult`
- `class PhoneValidationResult(BaseModel)`: `is_valid: bool`, `line_type: str | None`,
  `carrier: str | None`

**`enrichment/email_finder.py`**
- `class EmailFinder`
  - `find(self, name: PersonName, address: NormalizedAddress | None) -> list[str]`

**`cache.py`**
- `class ResearchCache`
  - `get(self, cache_key: str) -> ResearchResult | None`
  - `put(self, cache_key: str, result: ResearchResult) -> None`

**`exceptions.py`**
- `ResearchError`, `ProviderNotFoundError`, `SkipTraceError`, `EnrichmentError`

### 2.7 `surplus_ai/scoring/`

**`engine.py`**
- `class ScoringEngine`
  - `score(self, lead: Lead, context: ScoringContext) -> ScoreResult`
- `class ScoringContext(BaseModel)`: `case: SurplusCase`, `contacts: list[Contact]`,
  `compliance_result: ComplianceResult`, `property_record: PropertyRecordResult | None`
- `class ScoreResult(BaseModel)`: `total_score: float`, `factor_breakdown: dict[str, float]`

**`weights.py`**
- `class ScoringWeights(BaseModel)`: `surplus_amount_weight: float`,
  `time_since_sale_weight: float`, `contactability_weight: float`,
  `compliance_weight: float`, `property_value_weight: float`, `owner_location_weight: float`
- module-level: `load_scoring_weights() -> ScoringWeights`

**`factors/*.py`** — one class per factor, uniform interface:
- `class SurplusAmountFactor`: `compute(self, context: ScoringContext) -> float`
- `class TimeSinceSaleFactor`: `compute(self, context: ScoringContext) -> float`
- `class ContactabilityFactor`: `compute(self, context: ScoringContext) -> float`
- `class ComplianceFactor`: `compute(self, context: ScoringContext) -> float`
- `class PropertyValueFactor`: `compute(self, context: ScoringContext) -> float`
- `class OwnerLocationFactor`: `compute(self, context: ScoringContext) -> float`

**`exceptions.py`**
- `ScoringError`, `MissingFactorInputError`

### 2.8 `surplus_ai/crm/`

**`models.py`**
- `class LeadDTO`, `ContactDTO`, `InteractionDTO`, `DealDTO` (Pydantic mirrors of ORM rows
  for use outside the DB layer)

**`pipeline_stages.py`**
- `class PipelineStage(str, Enum)`: `NEW`, `QUALIFIED`, `CONTACTED`, `INTERESTED`,
  `CONTRACT_SENT`, `CONTRACT_SIGNED`, `CLAIM_FILED`, `PAID`, `DEAD`

**`repository.py`**
- `class CRMRepository`
  - `get_lead(self, lead_id: UUID) -> LeadDTO | None`
  - `list_leads(self, filters: LeadFilter) -> list[LeadDTO]`
  - `update_stage(self, lead_id: UUID, stage: PipelineStage) -> None`
  - `add_contact(self, lead_id: UUID, contact: ContactDTO) -> None`

**`activity_logger.py`**
- `class ActivityLogger`
  - `log_call(self, lead_id: UUID, user_id: UUID, outcome: str, notes: str) -> None`
  - `log_email(self, lead_id: UUID, user_id: UUID, notes: str) -> None`
  - `log_note(self, lead_id: UUID, user_id: UUID, notes: str) -> None`

**`assignment.py`**
- `class LeadAssignmentService`
  - `assign_round_robin(self, lead_ids: list[UUID], user_ids: list[UUID]) -> dict[UUID, UUID]`
  - `assign_by_rule(self, lead: LeadDTO, rules: list[AssignmentRule]) -> UUID | None`

**`exceptions.py`**
- `CRMError`, `LeadNotFoundError`, `InvalidStageTransitionError`

### 2.9 `surplus_ai/reports/`

**`csv_export.py`**
- `class CSVExporter`
  - `export_leads(self, leads: list[LeadDTO], output_path: Path) -> ExportBatchDTO`

**`call_sheet_generator.py`**
- `class CallSheetGenerator`
  - `generate(self, run_date: date, assigned_user_id: UUID | None) -> CallSheetDTO`
  - `_rank_leads(self, leads: list[LeadDTO]) -> list[LeadDTO]`

**`summary_report.py`**
- `class SummaryReportGenerator`
  - `daily_summary(self, run_date: date) -> SummaryReportDTO`
  - `weekly_summary(self, week_start: date) -> SummaryReportDTO`

**`exceptions.py`**
- `ReportGenerationError`

### 2.10 `surplus_ai/integrations/airtable/`

**`client.py`**
- `class AirtableClient`
  - `list_records(self, table: str, formula: str | None) -> list[dict]`
  - `create_record(self, table: str, fields: dict) -> dict`
  - `update_record(self, table: str, record_id: str, fields: dict) -> dict`
  - `upsert_record(self, table: str, key_field: str, fields: dict) -> dict`
  - `delete_record(self, table: str, record_id: str) -> None`
  - `batch_create(self, table: str, records: list[dict]) -> list[dict]`
  - `batch_update(self, table: str, records: list[dict]) -> list[dict]`

**`schema.py`**
- `class AirtableSchemaMapping(BaseModel)`: `table_name: str`, `field_map: dict[str, str]`
- module-level: `load_schema_mapping() -> dict[str, AirtableSchemaMapping]`

**`sync_engine.py`**
- `class AirtableSyncEngine`
  - `push_leads(self, leads: list[LeadDTO]) -> SyncReport`
  - `push_interactions(self, interactions: list[InteractionDTO]) -> SyncReport`
  - `pull_stage_changes(self) -> list[LeadStageChange]` — reads staff-driven stage edits
    made directly in Airtable back into Postgres.
  - `resolve_conflict(self, local: LeadDTO, remote: dict) -> LeadDTO`
- `class SyncReport(BaseModel)`: `created: int`, `updated: int`, `failed: int`,
  `errors: list[str]`

**`mappers.py`**
- module-level: `lead_to_airtable_fields(lead: LeadDTO) -> dict`
- module-level: `airtable_fields_to_lead_patch(fields: dict) -> dict`

**`rate_limiter.py`**
- `class AirtableRateLimiter` — token-bucket limiter honoring Airtable's 5 req/s/base cap.
  - `acquire(self) -> None`

**`exceptions.py`**
- `AirtableError`, `AirtableRateLimitError`, `AirtableSchemaMismatchError`

### 2.11 `surplus_ai/integrations/county_portals/`

**`bulk_downloader.py`**
- `class BulkDownloader`
  - `fetch(self, county: County) -> Path` — downloads from `county.source_url` per its
    configured schedule/auth, using `requests` with retry/backoff.

**`sftp_downloader.py`**
- `class SFTPDownloader`
  - `fetch(self, county: County) -> list[Path]`

**`email_ingest.py`**
- `class EmailIngestWatcher`
  - `poll_inbox(self) -> list[Path]` — IMAP polling of a monitored mailbox for counties that
    email their lists; saves attachments to `data/raw_pdfs/`.

### 2.12 `surplus_ai/database/`

**`engine.py`**
- module-level: `get_engine() -> Engine`
- module-level: `get_session_factory() -> sessionmaker`
- module-level: `session_scope() -> ContextManager[Session]`

**`base.py`**
- `class Base(DeclarativeBase)`

**`models/*.py`** — one SQLAlchemy ORM class per table (see §5 for full column lists):
`County`, `ParsingProfileVersion`, `IngestionJob`, `RawSurplusRow`, `SurplusCase`, `Owner`,
`Property`, `ComplianceEvaluation`, `Lead`, `Contact`, `ResearchResult`, `LeadScore`,
`Interaction`, `Deal`, `User`, `CallSheet`, `CallSheetItem`, `ExportBatch`,
`AirtableSyncRecord`, `AuditLog`.

**`repositories/*.py`** — one repository class per aggregate, each with `get`, `list`,
`create`, `update`, `delete` plus aggregate-specific query methods, e.g.:
- `class CaseRepository`: `get_by_dedupe_hash(self, hash_: str) -> SurplusCase | None`,
  `list_unresearched(self, limit: int) -> list[SurplusCase]`
- `class LeadRepository`: `list_qualified(self, county_slug: str | None) -> list[Lead]`,
  `list_unscored(self) -> list[Lead]`
- `class ExportRepository`: `record_batch(self, batch: ExportBatchDTO) -> ExportBatch`

**`seed.py`**
- module-level: `seed_dev_data(session: Session) -> None`

### 2.13 `surplus_ai/pipeline/`

**`orchestrator.py`**
- `class PipelineOrchestrator`
  - `run_full(self, county_slug: str | None, dry_run: bool) -> PipelineRunSummary`
  - `run_stage(self, stage: PipelineStage, context: PipelineRunContext) -> None`

**`stages.py`**
- `class PipelineStage(str, Enum)`: `ACQUIRE`, `PARSE`, `NORMALIZE`, `CLASSIFY`,
  `COMPLIANCE`, `RESEARCH`, `SCORE`, `QUALIFY`, `EXPORT`, `SYNC`, `CALLSHEET`

**`context.py`**
- `class PipelineRunContext(BaseModel)`: `run_id: UUID`, `county_slug: str | None`,
  `started_at: datetime`, `metrics: dict[str, int]`, `errors: list[str]`

**`scheduler.py`**
- `class PipelineScheduler`
  - `start(self) -> None` — registers APScheduler jobs: nightly ingestion sweep, daily
    call-sheet generation, hourly Airtable pull-sync.
  - `stop(self) -> None`

**`exceptions.py`**
- `PipelineError`, `StageFailedError`

### 2.14 `surplus_ai/dashboard/` (Phase 10)

**`app.py`**
- module-level: `main() -> None` — Streamlit entrypoint, page router.

**`pages/*.py`** — one `render() -> None` function per page (`overview`, `leads`,
`call_sheet`, `county_manager`, `compliance_status`, `analytics`), each reading through the
repository layer only (no direct SQL in dashboard code).

**`components/charts.py`, `tables.py`, `filters.py`** — pure rendering helper functions,
e.g. `render_pipeline_funnel(leads: list[LeadDTO]) -> None`,
`render_lead_table(leads: list[LeadDTO]) -> None`,
`county_filter_widget(counties: list[County]) -> str | None`.

### 2.15 `surplus_ai/utils/`

**`config.py`**
- `class Settings(BaseSettings)` — pydantic-settings root config (env-var + YAML layered).
- module-level: `get_settings() -> Settings` (cached singleton).

**`logging_config.py`**
- module-level: `configure_logging(settings: Settings) -> None` — structlog + stdlib
  logging bridge, JSON output, rotating file handler into `logs/`.

**`exceptions.py`**
- `class AppError(Exception)` — base of every custom exception in the codebase.

**`retry.py`**
- module-level: `with_retry(max_attempts: int, backoff: float) -> Callable` — tenacity
  decorator factory used by every external-call module.

**`rate_limiter.py`**
- `class TokenBucketRateLimiter`
  - `acquire(self) -> None`

**`file_utils.py`**
- module-level: `atomic_write(path: Path, content: bytes) -> None`
- module-level: `sha256_of_file(path: Path) -> str`

**`hashing.py`**
- module-level: `stable_row_hash(fields: dict) -> str`

**`money.py`**
- module-level: `to_decimal(value: str | float) -> Decimal`

**`dates.py`**
- module-level: `parse_flexible_date(value: str, fmt_hint: str | None) -> date | None`

**`strings.py`**
- module-level: `clean_whitespace(value: str) -> str`
- module-level: `strip_non_printable(value: str) -> str`

**`validation.py`**
- module-level shared Pydantic validators (e.g., `validate_state_code`, `validate_zip`).

**`secrets.py`**
- module-level: `get_secret(name: str) -> str` — reads from env, raises `AppError` if unset;
  no default vendor lock-in, but structured to swap in a secrets manager later.

---

## 5. Database Tables (PostgreSQL, SQLAlchemy + Alembic)

Postgres is the system of record for everything, including raw ingestion data and full
audit history. Airtable (§6) is a CRM-facing mirror of qualified leads only — never the
source of truth.

| Table | Purpose | Key Columns |
|---|---|---|
| `counties` | One row per onboarded county | `id (PK)`, `state`, `name`, `fips_code`, `source_type`, `source_url`, `parsing_profile_key`, `compliance_state_ref`, `publishing_frequency`, `is_active`, `created_at`, `updated_at` |
| `parsing_profile_versions` | Snapshot audit trail of county profile config | `id (PK)`, `county_id (FK)`, `version_hash`, `profile_json (JSONB)`, `created_at` |
| `ingestion_jobs` | One row per PDF ingestion run | `id (PK)`, `county_id (FK)`, `source_file_path`, `source_file_hash`, `started_at`, `completed_at`, `status`, `rows_extracted`, `rows_failed`, `error_message`, `triggered_by`, `created_at` |
| `raw_surplus_rows` | Untouched extracted rows | `id (PK)`, `ingestion_job_id (FK)`, `county_id (FK)`, `raw_data (JSONB)`, `row_hash`, `page_number`, `extraction_method`, `created_at` |
| `surplus_cases` | Normalized/deduped case records | `id (PK)`, `county_id (FK)`, `raw_row_id (FK)`, `case_number`, `parcel_id`, `property_address_raw`, `property_address_normalized (JSONB)`, `sale_date`, `judgment_amount`, `sale_amount`, `surplus_amount`, `distribution_deadline`, `status`, `dedupe_hash (UNIQUE w/ county_id)`, `created_at`, `updated_at` |
| `owners` | Classified owner records per case | `id (PK)`, `surplus_case_id (FK)`, `raw_name`, `owner_type`, `first_name`, `middle_name`, `last_name`, `suffix`, `entity_name`, `classification_confidence`, `classification_method`, `created_at` |
| `properties` | Property-level enrichment | `id (PK)`, `surplus_case_id (FK)`, `parcel_id`, `assessed_value`, `legal_description`, `property_type`, `last_researched_at`, `created_at`, `updated_at` |
| `compliance_evaluations` | State-rule evaluation per case | `id (PK)`, `surplus_case_id (FK)`, `state_rule_version`, `is_eligible`, `fee_cap_pct`, `earliest_contact_date`, `disclosures_required (JSONB)`, `evaluation_notes`, `evaluated_at` |
| `leads` | Qualified opportunities | `id (PK)`, `surplus_case_id (FK, UNIQUE)`, `owner_id (FK)`, `status`, `score`, `score_breakdown (JSONB)`, `assigned_user_id (FK, nullable)`, `airtable_record_id`, `qualified_at`, `created_at`, `updated_at` |
| `contacts` | Phone/email/mailing enrichment | `id (PK)`, `lead_id (FK)`, `contact_type`, `value`, `source`, `confidence`, `is_verified`, `created_at` |
| `research_results` | Raw provider responses | `id (PK)`, `surplus_case_id (FK)`, `provider`, `request_payload (JSONB)`, `response_payload (JSONB)`, `status`, `fetched_at` |
| `lead_scores` | Score history | `id (PK)`, `lead_id (FK)`, `score_version`, `total_score`, `factor_breakdown (JSONB)`, `scored_at` |
| `interactions` | CRM activity log | `id (PK)`, `lead_id (FK)`, `user_id (FK)`, `interaction_type`, `outcome`, `notes`, `occurred_at`, `created_at` |
| `deals` | Contract/claim tracking | `id (PK)`, `lead_id (FK, UNIQUE)`, `stage`, `contract_fee_pct`, `claim_amount`, `fee_amount`, `closed_at`, `created_at`, `updated_at` |
| `users` | Internal staff | `id (PK)`, `name`, `email`, `role`, `is_active`, `created_at` |
| `call_sheets` | Daily generated batches | `id (PK)`, `run_date`, `assigned_user_id (FK, nullable)`, `generated_at`, `lead_count` |
| `call_sheet_items` | Leads within a call sheet | `id (PK)`, `call_sheet_id (FK)`, `lead_id (FK)`, `priority_rank`, `called (bool)` |
| `export_batches` | CSV/Airtable export audit | `id (PK)`, `export_type`, `county_id (FK, nullable)`, `file_path`, `record_count`, `status`, `created_at` |
| `airtable_sync_records` | Local <-> Airtable ID mapping | `id (PK)`, `local_table`, `local_id`, `airtable_table_name`, `airtable_record_id`, `last_synced_at`, `sync_hash` |
| `audit_logs` | Full change audit trail | `id (PK)`, `actor`, `action`, `entity_type`, `entity_id`, `before (JSONB)`, `after (JSONB)`, `created_at` |

All tables use UUID primary keys, `created_at`/`updated_at` timestamps (UTC), and are
managed exclusively through Alembic migrations — no manual schema edits.

---

## 6. Airtable Tables (CRM-facing mirror)

Airtable is where the recovery team lives day-to-day. It only ever receives **qualified,
compliance-cleared, individual-owner leads** — never raw ingestion data.

| Airtable Table | Purpose | Key Fields | Linked To |
|---|---|---|---|
| **Counties** | Reference list of active counties | County Name, State, Status, Last Ingested | Leads |
| **Leads** | One record per qualified opportunity | Owner Name, County, Case Number, Surplus Amount, Sale Date, Score, Stage, Assigned To, Earliest Contact Date, Airtable-Sync Hash | Contacts, Interactions, Deals, Counties, Staff |
| **Contacts** | Phone/email/mailing info per lead | Type, Value, Source, Verified | Leads |
| **Interactions** | Full call/email/note history | Type, Outcome, Notes, Occurred At, Logged By | Leads, Staff |
| **Deals** | Contract/claim pipeline | Stage, Fee %, Claim Amount, Fee Amount, Closed At | Leads |
| **Call Sheets** | Daily generated call batches | Run Date, Assigned To, Lead Count | Leads (via linked items), Staff |
| **Staff** | Internal team directory | Name, Email, Role, Active | Leads, Interactions, Call Sheets |
| **Compliance Reference** | Read-only synced view of state rules for staff visibility | State, Max Fee %, Waiting Period, Required Disclosures | Leads (via County -> State) |

Sync direction: Postgres -> Airtable is authoritative for `Leads`/`Contacts`/`Deals` field
values computed by the pipeline (score, compliance, enrichment). Airtable -> Postgres is
authoritative for `Stage` and `Interactions` created directly by staff in the Airtable UI —
`AirtableSyncEngine.pull_stage_changes()` reconciles those back. Conflicts are resolved by
"most recently modified wins," logged to `audit_logs`.

---

## 7. External APIs / Integrations

| API / Service | Purpose | Auth | Notes |
|---|---|---|---|
| **Airtable REST API** | CRM sync target | API token (env var) | Rate limit 5 req/s/base; `AirtableRateLimiter` enforces this. |
| **County open-data APIs (Socrata)** | Official property record research where available | API token optional (higher rate limit) | Config-driven per county in `research/providers.yaml`. |
| **County GIS/ArcGIS REST FeatureServer** | Parcel/property lookups where county exposes GIS | Usually none | Config-driven layer URL. |
| **Generic county REST APIs** | One-off official APIs that don't fit Socrata/ArcGIS | Varies, config-driven | `ConfigurableRESTProvider` template-based. |
| **USPS Web Tools API** | Address standardization/validation | USPS User ID (env var) | Required before any mailing is sent. |
| **US Census Geocoder** (default) / **Google Maps Geocoding API** (config-swappable) | Geocoding for owner-location scoring factor | None / API key | Provider chosen via `research/providers.yaml`. |
| **Skip-trace vendor API** (vendor selected during Phase 4 — e.g., BatchData, Endato, IDI/TLOxp; interface is vendor-agnostic) | Locate current phone/email/address for owners | API key (env var) | Always behind `AbstractSkipTraceProvider`; vendor swap = config + one adapter class, no pipeline changes. |
| **Phone validation** (Twilio Lookup or NumVerify, config-swappable) | Validate/normalize phone numbers before dialing | API key | Used before adding a lead to a call sheet. |
| **LLM API (Anthropic Claude)** | Assisted extraction for irregular PDF layouts only | API key (env var) | Output always routed to human-review queue; never auto-trusted for compliance-critical fields. Explicitly optional, config flag `LLM_ASSIST_ENABLED`. |
| **SMTP / transactional email (e.g. SendGrid)** | Internal notifications: daily call-sheet digest, ingestion failures | API key | Outbound only, no inbound. |
| **IMAP** | Email-based county list ingestion (`email_ingest.py`) | Mailbox credentials (env var) | Only for counties that distribute lists via email. |
| **SFTP** | Bulk-download ingestion for counties that publish via SFTP | Credentials (env var) | `sftp_downloader.py`. |

No county property records are ever scraped when an official API or documented bulk-download
exists — per CLAUDE.md, `research/providers/manual_lookup_provider.py` is the explicit
fallback that queues a case for human research rather than writing a brittle scraper.

There is **no internal REST API** in the initial architecture (backend + CLI only, per
PROJECT.md). If the Streamlit dashboard later needs more than direct repository access, a
`surplus_ai/api/` FastAPI layer can be added as a Phase 11+ option — not part of this
design.

---

## 8. Python Dependencies (`pyproject.toml`)

**Runtime**
| Package | Purpose |
|---|---|
| `typer` | CLI framework |
| `pydantic` (v2) | Data validation / schemas |
| `pydantic-settings` | Layered config + env var loading |
| `SQLAlchemy` (2.x) | ORM |
| `alembic` | DB migrations |
| `psycopg[binary]` | PostgreSQL driver |
| `pdfplumber` | Table/text PDF extraction |
| `camelot-py[cv]` | Table extraction fallback (needs Ghostscript) |
| `pytesseract` | OCR extraction |
| `Pillow` | Image handling for OCR |
| `pdf2image` | PDF page rasterization (needs Poppler) |
| `usaddress` | US address parsing |
| `phonenumbers` | Phone number normalization/validation |
| `python-dateutil` | Flexible date parsing |
| `rapidfuzz` | Fuzzy string matching for dedupe |
| `pyairtable` | Airtable API client |
| `httpx` | HTTP client (research providers, skip trace, etc.) |
| `tenacity` | Retry/backoff decorators |
| `structlog` | Structured logging |
| `APScheduler` | Recurring job scheduling |
| `pandas` | CSV export / tabular manipulation |
| `python-dotenv` | Local `.env` loading |
| `scikit-learn` | Optional ML owner-type classifier |
| `Jinja2` | Report templates |
| `email-validator` | Email field validation |
| `anthropic` | Optional LLM-assisted extraction |

**Dev / Test**
| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-cov` | Coverage reporting |
| `pytest-mock` | Mocking |
| `factory-boy` | Test data factories |
| `faker` | Synthetic test data |
| `respx` | HTTP mocking for `httpx` |
| `freezegun` | Time mocking (waiting-period/compliance tests) |
| `ruff` | Linting |
| `black` | Formatting |
| `mypy` | Static typing |
| `pre-commit` | Git hook enforcement of the above |
| `types-requests`, `types-python-dateutil` | mypy stub packages |

---

## 9. External (non-Python) Libraries / Binaries

| Tool | Purpose | Required By |
|---|---|---|
| Ghostscript | Table detection backend | `camelot-py` |
| Poppler (`pdftoppm`, `pdfinfo`) | PDF rasterization | `pdf2image` |
| Tesseract OCR engine | OCR of scanned PDFs | `pytesseract` |
| PostgreSQL 15+ | Primary datastore | `database/` |
| Docker / docker-compose | Local dev environment (Postgres + app) | dev tooling |

`libpostal` is deliberately **not** used — its native build/runtime footprint is heavy for
marginal accuracy gain over `usaddress` at this stage. If international or highly malformed
addresses become common, it can be introduced later behind `AddressNormalizer` without
touching any caller.

---

## 10. Implementation Roadmap

Each phase is complete (tests + logging + docs + error handling, per CLAUDE.md) before the
next starts. No phase modifies modules outside its own boundary.

**Phase 0 — Foundations**
Repo scaffold, `pyproject.toml`, `utils/config.py`, `utils/logging_config.py`, base
exception hierarchy, Postgres + Alembic wiring, `docker-compose.yml`, CI (lint/type/test),
`pre-commit`. Deliverable: `surplusai db init` runs against an empty Postgres and creates
all tables in §5.

**Phase 1 — Parser + Normalizer (single pilot county)**
`CountyParsingProfile` model + loader, `TableExtractor`/`TextExtractor`/`OCRExtractor`,
`ColumnMapper`, `RowBuilder`, full `normalizer/` package. Deliverable: one real county PDF
goes in, a list of `CanonicalSurplusRecord` comes out, fully tested against a fixture PDF.

**Phase 2 — Classifier**
`entity_keywords.yaml`, `RuleBasedClassifier`, `OwnerTypeClassifier`. Deliverable: company
rows are correctly excluded, individuals correctly kept, with confidence scores.

**Phase 3 — Compliance Engine (pilot state)**
`StateComplianceRules` model + `compliance/states/<pilot>.yaml`, `ComplianceEngine`,
waiting-period/fee-cap/disclosure logic. Deliverable: every case gets an eligibility +
earliest-contact-date verdict, driven entirely by YAML.

**Phase 4 — Research & Enrichment**
`AbstractPropertyRecordProvider` + `ProviderRegistry`, Socrata/ArcGIS/configurable-REST
providers for counties with official APIs, `ManualLookupProvider` fallback queue, USPS
address validation, geocoding, one skip-trace vendor integration, phone validation.
Deliverable: leads carry verified mailing address + at least one contactability signal.

**Phase 5 — Scoring**
`scoring/weights.yaml`, six factor classes, `ScoringEngine`. Deliverable: every qualified
lead has a reproducible, explainable score.

**Phase 6 — CRM + Airtable Sync**
Airtable base provisioned to match §6, `AirtableClient`, `schema_mapping.yaml`,
`AirtableSyncEngine` (push + pull), `CRMRepository`, `ActivityLogger`. Deliverable:
qualified leads appear in Airtable within one sync cycle; stage edits in Airtable flow back
to Postgres.

**Phase 7 — Reports**
`CSVExporter`, `CallSheetGenerator`, `SummaryReportGenerator`. Deliverable: `surplusai
callsheet generate` produces a ranked daily call list from real pipeline data.

**Phase 8 — Pipeline Orchestration + Scheduler**
`PipelineOrchestrator`, `PipelineScheduler` (nightly ingest, daily call sheet, hourly
Airtable pull). Deliverable: `surplusai pipeline run` executes acquisition through
call-sheet generation unattended, with per-stage metrics and audit logging.

**Phase 9 — Multi-County Onboarding**
Onboard 3–5 additional counties using only new YAML profiles (no code changes) to prove the
config-driven contract in PROJECT.md holds. Deliverable: `docs/runbooks/onboarding_a_county.md`
validated end-to-end by someone who didn't write the parser.

**Phase 10 — Streamlit Dashboard**
`dashboard/app.py` + pages, read-only against the repository layer. Deliverable: pipeline
funnel, lead table, call sheet view, county/compliance status pages.

**Phase 11 — Hardening**
Observability (structured logs -> log aggregation), alerting on ingestion job failures,
automated Postgres backups (`scripts/backup_db.sh` on a schedule), secrets rotation runbook,
security review of all credential handling, load-testing the parser against the largest
known county PDF.

**Phase 12 — Scale-Out**
ML owner-type classifier trained on accumulated labeled data (replacing/augmenting rules),
advanced dedupe across counties/years, additional states' compliance profiles, analytics
pages, optional FastAPI layer if the dashboard outgrows direct repository access.
