"""Manual skip-trace stand-in — never invents contact candidates."""

from __future__ import annotations

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import SkipTraceLookupResult, SkipTraceStatus
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider


class ManualSkipTraceProvider(AbstractSkipTraceProvider):
    def __init__(self, name: str = "manual_skip_trace") -> None:
        self.name = name

    def lookup_contact_candidates(self, lead: Lead) -> SkipTraceLookupResult:
        return SkipTraceLookupResult(
            status=SkipTraceStatus.NOT_FOUND,
            provider_id=self.name,
            lead_id=lead.id,
            surplus_case_id=lead.surplus_case_id,
            candidates=(),
            requires_human_review=True,
            error_code="manual_research_required",
            error_detail="Manual skip-trace: no automated contact candidates.",
            notes="ManualSkipTraceProvider: human research required; zero live traffic.",
        )
