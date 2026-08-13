"""Null provider — explicit absence of a usable provider."""

from __future__ import annotations

from surplus_ai.research.models import (
    PropertyLookupQuery,
    ProviderOutcome,
    ProviderOutcomeStatus,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider


class NullProvider(AbstractPropertyRecordProvider):
    """Represents missing or deliberately disabled provider configuration.

    Never returns success. Missing configuration must not be stored as a successful
    research result.
    """

    def __init__(self, name: str = "null") -> None:
        self.name = name

    def supports(self, state: str, county_slug: str) -> bool:
        return True

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        return ProviderOutcome(
            status=ProviderOutcomeStatus.SKIPPED,
            found=False,
            evidence=(),
            requires_human_review=True,
            cacheable=False,
            error_code="provider_not_configured",
            error_detail=(
                f"No property-record provider is available for "
                f"{query.state.upper()}/{query.county_slug}."
            ),
            notes="NullProvider: configuration missing or provider explicitly disabled.",
            raw_response=None,
        )
