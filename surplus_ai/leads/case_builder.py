from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.enums import SurplusCaseStatus, SurplusSourceType
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.leads.dedupe import CaseIdentity, case_identity
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.models import InterpretedRow

logger = structlog.get_logger(__name__)

# Published money figures, mapped to the column that holds each. Every concept keeps its own
# column: a winning bid, an assessment and a surplus are different numbers, and only one of
# them is money owed back to a former owner.
MONEY_COLUMNS: dict[CanonicalField, str] = {
    CanonicalField.JUDGMENT_AMOUNT: "judgment_amount",
    CanonicalField.SALE_AMOUNT: "sale_amount_published",
    CanonicalField.WINNING_BID: "winning_bid",
    CanonicalField.OPENING_BID: "opening_bid",
    CanonicalField.ASSESSED_VALUE: "assessed_value",
    CanonicalField.APPRAISED_VALUE: "appraised_value",
    CanonicalField.TAXES_DUE: "taxes_due",
    CanonicalField.FEES_AMOUNT: "fees_amount",
    CanonicalField.FACE_VALUE_AMOUNT: "face_value_amount",
    CanonicalField.OVERBID_AMOUNT: "overbid_amount",
    CanonicalField.PURCHASE_AMOUNT: "purchase_amount",
    CanonicalField.REFUNDED_AMOUNT: "refunded_amount",
    CanonicalField.REMAINING_AMOUNT: "remaining_amount",
}


class CaseBuildResult:
    """One row's outcome: the case it became, and whether it already existed."""

    def __init__(self, case: SurplusCase, identity: CaseIdentity, created: bool) -> None:
        self.case = case
        self.identity = identity
        self.created = created


class CaseBuilder:
    """Writes a `SurplusCase` for every interpreted row.

    A case is a record of fact -- the county published this row -- so one is written
    whatever the row says. Nothing is filtered here: a row with no surplus, an unreadable
    owner or a figure of zero all become cases, because they are all things the county
    published and the pipeline has to be able to account for them later.

    Re-running over the same list updates the case in place rather than inserting a second.
    Counties republish with corrections, and the corrected figure is the one worth keeping;
    the verbatim original is never touched, since it lives in `raw_surplus_rows`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def build(
        self,
        row: InterpretedRow,
        county_id: uuid.UUID,
        raw_row_id: uuid.UUID | None = None,
    ) -> CaseBuildResult:
        identity = case_identity(row)
        existing = self._session.scalar(
            select(SurplusCase).where(
                SurplusCase.county_id == county_id,
                SurplusCase.dedupe_hash == identity.dedupe_hash,
            )
        )
        case = existing or SurplusCase(county_id=county_id, dedupe_hash=identity.dedupe_hash)
        self._apply(case, row, identity, raw_row_id)

        if existing is None:
            self._session.add(case)
        self._session.flush()
        self._attach_property(case, row)

        return CaseBuildResult(case, identity, created=existing is None)

    def _apply(
        self,
        case: SurplusCase,
        row: InterpretedRow,
        identity: CaseIdentity,
        raw_row_id: uuid.UUID | None,
    ) -> None:
        case.raw_row_id = raw_row_id
        case.case_number = _text(row, CanonicalField.CASE_NUMBER)
        case.parcel_id = _text(row, CanonicalField.PARCEL_ID)
        case.property_address_raw = _text(row, CanonicalField.PROPERTY_ADDRESS)
        case.sale_date = identity.sale_date

        # The surplus is copied exactly as the interpreter resolved it, including the None
        # that means "this county published no surplus". Filling that in from another money
        # column here would undo the one rule the parser is most careful about.
        case.surplus_amount = row.surplus_amount
        case.surplus_is_explicit = row.surplus_is_explicit
        case.surplus_source = SurplusSourceType(row.surplus_source.value)
        case.surplus_source_column = row.surplus_source_column

        for field, column in MONEY_COLUMNS.items():
            setattr(case, column, _money(row, field))

        deadline = row.value(CanonicalField.CLAIM_DEADLINE)
        case.distribution_deadline = deadline if isinstance(deadline, date) else None

        if case.status is None or case.status is SurplusCaseStatus.NEW:
            case.status = SurplusCaseStatus.NORMALIZED

    def _attach_property(self, case: SurplusCase, row: InterpretedRow) -> None:
        """Record the property where the county described one.

        Only written when there is something to say. An empty property row would suggest a
        parcel was looked at and found to have no details, which is not what happened.
        """
        parcel = _text(row, CanonicalField.PARCEL_ID)
        legal = _text(row, CanonicalField.LEGAL_DESCRIPTION)
        assessed = _money(row, CanonicalField.ASSESSED_VALUE)
        if parcel is None and legal is None and assessed is None:
            return

        existing = self._session.scalar(select(Property).where(Property.surplus_case_id == case.id))
        prop = existing or Property(surplus_case_id=case.id)
        prop.parcel_id = parcel
        prop.legal_description = legal
        prop.assessed_value = assessed
        if existing is None:
            self._session.add(prop)
        self._session.flush()


def _text(row: InterpretedRow, field: CanonicalField) -> str | None:
    value = row.value(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _money(row: InterpretedRow, field: CanonicalField) -> Decimal | None:
    value = row.value(field)
    return value if isinstance(value, Decimal) else None
