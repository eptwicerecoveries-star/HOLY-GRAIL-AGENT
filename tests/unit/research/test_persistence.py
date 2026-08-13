from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import ResearchStatus
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.models import PropertyLookupQuery
from surplus_ai.research.persistence import ResearchResultWriter, parse_outcome_dict


def test_persist_success_fixture(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    outcome = parse_outcome_dict(outcome_fixtures["success"])
    row = ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture_open_data",
        query=sample_query,
        outcome=outcome,
    )
    assert row.status is ResearchStatus.SUCCESS
    assert row.request_payload["schema_version"] == 1
    assert row.response_payload["schema_version"] == 1
    assert row.response_payload["found"] is True
    evidence = row.response_payload["evidence"][0]
    assert evidence["original_value"]
    assert evidence["normalized_value"]
    assert evidence["source"]
    assert evidence["retrieved_at"]
    assert "confidence" in evidence
    assert evidence["method"]
    assert "requires_human_verification" in evidence


def test_persist_not_found_and_error(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    writer = ResearchResultWriter(session)
    not_found = writer.persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["not_found"]),
    )
    error = writer.persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["error"]),
    )
    assert not_found.status is ResearchStatus.NOT_FOUND
    assert error.status is ResearchStatus.ERROR


def test_secrets_redacted_in_persisted_payload(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    row = ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["secrets_must_redact"]),
    )
    raw = row.response_payload["raw_response"]
    assert raw["api_key"] == "[REDACTED]"
    assert raw["Authorization"] == "[REDACTED]"
    assert "SUPER_SECRET" not in str(row.response_payload)
    assert "SECRET_TOKEN" not in str(row.response_payload)


def test_ambiguous_forces_human_review_flag(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    row = ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["ambiguous_identity"]),
    )
    assert row.response_payload["requires_human_review"] is True


def test_persist_does_not_create_contacts_leads_properties_or_compliance(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    before_status = sample_case.status
    ResearchResultWriter(session).persist(
        surplus_case_id=sample_case.id,
        provider_name="fixture",
        query=sample_query,
        outcome=parse_outcome_dict(outcome_fixtures["success"]),
    )
    session.refresh(sample_case)
    assert sample_case.status is before_status
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0
    assert session.scalar(select(func.count()).select_from(ComplianceEvaluation)) == 0
    assert session.scalar(select(func.count()).select_from(ResearchResult)) == 1


def test_append_only_idempotency(
    session: Session,
    sample_case: SurplusCase,
    sample_query: PropertyLookupQuery,
    outcome_fixtures: dict[str, Any],
) -> None:
    writer = ResearchResultWriter(session)
    outcome = parse_outcome_dict(outcome_fixtures["not_found"])
    writer.persist(
        surplus_case_id=sample_case.id,
        provider_name="manual_lookup",
        query=sample_query,
        outcome=outcome,
    )
    writer.persist(
        surplus_case_id=sample_case.id,
        provider_name="manual_lookup",
        query=sample_query,
        outcome=outcome,
    )
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 2
