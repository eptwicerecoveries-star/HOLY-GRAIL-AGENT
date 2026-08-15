from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.enums import ResearchReviewReason, ResearchReviewResolution
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.research_result import ResearchResult
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.research.http import HttpGetResult
from surplus_ai.research.pipeline import ResearchPipeline
from surplus_ai.research.registry import ProviderRegistry
from surplus_ai.research.review import ResearchReviewQueue, classify_review_reason
from tests.unit.research.conftest import (
    SHIPPED_RELIABILITY,
    FakeClock,
    force_provider,
    write_providers_yaml,
)
from tests.unit.research.test_socrata import RecordingHttp, _row, http_json, valid_options


def _registry(
    tmp_path: Any,
    http: RecordingHttp,
    *,
    reliability: dict[str, Any] | None = None,
    verified: bool = True,
    requires_credential: str | None = None,
    extra_options: dict[str, Any] | None = None,
) -> ProviderRegistry:
    options = valid_options(verified_for_automated_access=verified)
    if extra_options:
        options.update(extra_options)
    spec: dict[str, Any] = {"type": "socrata", "description": "test socrata", "options": options}
    if requires_credential:
        spec["requires_credential"] = requires_credential
    path = write_providers_yaml(
        tmp_path,
        reliability=reliability or SHIPPED_RELIABILITY,
        providers={
            "manual_lookup": {"type": "manual", "description": "t"},
            "live_socrata": spec,
        },
    )
    registry = ProviderRegistry(
        config_path=path,
        counties_dir=tmp_path / "counties",
        http_client=http,
    )
    force_provider(registry, "live_socrata")
    return registry


def _pipeline(
    session: Session,
    tmp_path: Any,
    http: RecordingHttp,
    *,
    clock: FakeClock | None = None,
    use_cache: bool = True,
    **kwargs: Any,
) -> ResearchPipeline:
    registry = _registry(tmp_path, http, **kwargs)
    extra: dict[str, Any] = {}
    if clock is not None:
        extra = {
            "monotonic": clock.monotonic,
            "sleeper": clock.sleep,
            "wall_clock": clock.wall,
        }
    return ResearchPipeline(session, registry=registry, use_cache=use_cache, **extra)


def test_cache_hit_makes_no_http(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([_row()]))
    pipeline = _pipeline(session, tmp_path, http)
    first = pipeline.research_case(sample_case.id)
    second = pipeline.research_case(sample_case.id)
    assert first.id == second.id
    assert len(http.calls) == 1
    count = session.scalar(
        select(func.count())
        .select_from(ResearchResult)
        .where(ResearchResult.surplus_case_id == sample_case.id)
    )
    assert count == 1
    assert session.scalar(select(func.count()).select_from(ResearchReviewItem)) == 0


def test_no_cache_appends_and_calls_http(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([_row()]))
    pipeline = _pipeline(session, tmp_path, http)
    first = pipeline.research_case(sample_case.id)
    second = pipeline.research_case(sample_case.id, use_cache=False)
    assert first.id != second.id
    assert len(http.calls) == 2
    assert second.response_payload["cacheable"] is True


def test_timeout_retries_through_6b(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        HttpGetResult(error_code="timeout", retryable=True),
        HttpGetResult(error_code="timeout", retryable=True),
        http_json([]),
    )
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 3
    assert clock.sleeps == [0.5, 0.5]
    assert row.response_payload["provider_status"] == "not_found"


def test_429_retries_through_6b(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        HttpGetResult(status_code=429, body=b""),
        http_json([_row()]),
    )
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 2
    assert clock.sleeps == [0.5]
    assert row.status.value == "success"


def test_5xx_retryable_through_6b(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        HttpGetResult(status_code=503, body=b"err"),
        http_json([_row()]),
    )
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 2
    assert clock.sleeps == [0.5]
    assert row.status.value == "success"


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_not_retried(
    session: Session, sample_case: SurplusCase, tmp_path: Any, status: int
) -> None:
    http = RecordingHttp(HttpGetResult(status_code=status, body=b"denied"))
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 1
    assert clock.sleeps == []
    assert row.status.value == "error"
    assert row.response_payload["error_code"] == f"http_{status}"


def test_local_limiter_creates_no_extra_evidence(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([]))
    clock = FakeClock()
    pipeline = _pipeline(
        session,
        tmp_path,
        http,
        clock=clock,
        use_cache=False,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": 10},
        },
    )
    first = pipeline.research_case(sample_case.id)
    second = pipeline.research_case(sample_case.id)
    assert first.id != second.id
    assert len(http.calls) == 2
    assert clock.sleeps == [0.1]
    assert first.response_payload["provider_status"] == "not_found"
    assert second.response_payload["provider_status"] == "not_found"


def test_clean_success_creates_no_review(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([_row()]))
    row = _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    assert row.response_payload["requires_human_review"] is False
    assert session.scalar(select(func.count()).select_from(ResearchReviewItem)) == 0


def test_ambiguity_creates_review(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        http_json(
            [
                _row(**{":id": "row-1", "owner_name": "A"}),
                _row(**{":id": "row-2", "owner_name": "B"}),
            ]
        )
    )
    row = _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.AMBIGUOUS_IDENTITY
    assert row.response_payload["cacheable"] is True
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.AMBIGUOUS_IDENTITY
    )


def test_unverified_maps_to_provider_unavailable(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([_row()]))
    row = _pipeline(session, tmp_path, http, use_cache=False, verified=False).research_case(
        sample_case.id
    )
    assert http.calls == []
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE
    assert row.response_payload["error_code"] == "automated_access_not_verified"
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.PROVIDER_UNAVAILABLE
    )


def test_schema_mismatch_is_provider_failure(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(http_json([{"parcel_id": "01-234567"}]))
    row = _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_FAILURE
    assert row.response_payload["error_code"] == "schema_mismatch"
    clock = FakeClock()
    http2 = RecordingHttp(http_json([{"parcel_id": "01-234567"}]))
    pipeline = _pipeline(session, tmp_path, http2, clock=clock, use_cache=False)
    pipeline.research_case(sample_case.id)
    assert len(http2.calls) == 1
    assert clock.sleeps == []


def test_provider_failure_reason_for_live_errors(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(HttpGetResult(status_code=500, body=b"err"))
    row = _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_FAILURE
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.PROVIDER_FAILURE
    )


def test_http_401_is_provider_unavailable(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(HttpGetResult(status_code=401, body=b"denied"))
    row = _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.PROVIDER_UNAVAILABLE
    )


def test_unsafe_resolved_address_is_provider_unavailable(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(HttpGetResult(error_code="unsafe_resolved_address", retryable=False))
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 1
    assert clock.sleeps == []
    assert row.response_payload["error_code"] == "unsafe_resolved_address"
    dumped = str(row.response_payload)
    assert "10.0.0.1" not in dumped
    assert "8.8.8.8" not in dumped
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.PROVIDER_UNAVAILABLE
    )


def test_dns_resolution_failed_retries_as_provider_failure(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        HttpGetResult(error_code="dns_resolution_failed", retryable=True),
        http_json([]),
    )
    clock = FakeClock()
    pipeline = _pipeline(session, tmp_path, http, clock=clock, use_cache=False)
    row = pipeline.research_case(sample_case.id)
    assert len(http.calls) == 2
    assert clock.sleeps == [0.5]
    assert row.response_payload["provider_status"] == "not_found"


def test_dns_resolution_failed_maps_to_provider_failure(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(HttpGetResult(error_code="dns_resolution_failed", retryable=True))
    pipeline = _pipeline(
        session,
        tmp_path,
        http,
        use_cache=False,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": 0},
        },
    )
    row = pipeline.research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_FAILURE
    assert row.response_payload["error_code"] == "dns_resolution_failed"
    assert classify_review_reason(row, provider_type="socrata") is (
        ResearchReviewReason.PROVIDER_FAILURE
    )


def test_required_credential_missing_no_http(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SURPLUS_AI_SOCRATA_REQUIRED", raising=False)
    http = RecordingHttp(http_json([_row()]))
    row = _pipeline(
        session,
        tmp_path,
        http,
        use_cache=False,
        requires_credential="SURPLUS_AI_SOCRATA_REQUIRED",
    ).research_case(sample_case.id)
    assert http.calls == []
    assert row.response_payload["error_code"] == "credentials_missing"
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    assert item.reason is ResearchReviewReason.PROVIDER_UNAVAILABLE


def test_persisted_payloads_omit_token_and_query(
    session: Session,
    sample_case: SurplusCase,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN", "super-secret-app-token")
    http = RecordingHttp(http_json([_row()]))
    row = _pipeline(
        session,
        tmp_path,
        http,
        use_cache=False,
        extra_options={"optional_credential": "SURPLUS_AI_SOCRATA_EXAMPLE_APP_TOKEN"},
    ).research_case(sample_case.id)
    assert http.calls[0]["headers"]["X-App-Token"] == "super-secret-app-token"
    dumped = f"{row.request_payload}{row.response_payload}"
    assert "super-secret-app-token" not in dumped
    assert "X-App-Token" not in dumped
    source_url = row.response_payload.get("source_url")
    assert source_url == "https://opendata.example.gov/resource/abcd-1234.json"
    assert "?" not in source_url
    raw = row.response_payload.get("raw_response") or {}
    assert "$where" not in str(raw)
    assert "parcel_id = " not in str(raw)


def test_review_resolve_side_effects_remain_intact(
    session: Session, sample_case: SurplusCase, tmp_path: Any
) -> None:
    http = RecordingHttp(
        http_json(
            [
                _row(**{":id": "row-1", "owner_name": "A"}),
                _row(**{":id": "row-2", "owner_name": "B"}),
            ]
        )
    )
    _pipeline(session, tmp_path, http, use_cache=False).research_case(sample_case.id)
    item = session.scalar(select(ResearchReviewItem))
    assert item is not None
    queue = ResearchReviewQueue(session)
    queue.resolve(
        item.id,
        reviewed_by="tester",
        resolution=ResearchReviewResolution.EVIDENCE_USABLE,
    )
    session.flush()
    assert session.scalar(select(func.count()).select_from(Contact)) == 0
    assert session.scalar(select(func.count()).select_from(Lead)) == 0
    assert session.scalar(select(func.count()).select_from(Property)) == 0
    assert session.scalar(select(func.count()).select_from(ComplianceEvaluation)) == 0
    session.refresh(sample_case)
    assert sample_case.status.value == "normalized"
