"""Manual lookup provider — no automated official source configured."""

from __future__ import annotations

from surplus_ai.research.models import (
    PropertyLookupQuery,
    ProviderOutcome,
    ProviderOutcomeStatus,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider


class ManualLookupProvider(AbstractPropertyRecordProvider):
    """Always reports not_found and requires human research.

    Never fabricates a successful research result. Used when no official automated
    property-record source is configured for a county.
    """

    def __init__(self, name: str = "manual_lookup") -> None:
        self.name = name

    def supports(self, state: str, county_slug: str) -> bool:
        return bool(state) and bool(county_slug)

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        return ProviderOutcome(
            status=ProviderOutcomeStatus.NOT_FOUND,
            found=False,
            evidence=(),
            source_url=None,
            error_code=None,
            error_detail=None,
            requires_human_review=True,
            cacheable=True,
            notes=(
                "No automated official property-record provider is configured for "
                f"{query.state.upper()}/{query.county_slug}. Manual research is required."
            ),
            raw_response=None,
        )
