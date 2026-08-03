from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.compliance.rules_loader import ComplianceRulesLoader
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.raw_surplus_row import RawSurplusRow
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.leads.case_builder import CaseBuilder
from surplus_ai.leads.compliance_recorder import ComplianceRecorder
from surplus_ai.leads.county_registry import CountyRegistry
from surplus_ai.leads.lead_builder import LeadBuilder, LeadCreationReport
from surplus_ai.leads.owner_builder import OwnerBuilder
from surplus_ai.parser.interpretation.models import InterpretedDocument, InterpretedRow

logger = structlog.get_logger(__name__)


class LeadPipeline:
    """Builds cases, owners, compliance verdicts and leads from one interpreted document.

    The order matters and is the same every time: a case first, because everything else
    hangs off it; then the owner, because whether the case is worth pursuing depends on who
    the owner is; then the compliance verdict, because it is the most expensive check and
    the only one with legal consequences; then the lead, which is the only step that
    decides rather than records.

    Everything here is idempotent. Running the same document twice updates the same cases
    and leaves the lead count unchanged, because a county republishing its list must not
    double the work queue.
    """

    def __init__(self, session: Session, loader: ComplianceRulesLoader | None = None) -> None:
        self._session = session
        self._counties = CountyRegistry(session)
        self._cases = CaseBuilder(session)
        self._owners = OwnerBuilder(session)
        # One loader shared by both the per-row recorder and promotion, so a run cannot
        # evaluate half its cases against one set of rules and half against another.
        recorder = ComplianceRecorder(session, loader=loader)
        self._compliance = recorder
        self._leads = LeadBuilder(session, recorder=recorder)

    def build(
        self,
        document: InterpretedDocument,
        state: str,
        county_slug: str,
        parsed_document_id: uuid.UUID | None = None,
        as_of: date | None = None,
    ) -> LeadCreationReport:
        """Turn one interpreted document into cases, owners and leads."""
        county, created = self._counties.register(state, county_slug)
        if created:
            logger.info("county_first_seen", state=county.state, slug=county.slug)

        raw_rows = self._raw_row_index(parsed_document_id)
        report = LeadCreationReport()

        for table in document.tables:
            for row in table.rows:
                report.cases_seen += 1
                result = self._cases.build(row, county.id, raw_row_id=raw_rows.get(_position(row)))
                if result.created:
                    report.cases_created += 1

                owner = self._owners.build(result.case, row)
                if owner.owner is not None:
                    report.owners_written += 1

                compliance = self._compliance.record(
                    result.case, county.compliance_state_ref, as_of=as_of
                )
                report.record(self._leads.build(result.case, owner, row.routing, compliance))

        self._session.flush()
        logger.info(
            "lead_pipeline_complete",
            state=county.state,
            county=county.slug,
            cases=report.cases_seen,
            leads=report.leads_total,
            rejections={r.value: c for r, c in report.rejections.items()},
        )
        return report

    def promote(
        self, state: str = "", county_slug: str = "", as_of: date | None = None
    ) -> LeadCreationReport:
        """Re-check stored cases against the rules as they now stand.

        Used after a state's statutes are recorded: the backlog of cases held only by an
        unverified rule set becomes leads without re-parsing anything.
        """
        county_id: uuid.UUID | None = None
        if state and county_slug:
            county = self._counties.find(state, county_slug)
            if county is None:
                logger.info("promote_county_unknown", state=state, slug=county_slug)
                return LeadCreationReport()
            county_id = county.id
        return self._leads.promote(county_id=county_id, as_of=as_of)

    def _raw_row_index(
        self, parsed_document_id: uuid.UUID | None
    ) -> dict[tuple[int, int, int], uuid.UUID]:
        """Link each case back to the verbatim row it came from, where one was stored.

        Keyed by position within the document rather than by content, because two rows in a
        county's list can be byte-identical -- the same owner appearing twice -- and a
        content key would point both cases at one row.
        """
        if parsed_document_id is None:
            return {}
        rows = self._session.scalars(
            select(RawSurplusRow).where(RawSurplusRow.parsed_document_id == parsed_document_id)
        ).all()
        index: dict[tuple[int, int, int], uuid.UUID] = {}
        for row in rows:
            # A row stored without a full position cannot be matched to one here. That is a
            # link not made, not a case lost: the case is still written, and the verbatim
            # row is still stored -- they just are not joined to each other.
            if row.table_index is None or row.page_number is None:
                continue
            if row.row_index_on_page is None:
                continue
            index[(row.table_index, row.page_number, row.row_index_on_page)] = row.id
        return index


def _position(row: InterpretedRow) -> tuple[int, int, int]:
    return (row.table_index, row.page_number, row.row_index_on_page)


class LeadInventory:
    """Reads what the pipeline has produced, for reporting."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def case_count(self, county_id: uuid.UUID | None = None) -> int:
        statement = select(SurplusCase.id)
        if county_id is not None:
            statement = statement.where(SurplusCase.county_id == county_id)
        return len(list(self._session.scalars(statement).all()))

    def claimable_case_count(self, county_id: uuid.UUID | None = None) -> int:
        """Cases where the county published a figure above zero -- money still held."""
        statement = select(SurplusCase.id).where(
            SurplusCase.surplus_amount.is_not(None), SurplusCase.surplus_amount > 0
        )
        if county_id is not None:
            statement = statement.where(SurplusCase.county_id == county_id)
        return len(list(self._session.scalars(statement).all()))

    def lead_count(self, county_id: uuid.UUID | None = None) -> int:
        statement = select(Lead.id)
        if county_id is not None:
            statement = statement.join(SurplusCase, SurplusCase.id == Lead.surplus_case_id).where(
                SurplusCase.county_id == county_id
            )
        return len(list(self._session.scalars(statement).all()))
