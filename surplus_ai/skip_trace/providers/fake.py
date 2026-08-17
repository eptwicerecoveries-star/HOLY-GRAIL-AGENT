"""In-process fake skip-trace provider for tests. Performs no network I/O."""

from __future__ import annotations

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import (
    ContactCandidate,
    SkipTraceLookupResult,
    SkipTraceStatus,
)
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider


class FakeSkipTraceProvider(AbstractSkipTraceProvider):
    def __init__(
        self,
        name: str,
        result: SkipTraceLookupResult | None = None,
        *,
        candidates: tuple[ContactCandidate, ...] = (),
        status: SkipTraceStatus = SkipTraceStatus.SUCCESS,
        requires_human_review: bool = False,
        error_code: str | None = None,
    ) -> None:
        self.name = name
        self._result = result
        self._candidates = candidates
        self._status = status
        self._requires_human_review = requires_human_review
        self._error_code = error_code
        self.calls = 0

    def lookup_contact_candidates(self, lead: Lead) -> SkipTraceLookupResult:
        self.calls += 1
        if self._result is not None:
            return self._result
        stamped = tuple(
            c.model_copy(
                update={
                    "surplus_case_id": lead.surplus_case_id,
                    "provider_id": self.name,
                }
            )
            for c in self._candidates
        )
        return SkipTraceLookupResult(
            status=self._status,
            provider_id=self.name,
            lead_id=lead.id,
            surplus_case_id=lead.surplus_case_id,
            candidates=stamped,
            requires_human_review=self._requires_human_review,
            error_code=self._error_code,
            notes="FakeSkipTraceProvider — offline fixture only.",
        )
