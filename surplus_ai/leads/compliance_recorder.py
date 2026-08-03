from __future__ import annotations

from datetime import date

import structlog
from sqlalchemy.orm import Session

from surplus_ai.compliance.engine import ComplianceCase, ComplianceEngine, ComplianceResult
from surplus_ai.compliance.rules_loader import ComplianceRulesLoader
from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.enums import SurplusCaseStatus
from surplus_ai.database.models.surplus_case import SurplusCase

logger = structlog.get_logger(__name__)

# Recorded when a state has no rules file at all. A verdict still has to say which rules
# produced it, and "there were none" is the honest answer -- more useful than a blank, which
# would be indistinguishable from a bug in the recorder.
NO_RULES_VERSION = "absent"


class ComplianceRecorder:
    """Evaluates a case against its state's rules and records the verdict.

    Every case gets an evaluation, including the ones that are refused. A blocked case with
    no stored reason is indistinguishable from a case nobody looked at, and the difference
    matters: the first is the system working, the second is work not done.

    Each evaluation records the version of the rules that produced it, so a verdict reached
    under one reading of a statute is still legible after the file is corrected.
    """

    def __init__(
        self,
        session: Session,
        engine: ComplianceEngine | None = None,
        loader: ComplianceRulesLoader | None = None,
    ) -> None:
        self._session = session
        self._loader = loader or ComplianceRulesLoader()
        self._engine = engine or ComplianceEngine(self._loader)

    def record(
        self, case: SurplusCase, state_code: str, as_of: date | None = None
    ) -> ComplianceResult:
        """Evaluate one case and store the verdict against it."""
        result = self._engine.evaluate(
            ComplianceCase(
                state_code=state_code,
                sale_date=case.sale_date,
                surplus_amount=case.surplus_amount,
                case_reference=str(case.id),
            ),
            as_of=as_of,
        )

        self._session.add(
            ComplianceEvaluation(
                surplus_case_id=case.id,
                state_rule_version=self._version_for(state_code),
                is_eligible=result.is_eligible,
                fee_cap_pct=result.fee_cap_pct,
                earliest_contact_date=result.earliest_contact_date,
                disclosures_required=list(result.disclosures_required),
                evaluation_notes=_notes(result),
            )
        )
        if case.status in (SurplusCaseStatus.NORMALIZED, SurplusCaseStatus.CLASSIFIED):
            case.status = SurplusCaseStatus.COMPLIANCE_CHECKED
        self._session.flush()

        logger.debug(
            "compliance_recorded",
            case_id=str(case.id),
            state=state_code,
            eligible=result.is_eligible,
        )
        return result

    def _version_for(self, state_code: str) -> str:
        rules = self._loader.load_or_none(state_code)
        return rules.version_hash() if rules is not None else NO_RULES_VERSION


def _notes(result: ComplianceResult) -> str:
    """Why the case was cleared or held, in words a reviewer can act on."""
    if result.is_eligible:
        return "Eligible to contact." + (f" {result.notes}" if result.notes else "")
    return " ".join(f"[{r.code.value}] {r.detail}" for r in result.blocking_reasons)
