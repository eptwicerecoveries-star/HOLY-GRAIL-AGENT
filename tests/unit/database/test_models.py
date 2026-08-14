from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from surplus_ai.database.base import Base
from surplus_ai.database.models import (
    AirtableSyncRecord,
    AuditLog,
    CallSheet,
    CallSheetItem,
    ComplianceEvaluation,
    Contact,
    County,
    Deal,
    DocumentColumnMapping,
    ExportBatch,
    IngestionJob,
    Interaction,
    Lead,
    LeadScore,
    Owner,
    ParsedDocument,
    ParsingProfileVersion,
    Property,
    RawSurplusRow,
    ResearchResult,
    SurplusCase,
    User,
)
from surplus_ai.database.models.enums import (
    ClassificationMethod,
    ContactType,
    CountySourceType,
    DealStage,
    ExportStatus,
    ExportType,
    ExtractionMethod,
    IngestionJobStatus,
    InteractionType,
    LeadStatus,
    MappingMethodType,
    OwnerType,
    PdfType,
    PublishingFrequency,
    ResearchStatus,
    SurplusCaseStatus,
    SurplusSourceType,
    UserRole,
)

EXPECTED_TABLES = {
    "airtable_sync_records",
    "audit_logs",
    "call_sheet_items",
    "call_sheets",
    "compliance_evaluations",
    "contacts",
    "counties",
    "deals",
    "document_column_mappings",
    "export_batches",
    "ingestion_jobs",
    "interactions",
    "lead_scores",
    "leads",
    "owners",
    "parse_review_items",
    "parsed_documents",
    "parsing_profile_versions",
    "properties",
    "raw_surplus_rows",
    "research_results",
    "research_review_items",
    "surplus_cases",
    "users",
}


def _county(**overrides: object) -> County:
    defaults: dict[str, object] = {
        "slug": "demo-county",
        "name": "Demo County",
        "state": "XX",
        "source_type": CountySourceType.BULK_DOWNLOAD,
        "parsing_profile_key": "demo-county",
        "compliance_state_ref": "XX",
        "publishing_frequency": PublishingFrequency.MONTHLY,
    }
    defaults.update(overrides)
    return County(**defaults)


def _case(county: County, **overrides: object) -> SurplusCase:
    defaults: dict[str, object] = {
        "county_id": county.id,
        "case_number": "CASE-1",
        "surplus_amount": Decimal("1234.56"),
        "dedupe_hash": "hash-1",
    }
    defaults.update(overrides)
    return SurplusCase(**defaults)


def test_all_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 24


def test_schema_matches_models(engine, _schema) -> None:  # type: ignore[no-untyped-def]
    assert EXPECTED_TABLES.issubset(set(inspect(engine).get_table_names()))


def test_uuid_primary_key_is_generated(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()

    assert isinstance(county.id, uuid.UUID)


def test_timestamps_are_populated(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    session.refresh(county)

    assert county.created_at is not None
    assert county.updated_at is not None
    assert county.created_at.tzinfo is not None


def test_county_state_slug_is_unique(session: Session) -> None:
    session.add(_county())
    session.flush()
    session.add(_county())

    with pytest.raises(IntegrityError):
        session.flush()


def test_same_slug_allowed_in_different_states(session: Session) -> None:
    session.add(_county(state="XX"))
    session.add(_county(state="YY"))
    session.flush()

    assert session.scalars(select(County)).all().__len__() == 2


def test_surplus_case_dedupe_hash_unique_per_county(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()

    session.add(_case(county))
    session.flush()
    session.add(_case(county, case_number="CASE-2"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_same_dedupe_hash_allowed_across_counties(session: Session) -> None:
    first = _county(slug="a", state="AA")
    second = _county(slug="b", state="BB")
    session.add_all([first, second])
    session.flush()

    session.add(_case(first))
    session.add(_case(second))
    session.flush()

    assert len(session.scalars(select(SurplusCase)).all()) == 2


def test_lead_is_one_to_one_with_case(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()

    session.add(Lead(surplus_case_id=case.id))
    session.flush()
    session.add(Lead(surplus_case_id=case.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_decimal_amounts_round_trip_exactly(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(
        county,
        surplus_amount=Decimal("99999999.99"),
        judgment_amount=Decimal("0.01"),
        sale_amount=Decimal("12345.67"),
    )
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.surplus_amount == Decimal("99999999.99")
    assert case.judgment_amount == Decimal("0.01")
    assert case.sale_amount == Decimal("12345.67")


def test_jsonb_columns_round_trip(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    payload = {"street": "1 Main St", "city": "Springfield", "nested": {"zip": "00001"}}
    case = _case(county, property_address_normalized=payload)
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.property_address_normalized == payload


def test_enum_values_persist_as_declared(session: Session) -> None:
    county = _county(source_type=CountySourceType.SFTP)
    session.add(county)
    session.flush()

    stored = session.execute(
        select(County.__table__.c.source_type).where(County.__table__.c.id == county.id)
    ).scalar_one()
    assert stored == "sftp"


def test_invalid_enum_value_is_rejected(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()

    owner = Owner(surplus_case_id=case.id, raw_name="X", owner_type="not_a_real_type")  # type: ignore[arg-type]
    session.add(owner)
    with pytest.raises(DataError):
        session.flush()


def test_cascade_delete_removes_dependent_rows(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()
    session.add(Owner(surplus_case_id=case.id, raw_name="Jane Doe"))
    session.flush()

    session.delete(case)
    session.flush()

    assert session.scalars(select(Owner)).all() == []


def test_set_null_on_user_delete_preserves_lead(session: Session) -> None:
    user = User(name="Agent", email="agent@example.invalid", role=UserRole.AGENT)
    county = _county()
    session.add_all([user, county])
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()
    lead = Lead(surplus_case_id=case.id, assigned_user_id=user.id)
    session.add(lead)
    session.flush()

    session.delete(user)
    session.flush()
    session.expire(lead)

    assert lead.assigned_user_id is None


def test_user_email_is_unique(session: Session) -> None:
    session.add(User(name="A", email="dup@example.invalid"))
    session.flush()
    session.add(User(name="B", email="dup@example.invalid"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_surplus_amount_may_be_null(session: Session) -> None:
    """A case with no published surplus must be storable without inventing a figure.

    Counties commonly publish a sale price, a winning bid and an assessment while never
    stating a surplus. The difference is not the surplus -- liens, fees and costs come out
    first -- so the column is nullable and consumers must read NULL as "not a qualified
    lead" rather than as zero.
    """
    county = _county()
    session.add(county)
    session.flush()
    case = SurplusCase(county_id=county.id, case_number="C", dedupe_hash="h", surplus_amount=None)
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.surplus_amount is None


def test_full_object_graph_persists(session: Session) -> None:
    county = _county()
    user = User(name="Agent", email="graph@example.invalid")
    session.add_all([county, user])
    session.flush()

    job = IngestionJob(
        county_id=county.id,
        source_file_path="/data/raw_pdfs/demo.pdf",
        source_file_hash="a" * 64,
        status=IngestionJobStatus.COMPLETED,
        rows_extracted=1,
    )
    session.add(job)
    session.flush()

    raw = RawSurplusRow(
        ingestion_job_id=job.id,
        county_id=county.id,
        raw_data={"Owner": "Jane Doe"},
        row_hash="b" * 64,
        page_number=1,
        extraction_method=ExtractionMethod.TABLE,
    )
    session.add(raw)
    session.flush()

    case = _case(county, raw_row_id=raw.id, sale_date=date(2025, 1, 15))
    session.add(case)
    session.flush()

    owner = Owner(
        surplus_case_id=case.id,
        raw_name="Jane Doe",
        owner_type=OwnerType.INDIVIDUAL,
        first_name="Jane",
        last_name="Doe",
        classification_confidence=0.97,
        classification_method=ClassificationMethod.RULE,
    )
    prop = Property(surplus_case_id=case.id, parcel_id="P-1", assessed_value=Decimal("250000.00"))
    evaluation = ComplianceEvaluation(
        surplus_case_id=case.id,
        state_rule_version="v1",
        is_eligible=True,
        fee_cap_pct=20.0,
        earliest_contact_date=date(2025, 4, 15),
        disclosures_required=["contract_notice"],
    )
    research = ResearchResult(
        surplus_case_id=case.id,
        provider="socrata",
        request_payload={"parcel": "P-1"},
        response_payload={"found": True},
        status=ResearchStatus.SUCCESS,
    )
    session.add_all([owner, prop, evaluation, research])
    session.flush()

    lead = Lead(
        surplus_case_id=case.id,
        owner_id=owner.id,
        status=LeadStatus.QUALIFIED,
        score=88.5,
        score_breakdown={"surplus_amount": 40.0},
        assigned_user_id=user.id,
        qualified_at=datetime.now(UTC),
    )
    session.add(lead)
    session.flush()

    session.add_all(
        [
            Contact(
                lead_id=lead.id,
                contact_type=ContactType.PHONE,
                value="+15555550123",
                source="skip_trace",
                confidence=0.8,
                is_verified=True,
            ),
            LeadScore(lead_id=lead.id, score_version="v1", total_score=88.5),
            Interaction(
                lead_id=lead.id,
                user_id=user.id,
                interaction_type=InteractionType.CALL,
                outcome="voicemail",
            ),
            Deal(lead_id=lead.id, stage=DealStage.CONTRACT_SENT, contract_fee_pct=20.0),
        ]
    )
    sheet = CallSheet(run_date=date(2025, 5, 1), assigned_user_id=user.id, lead_count=1)
    session.add(sheet)
    session.flush()
    session.add(CallSheetItem(call_sheet_id=sheet.id, lead_id=lead.id, priority_rank=1))
    session.add(
        ExportBatch(
            export_type=ExportType.CSV,
            county_id=county.id,
            file_path="exports/csv/demo.csv",
            record_count=1,
            status=ExportStatus.COMPLETED,
        )
    )
    session.add(
        AirtableSyncRecord(
            local_table="leads",
            local_id=lead.id,
            airtable_table_name="Leads",
            airtable_record_id="rec123",
        )
    )
    session.add(
        AuditLog(
            actor="cli",
            action="lead.qualified",
            entity_type="lead",
            entity_id=lead.id,
            after={"status": "qualified"},
        )
    )
    session.flush()
    session.expire_all()

    reloaded = session.scalar(select(SurplusCase).where(SurplusCase.id == case.id))
    assert reloaded is not None
    assert reloaded.lead is not None
    assert reloaded.lead.deal is not None
    assert reloaded.lead.contacts[0].value == "+15555550123"
    assert reloaded.owners[0].owner_type is OwnerType.INDIVIDUAL
    assert reloaded.compliance_evaluations[0].disclosures_required == ["contract_notice"]
    assert reloaded.county.name == "Demo County"
    assert reloaded.status is SurplusCaseStatus.NEW


def test_parsing_profile_version_unique_per_county(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()

    session.add(
        ParsingProfileVersion(county_id=county.id, version_hash="v1", profile_json={"a": 1})
    )
    session.flush()
    session.add(
        ParsingProfileVersion(county_id=county.id, version_hash="v1", profile_json={"a": 2})
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_airtable_sync_record_remote_id_unique(session: Session) -> None:
    session.add(
        AirtableSyncRecord(
            local_table="leads",
            local_id=uuid.uuid4(),
            airtable_table_name="Leads",
            airtable_record_id="recDUP",
        )
    )
    session.flush()
    session.add(
        AirtableSyncRecord(
            local_table="leads",
            local_id=uuid.uuid4(),
            airtable_table_name="Leads",
            airtable_record_id="recDUP",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_call_sheet_item_unique_per_sheet(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()
    lead = Lead(surplus_case_id=case.id)
    sheet = CallSheet(run_date=date(2025, 5, 2))
    session.add_all([lead, sheet])
    session.flush()

    session.add(CallSheetItem(call_sheet_id=sheet.id, lead_id=lead.id, priority_rank=1))
    session.flush()
    session.add(CallSheetItem(call_sheet_id=sheet.id, lead_id=lead.id, priority_rank=2))

    with pytest.raises(IntegrityError):
        session.flush()


def test_deal_is_one_to_one_with_lead(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county)
    session.add(case)
    session.flush()
    lead = Lead(surplus_case_id=case.id)
    session.add(lead)
    session.flush()

    session.add(Deal(lead_id=lead.id))
    session.flush()
    session.add(Deal(lead_id=lead.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_surplus_provenance_round_trips(session: Session) -> None:
    """A null surplus must record *why* it is null, not merely that it is.

    "The county published none" and "several columns rivalled each other and we refused to
    guess" are different facts that lead to different follow-up, so the reason is stored.
    """
    county = _county()
    session.add(county)
    session.flush()
    case = _case(
        county,
        surplus_amount=None,
        surplus_is_explicit=False,
        surplus_source=SurplusSourceType.AMBIGUOUS,
        surplus_source_column=None,
    )
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.surplus_amount is None
    assert case.surplus_source is SurplusSourceType.AMBIGUOUS
    assert case.surplus_is_explicit is False


def test_surplus_source_defaults_to_absent(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    case = _case(county, surplus_amount=None)
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.surplus_source is SurplusSourceType.ABSENT


def test_every_published_money_column_persists_separately(session: Session) -> None:
    """Marion's five money figures must survive as five distinct values."""
    county = _county()
    session.add(county)
    session.flush()
    case = _case(
        county,
        surplus_amount=Decimal("0.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.COUNTY_CONFIG,
        surplus_source_column="Remaining Overbid",
        face_value_amount=Decimal("5118.74"),
        overbid_amount=Decimal("3203.00"),
        purchase_amount=Decimal("8321.74"),
        refunded_amount=Decimal("3203.00"),
        remaining_amount=Decimal("0.00"),
    )
    session.add(case)
    session.flush()
    session.expire(case)

    assert case.face_value_amount == Decimal("5118.74")
    assert case.overbid_amount == Decimal("3203.00")
    assert case.purchase_amount == Decimal("8321.74")
    assert case.refunded_amount == Decimal("3203.00")
    assert case.remaining_amount == Decimal("0.00")
    assert case.surplus_amount == Decimal("0.00")
    assert case.surplus_source_column == "Remaining Overbid"


def test_document_column_mapping_records_its_evidence(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    document = ParsedDocument(
        county_id=county.id,
        source_file_path="/data/x.pdf",
        source_file_sha256="c" * 64,
        page_count=1,
        pdf_type=PdfType.SEARCHABLE,
        ocr_required=False,
    )
    session.add(document)
    session.flush()

    session.add(
        DocumentColumnMapping(
            parsed_document_id=document.id,
            table_index=0,
            column_position=3,
            original_header="ACCT #",
            canonical_field="parcel_id",
            method=MappingMethodType.EXACT_ALIAS,
            confidence=0.95,
            evidence="exact match in the global alias registry",
        )
    )
    session.flush()

    stored = session.scalars(select(DocumentColumnMapping)).one()
    assert stored.original_header == "ACCT #"
    assert stored.canonical_field == "parcel_id"
    assert stored.method is MappingMethodType.EXACT_ALIAS


def test_column_mapping_is_unique_per_position(session: Session) -> None:
    county = _county()
    session.add(county)
    session.flush()
    document = ParsedDocument(
        county_id=county.id,
        source_file_path="/data/y.pdf",
        source_file_sha256="d" * 64,
        page_count=1,
        pdf_type=PdfType.SEARCHABLE,
        ocr_required=False,
    )
    session.add(document)
    session.flush()

    for _ in range(2):
        session.add(
            DocumentColumnMapping(
                parsed_document_id=document.id,
                table_index=0,
                column_position=1,
                original_header="Owner",
                canonical_field="owner_name",
                method=MappingMethodType.EXACT_ALIAS,
                confidence=0.95,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()
