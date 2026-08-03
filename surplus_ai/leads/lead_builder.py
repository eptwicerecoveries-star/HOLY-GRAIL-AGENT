from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.classifier.entity_keywords import load_entity_keywords
from surplus_ai.compliance.engine import ComplianceResult
from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import LeadStatus, OwnerType, SurplusCaseStatus
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.leads.compliance_recorder import ComplianceRecorder
from surplus_ai.leads.owner_builder import OwnerBuildResult
from surplus_ai.parser.interpretation.models import RoutingDecision

logger = structlog.get_logger(__name__)


class RejectionReason(str, Enum):
    """Why a case did not become a lead. Coded so the funnel can be counted and explained."""

    NO_SURPLUS = "no_surplus"
    """The county published no surplus figure. Never treated as zero, and never inferred."""

    SURPLUS_NOT_CLAIMABLE = "surplus_not_claimable"
    """A published figure of zero or less: a real answer meaning nothing is left to claim."""

    NO_OWNER = "no_owner"
    OWNER_NOT_PURSUABLE = "owner_not_pursuable"
    NEEDS_REVIEW = "needs_review"
    """The row was not auto-accepted, so nobody may act on it until a person has looked."""

    COMPLIANCE_BLOCKED = "compliance_blocked"


class LeadDecision:
    """The verdict on one case, with the reason when the answer is no."""

    def __init__(
        self,
        case_id: uuid.UUID,
        lead: Lead | None,
        reason: RejectionReason | None,
        detail: str = "",
        created: bool = False,
    ) -> None:
        self.case_id = case_id
        self.lead = lead
        self.reason = reason
        self.detail = detail
        self.created = created

    @property
    def is_lead(self) -> bool:
        return self.lead is not None


class LeadCreationReport:
    """What a run produced, stage by stage.

    Reported as a funnel rather than a single number because the interesting question is
    almost never "how many leads" but "where did the rest go". A run that produces no leads
    at all is a normal outcome -- an unverified state blocks every case in it -- and the
    counts are what make that legible instead of alarming.
    """

    def __init__(self) -> None:
        self.cases_seen = 0
        self.cases_created = 0
        self.owners_written = 0
        self.leads_created = 0
        self.leads_existing = 0
        self.rejections: Counter[RejectionReason] = Counter()

    def record(self, decision: LeadDecision) -> None:
        if decision.is_lead:
            if decision.created:
                self.leads_created += 1
            else:
                self.leads_existing += 1
            return
        if decision.reason is not None:
            self.rejections[decision.reason] += 1

    @property
    def leads_total(self) -> int:
        return self.leads_created + self.leads_existing

    def summary_lines(self) -> list[str]:
        lines = [
            f"cases seen:     {self.cases_seen}",
            f"cases created:  {self.cases_created}",
            f"owners written: {self.owners_written}",
            f"leads created:  {self.leads_created}",
            f"leads existing: {self.leads_existing}",
        ]
        if self.rejections:
            lines.append("not leads:")
            for reason, count in self.rejections.most_common():
                lines.append(f"  {reason.value:24} {count}")
        return lines


class LeadBuilder:
    """Decides which cases are worth someone's time, and writes those as leads.

    Cases and owners are records of fact and are written whatever they say. A lead is a
    decision that a person should be contacted, so it is gated on four things at once:

    1. the county holds money -- a surplus figure exists and is above zero;
    2. the owner is a kind the business pursues;
    3. the row was auto-accepted, so no unreviewed reading reaches a call list;
    4. the state's compliance rules positively permit contact.

    All four must hold. The fourth blocks everything today, because no state's statutes
    have been recorded yet -- which is the compliance engine working as designed, not a
    fault in this class. When a state is brought online, `promote` converts the backlog.
    """

    def __init__(self, session: Session, recorder: ComplianceRecorder | None = None) -> None:
        self._session = session
        # Only `promote` needs this: `build` is handed a verdict that the pipeline already
        # obtained. It is injectable so a caller can evaluate against a specific set of
        # rules rather than whatever happens to be on disk.
        self._recorder = recorder or ComplianceRecorder(session)

    def build(
        self,
        case: SurplusCase,
        owner: OwnerBuildResult,
        routing: RoutingDecision,
        compliance: ComplianceResult,
    ) -> LeadDecision:
        rejection = self._rejection(case, owner.owner_type, owner.owner is not None, routing)
        if rejection is None and not compliance.is_eligible:
            rejection = RejectionReason.COMPLIANCE_BLOCKED

        if rejection is not None:
            case.status = SurplusCaseStatus.REJECTED
            self._session.flush()
            return LeadDecision(case.id, None, rejection, _detail(rejection, case, compliance))

        return self._qualify(case, owner.owner)

    def promote(
        self, county_id: uuid.UUID | None = None, as_of: date | None = None
    ) -> LeadCreationReport:
        """Re-check stored cases and create leads for any that now qualify.

        This is what a state coming online is for. Cases rejected only because nobody had
        recorded a statute are re-evaluated against the rules as they now stand, without
        re-parsing a single PDF. Cases rejected for a reason that cannot change -- no
        surplus, a company owner -- are skipped cheaply.
        """
        report = LeadCreationReport()

        for case, state_code in self._promotable(county_id):
            report.cases_seen += 1
            owner = self._principal_owner(case)
            rejection = self._rejection(
                case,
                owner.owner_type if owner else OwnerType.UNKNOWN,
                owner is not None,
                RoutingDecision.AUTO_ACCEPT,
            )
            if rejection is not None:
                report.record(LeadDecision(case.id, None, rejection))
                continue

            result = self._recorder.record(case, state_code, as_of=as_of)
            if not result.is_eligible:
                case.status = SurplusCaseStatus.REJECTED
                report.record(
                    LeadDecision(
                        case.id,
                        None,
                        RejectionReason.COMPLIANCE_BLOCKED,
                        result.summary,
                    )
                )
                continue
            report.record(self._qualify(case, owner))

        self._session.flush()
        logger.info(
            "leads_promoted",
            county_id=str(county_id) if county_id else None,
            created=report.leads_created,
            seen=report.cases_seen,
        )
        return report

    def _qualify(self, case: SurplusCase, owner: Owner | None) -> LeadDecision:
        """Write the lead, or return the one already there."""
        existing = self._session.scalar(select(Lead).where(Lead.surplus_case_id == case.id))
        case.status = SurplusCaseStatus.QUALIFIED
        if existing is not None:
            existing.owner_id = owner.id if owner else existing.owner_id
            self._session.flush()
            return LeadDecision(case.id, existing, None, created=False)

        lead = Lead(
            surplus_case_id=case.id,
            owner_id=owner.id if owner else None,
            status=LeadStatus.QUALIFIED,
            qualified_at=datetime.now(UTC),
        )
        self._session.add(lead)
        self._session.flush()
        logger.info("lead_created", case_id=str(case.id), lead_id=str(lead.id))
        return LeadDecision(case.id, lead, None, created=True)

    def _rejection(
        self,
        case: SurplusCase,
        owner_type: OwnerType,
        has_owner: bool,
        routing: RoutingDecision,
    ) -> RejectionReason | None:
        """The first reason this case is not worth a call, checked cheapest first."""
        if case.surplus_amount is None:
            return RejectionReason.NO_SURPLUS
        if case.surplus_amount <= Decimal("0"):
            return RejectionReason.SURPLUS_NOT_CLAIMABLE
        if not has_owner:
            return RejectionReason.NO_OWNER
        if not _is_pursuable(owner_type):
            return RejectionReason.OWNER_NOT_PURSUABLE
        if routing is not RoutingDecision.AUTO_ACCEPT:
            return RejectionReason.NEEDS_REVIEW
        return None

    def _promotable(self, county_id: uuid.UUID | None) -> list[tuple[SurplusCase, str]]:
        """Cases that could still become leads, with the state whose rules govern them."""
        statement = (
            select(SurplusCase, County.compliance_state_ref)
            .join(County, County.id == SurplusCase.county_id)
            .where(SurplusCase.status != SurplusCaseStatus.QUALIFIED)
            .where(SurplusCase.surplus_amount.is_not(None))
            .where(SurplusCase.surplus_amount > Decimal("0"))
        )
        if county_id is not None:
            statement = statement.where(SurplusCase.county_id == county_id)
        return [(case, state) for case, state in self._session.execute(statement).all()]

    def _principal_owner(self, case: SurplusCase) -> Owner | None:
        """The owner to attach a lead to when a case carries more than one row's worth.

        The most confidently classified pursuable owner wins. Preferring a pursuable owner
        over a more confident unpursuable one is deliberate: a case listing a person and a
        servicing company is a case about the person.
        """
        owners = list(
            self._session.scalars(select(Owner).where(Owner.surplus_case_id == case.id)).all()
        )
        if not owners:
            return None
        pursuable = [o for o in owners if _is_pursuable(o.owner_type)]
        pool = pursuable or owners
        return max(pool, key=lambda o: o.classification_confidence or 0.0)


def _is_pursuable(owner_type: OwnerType) -> bool:
    """Whether this kind of owner is worth contacting, per the classifier's configuration."""
    return load_entity_keywords().is_pursuable(owner_type)


def _detail(reason: RejectionReason, case: SurplusCase, compliance: ComplianceResult) -> str:
    if reason is RejectionReason.COMPLIANCE_BLOCKED:
        return compliance.summary
    if reason is RejectionReason.NO_SURPLUS:
        return (
            "The county published no surplus figure for this case. It is stored, but there "
            "is nothing recorded as owed."
        )
    if reason is RejectionReason.SURPLUS_NOT_CLAIMABLE:
        return f"The published surplus is {case.surplus_amount}, so nothing is left to claim."
    return ""
