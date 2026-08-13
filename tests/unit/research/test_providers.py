from __future__ import annotations

import pytest

from surplus_ai.research.models import PropertyLookupQuery, ProviderOutcomeStatus
from surplus_ai.research.providers.credentials_missing import CredentialsMissingProvider
from surplus_ai.research.providers.manual import ManualLookupProvider
from surplus_ai.research.providers.null import NullProvider


def _query() -> PropertyLookupQuery:
    return PropertyLookupQuery(
        state="MD",
        county_slug="harford",
        parcel_id="01-234567",
        owner_raw_name="STEFFEN, DEBORAH",
    )


def test_manual_lookup_never_succeeds() -> None:
    outcome = ManualLookupProvider().lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.NOT_FOUND
    assert outcome.found is False
    assert outcome.requires_human_review is True
    assert outcome.evidence == ()


def test_null_provider_never_succeeds() -> None:
    outcome = NullProvider().lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.SKIPPED
    assert outcome.found is False
    assert outcome.error_code == "provider_not_configured"
    assert outcome.requires_human_review is True


def test_credentials_missing_never_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY", raising=False)
    provider = CredentialsMissingProvider(
        name="example_open_data",
        env_var="SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY",
    )
    outcome = provider.lookup(_query())
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.found is False
    assert outcome.error_code == "credentials_missing"


def test_credentials_present_still_not_live_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY", "not-a-real-secret-for-tests")
    provider = CredentialsMissingProvider(
        name="example_open_data",
        env_var="SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY",
    )
    outcome = provider.lookup(_query())
    assert outcome.found is False
    assert outcome.status is ProviderOutcomeStatus.ERROR
    assert outcome.error_code == "provider_not_implemented"
