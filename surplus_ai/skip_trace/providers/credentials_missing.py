"""Credentials-missing skip-trace stand-in. Never fabricates success."""

from __future__ import annotations

import os

from surplus_ai.database.models.lead import Lead
from surplus_ai.skip_trace.models import SkipTraceLookupResult, SkipTraceStatus
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider


class CredentialsMissingSkipTraceProvider(AbstractSkipTraceProvider):
    def __init__(self, name: str, env_var: str) -> None:
        self.name = name
        self._env_var = env_var

    def lookup_contact_candidates(self, lead: Lead) -> SkipTraceLookupResult:
        if os.environ.get(self._env_var):
            return SkipTraceLookupResult(
                status=SkipTraceStatus.ERROR,
                provider_id=self.name,
                lead_id=lead.id,
                surplus_case_id=lead.surplus_case_id,
                candidates=(),
                requires_human_review=True,
                error_code="provider_not_implemented",
                error_detail=(
                    f"Provider {self.name!r} has credentials but no live adapter. "
                    "Phase 6F V1 does not perform live skip-trace."
                ),
                notes="Live skip-trace vendors require separate human approval.",
            )
        return SkipTraceLookupResult(
            status=SkipTraceStatus.ERROR,
            provider_id=self.name,
            lead_id=lead.id,
            surplus_case_id=lead.surplus_case_id,
            candidates=(),
            requires_human_review=True,
            error_code="credentials_missing",
            error_detail=(
                f"Provider {self.name!r} requires environment variable {self._env_var}, "
                "which is not set. Credentials are never stored in contact payloads."
            ),
            notes="Missing credentials must not be treated as a successful skip-trace.",
        )
