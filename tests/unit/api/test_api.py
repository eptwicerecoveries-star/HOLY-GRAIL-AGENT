"""Productization P1 API tests — local read-only FastAPI shell."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    ContactType,
    CountySourceType,
    LeadStatus,
    PublishingFrequency,
    ResearchReviewReason,
    ResearchStatus,
    ReviewStatus,
    SurplusCaseStatus,
    SurplusSourceType,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase


def _seed_county(session: Session, slug: str = "api-p1") -> County:
    county = County(
        slug=slug,
        name="API County",
        state="MD",
        fips_code="24998",
        source_type=CountySourceType.MANUAL_UPLOAD,
        parsing_profile_key="md-api",
        compliance_state_ref="MD",
        publishing_frequency=PublishingFrequency.IRREGULAR,
        is_active=True,
    )
    session.add(county)
    session.flush()
    return county


def _seed_case(
    session: Session,
    county: County,
    *,
    dedupe: str,
    parcel: str = "P-1",
) -> SurplusCase:
    case = SurplusCase(
        county_id=county.id,
        parcel_id=parcel,
        property_address_raw="1 MAIN ST",
        sale_date=date(2024, 1, 1),
        surplus_amount=Decimal("1000.00"),
        surplus_is_explicit=True,
        surplus_source=SurplusSourceType.EXPLICIT,
        status=SurplusCaseStatus.NORMALIZED,
        dedupe_hash=dedupe,
    )
    session.add(case)
    session.flush()
    return case


def _seed_lead(session: Session, case: SurplusCase) -> Lead:
    lead = Lead(surplus_case_id=case.id, status=LeadStatus.QUALIFIED, score=0.5)
    session.add(lead)
    session.flush()
    return lead


def test_health_ok(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
    blob = response.text.lower()
    assert "postgresql" not in blob
    assert "traceback" not in blob
    assert "password" not in blob


def test_status_safe(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["application"] == "surplus-ai"
    assert body["database_reachable"] is True
    assert body["local_only"] is True
    assert body["authentication"] == "session_cookie"
    assert "INTERNET-FACING" in body["warning"]
    blob = response.text.lower()
    assert "postgresql+psycopg" not in blob
    assert "password" not in blob
    assert "surplus_ai_dev" not in blob
    assert "traceback" not in blob


def test_cases_list_detail_and_404(api_client: TestClient, session: Session) -> None:
    county = _seed_county(session)
    case = _seed_case(session, county, dedupe="api-case-1")
    _seed_lead(session, case)

    listed = api_client.get("/api/v1/cases")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) >= 1
    match = next(r for r in rows if r["id"] == str(case.id))
    assert match["parcel_id"] == "P-1"
    assert match["has_lead"] is True
    assert match["county_state"] == "MD"
    assert "property_address_raw" not in match

    detail = api_client.get(f"/api/v1/cases/{case.id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == str(case.id)

    missing = api_client.get(f"/api/v1/cases/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_cases_deterministic_order(api_client: TestClient, session: Session) -> None:
    county = _seed_county(session, slug="api-order")
    older = _seed_case(session, county, dedupe="api-ord-old", parcel="OLD")
    newer = _seed_case(session, county, dedupe="api-ord-new", parcel="NEW")
    # Force created_at ordering when DB defaults are equal within the same flush.
    session.execute(
        SurplusCase.__table__.update()
        .where(SurplusCase.id == older.id)
        .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    session.execute(
        SurplusCase.__table__.update()
        .where(SurplusCase.id == newer.id)
        .values(created_at=datetime(2024, 1, 1, tzinfo=UTC))
    )
    session.flush()

    rows = api_client.get("/api/v1/cases?limit=50").json()
    ids = [r["id"] for r in rows if r["id"] in {str(older.id), str(newer.id)}]
    assert ids == [str(newer.id), str(older.id)]


def test_leads_list_detail_404(api_client: TestClient, session: Session) -> None:
    county = _seed_county(session, slug="api-lead")
    case = _seed_case(session, county, dedupe="api-lead-1")
    lead = _seed_lead(session, case)
    session.add(
        Contact(
            lead_id=lead.id,
            contact_type=ContactType.PHONE,
            value="+15555550100",
            source="seed",
            is_verified=False,
        )
    )
    session.flush()

    listed = api_client.get("/api/v1/leads")
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["id"] == str(lead.id))
    assert row["contact_count"] == 1
    assert row["surplus_case_id"] == str(case.id)

    detail = api_client.get(f"/api/v1/leads/{lead.id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == LeadStatus.QUALIFIED.value

    assert api_client.get(f"/api/v1/leads/{uuid.uuid4()}").status_code == 404


def test_reviews_metadata_no_raw_payload(api_client: TestClient, session: Session) -> None:
    county = _seed_county(session, slug="api-rev")
    case = _seed_case(session, county, dedupe="api-rev-1")
    result = ResearchResult(
        surplus_case_id=case.id,
        provider="manual_lookup",
        status=ResearchStatus.SUCCESS,
        response_payload={"secret": "do-not-leak"},
    )
    session.add(result)
    session.flush()
    review = ResearchReviewItem(
        surplus_case_id=case.id,
        research_result_id=result.id,
        provider="manual_lookup",
        reason=ResearchReviewReason.MANUAL_RESEARCH_REQUIRED,
        status=ReviewStatus.PENDING,
    )
    session.add(review)
    session.flush()

    listed = api_client.get("/api/v1/research/reviews")
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["id"] == str(review.id))
    assert row["provider"] == "manual_lookup"
    assert "raw_response" not in row
    assert "response_payload" not in row
    assert "secret" not in listed.text

    detail = api_client.get(f"/api/v1/research/reviews/{review.id}")
    assert detail.status_code == 200
    assert "raw_response" not in detail.json()
    assert "response_payload" not in detail.json()
    assert "secret" not in detail.text


def test_contacts_omit_value(api_client: TestClient, session: Session) -> None:
    county = _seed_county(session, slug="api-ct")
    case = _seed_case(session, county, dedupe="api-ct-1")
    lead = _seed_lead(session, case)
    contact = Contact(
        lead_id=lead.id,
        contact_type=ContactType.EMAIL,
        value="secret@example.com",
        source="seed",
        confidence=0.9,
        is_verified=False,
    )
    session.add(contact)
    session.flush()

    listed = api_client.get("/api/v1/contacts")
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["id"] == str(contact.id))
    assert "value" not in row
    assert "secret@example.com" not in listed.text
    assert row["contact_type"] == "email"
    assert row["lead_id"] == str(lead.id)

    detail = api_client.get(f"/api/v1/contacts/{contact.id}")
    assert detail.status_code == 200
    assert "value" not in detail.json()
    assert "secret@example.com" not in detail.text


def test_pagination_bounds(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/cases?limit=50").status_code == 200
    assert api_client.get("/api/v1/cases?limit=100").status_code == 200
    bad_max = api_client.get("/api/v1/cases?limit=101")
    assert bad_max.status_code == 422
    assert bad_max.json()["code"] == "validation_error"
    assert api_client.get("/api/v1/cases?limit=0").status_code == 422
    assert api_client.get("/api/v1/cases?offset=-1").status_code == 422
    assert "traceback" not in bad_max.text.lower()


def test_write_methods_rejected(api_client: TestClient) -> None:
    for path in (
        "/api/v1/cases",
        "/api/v1/leads",
        "/api/v1/contacts",
        "/api/v1/research/reviews",
    ):
        assert api_client.post(path, json={}).status_code == 405
        assert api_client.delete(path).status_code == 405
        assert api_client.put(path, json={}).status_code == 405
        assert api_client.patch(path, json={}).status_code == 405


def test_routes_write_surface_is_auth_only() -> None:
    app = create_app()
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    allowed_write_paths = {"/api/v1/auth/login", "/api/v1/auth/logout"}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        writes = methods & write_methods
        if not writes:
            continue
        assert route.path in allowed_write_paths, f"unexpected write on {route.path}: {writes}"
        assert writes == {"POST"}


def test_no_provider_or_skip_trace_imports_in_routes() -> None:
    from pathlib import Path

    routes_dir = Path(__file__).resolve().parents[3] / "surplus_ai" / "api"
    forbidden = (
        "run_skip_trace_for_lead",
        "materialize_contact_candidate",
        "ResearchPipeline",
        "LeadPipeline",
        "lookup_contact_candidates",
    )
    for path in routes_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} contains {token}"


def test_unhandled_error_is_generic(api_client: TestClient, session: Session) -> None:
    from surplus_ai.api.dependencies import get_db_session

    application = create_app()

    def _boom() -> Iterator[Session]:
        raise RuntimeError("secret DB failure with postgresql+psycopg://x")
        yield session  # pragma: no cover

    application.dependency_overrides[get_db_session] = _boom
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/cases")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert "postgresql" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "traceback" not in response.text.lower()


def test_api_does_not_expose_password_hash(api_client: TestClient, session: Session) -> None:
    from surplus_ai.auth.passwords import hash_password
    from surplus_ai.database.models.enums import UserRole
    from surplus_ai.database.models.user import User

    user = User(
        name="Api User",
        email="api-user@example.invalid",
        role=UserRole.AGENT,
        password_hash=hash_password("test-passphrase-ok"),
    )
    session.add(user)
    session.flush()
    county = _seed_county(session, slug="api-auth")
    case = _seed_case(session, county, dedupe="api-auth-1")
    lead = Lead(
        surplus_case_id=case.id,
        status=LeadStatus.QUALIFIED,
        assigned_user_id=user.id,
    )
    session.add(lead)
    session.flush()

    paths = (
        "/health",
        "/api/v1/status",
        "/api/v1/cases",
        f"/api/v1/cases/{case.id}",
        "/api/v1/leads",
        f"/api/v1/leads/{lead.id}",
        "/api/v1/contacts",
        "/api/v1/research/reviews",
        "/openapi.json",
        "/",
    )
    for path in paths:
        response = api_client.get(path.strip())
        assert response.status_code == 200, path
        assert "password_hash" not in response.text
        assert "$argon2" not in response.text
        assert "test-passphrase-ok" not in response.text
