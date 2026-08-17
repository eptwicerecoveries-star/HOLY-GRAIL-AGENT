"""Abstract skip-trace provider. Offline foundation only — no live HTTP."""

from __future__ import annotations

from abc import ABC, abstractmethod

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import SkipTraceLookupResult


class AbstractSkipTraceProvider(ABC):
    """Acquire contact *candidates* for an existing Lead. Does not create Contact rows."""

    name: str

    @abstractmethod
    def lookup_contact_candidates(self, lead: Lead) -> SkipTraceLookupResult:
        """Return candidates for the given Lead. Never creates Contact or Lead."""
