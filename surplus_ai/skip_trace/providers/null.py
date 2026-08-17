"""Null skip-trace provider — explicit absence of a usable vendor."""

from __future__ import annotations

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import SkipTraceLookupResult, SkipTraceStatus
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider


class NullSkipTraceProvider(AbstractSkipTraceProvider):
    def __init__(self, name: str = "null_skip_trace") -> None:
        self.name = name

    def lookup_contact_candidates(self, lead: Lead) -> SkipTraceLookupResult:
        return SkipTraceLookupResult(
            status=SkipTraceStatus.SKIPPED,
            provider_id=self.name,
            lead_id=lead.id,
            surplus_case_id=lead.surplus_case_id,
            candidates=(),
            requires_human_review=True,
            error_code="provider_not_configured",
            error_detail="No skip-trace provider is configured.",
            notes="NullSkipTraceProvider: missing or disabled configuration.",
        )
