from __future__ import annotations

from abc import ABC, abstractmethod

from surplus_ai.research.models import PropertyLookupQuery, ProviderOutcome


class AbstractPropertyRecordProvider(ABC):
    """County-agnostic property-record lookup. County variance belongs in config."""

    name: str

    @abstractmethod
    def supports(self, state: str, county_slug: str) -> bool:
        """Whether this provider can be used for the given county reference."""

    @abstractmethod
    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        """Perform a lookup. Must never invent a successful result."""
