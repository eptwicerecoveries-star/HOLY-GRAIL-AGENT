"""Provider that fails closed when a required credential env var is unset."""

from __future__ import annotations

import os

from surplus_ai.research.models import (
    PropertyLookupQuery,
    ProviderOutcome,
    ProviderOutcomeStatus,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider


class CredentialsMissingProvider(AbstractPropertyRecordProvider):
    """Stand-in used when a configured provider requires credentials that are absent.

    Never fabricates success. Phase 6A has no live HTTP providers; this exists so
    missing credentials are an explicit error outcome rather than a silent skip-as-ok.
    """

    def __init__(self, name: str, env_var: str) -> None:
        self.name = name
        self._env_var = env_var

    def supports(self, state: str, county_slug: str) -> bool:
        return True

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        # Re-check at call time so tests can set/clear env around a resolve().
        if os.environ.get(self._env_var):
            return ProviderOutcome(
                status=ProviderOutcomeStatus.ERROR,
                found=False,
                requires_human_review=True,
                cacheable=False,
                error_code="provider_not_implemented",
                error_detail=(
                    f"Provider {self.name!r} has credentials but no live adapter in "
                    "Phase 6A. Live external providers are not enabled."
                ),
                notes="Phase 6A does not perform network research.",
            )
        return ProviderOutcome(
            status=ProviderOutcomeStatus.ERROR,
            found=False,
            requires_human_review=True,
            cacheable=False,
            error_code="credentials_missing",
            error_detail=(
                f"Provider {self.name!r} requires environment variable {self._env_var}, "
                "which is not set. Credentials are never stored in research payloads."
            ),
            notes="Missing credentials must not be recorded as a successful research result.",
        )
