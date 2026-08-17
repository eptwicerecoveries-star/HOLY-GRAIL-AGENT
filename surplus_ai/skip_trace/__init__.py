"""Phase 6F: skip-trace candidates and Lead-gated Contact materialization.

LOCKED: Contact rows may materialize only when an actual Lead already exists.
Pre-Lead contact facts remain candidate evidence only. No live vendor traffic in V1.
Compliance remains authoritative for outreach eligibility; Contact existence ≠ permission
to contact. Does not create Lead, mutate Owner, or send communications.
"""

from __future__ import annotations

from surplus_ai.skip_trace.materialize import (
    ContactMaterializeResult,
    MaterializeBlockReason,
    materialize_contact_candidate,
)
from surplus_ai.skip_trace.models import (
    ContactCandidate,
    ContactCandidateType,
    SkipTraceLookupResult,
    SkipTraceStatus,
)
from surplus_ai.skip_trace.providers.base import AbstractSkipTraceProvider
from surplus_ai.skip_trace.providers.credentials_missing import CredentialsMissingSkipTraceProvider
from surplus_ai.skip_trace.providers.fake import FakeSkipTraceProvider
from surplus_ai.skip_trace.providers.manual import ManualSkipTraceProvider
from surplus_ai.skip_trace.providers.null import NullSkipTraceProvider
from surplus_ai.skip_trace.workflow import (
    SkipTraceWorkflowBlockReason,
    SkipTraceWorkflowResult,
    run_skip_trace_for_lead,
)

__all__ = [
    "AbstractSkipTraceProvider",
    "ContactCandidate",
    "ContactCandidateType",
    "ContactMaterializeResult",
    "CredentialsMissingSkipTraceProvider",
    "FakeSkipTraceProvider",
    "ManualSkipTraceProvider",
    "MaterializeBlockReason",
    "NullSkipTraceProvider",
    "SkipTraceLookupResult",
    "SkipTraceStatus",
    "SkipTraceWorkflowBlockReason",
    "SkipTraceWorkflowResult",
    "materialize_contact_candidate",
    "run_skip_trace_for_lead",
]
