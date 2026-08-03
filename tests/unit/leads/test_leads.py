"""Turning parsed rows into cases, owners and leads.

Two things are being proved here. The first is that cases and owners are records of fact:
they are written for every row, whatever the row says, because a case nobody wrote is a
case nobody can account for later. The second is that a lead is a decision, gated on four
independent conditions, and that each of those four alone is enough to withhold it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.compliance.rules_loader import ComplianceRulesLoader
from surplus_ai.compliance.state_rules import FeeCapBasis, StateComplianceRules
from surplus_ai.database.models.compliance_evaluation import ComplianceEvaluation
from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import (
    CountySourceType,
    ExtractionMethod,
    OwnerType,
    PublishingFrequency,
    SurplusCaseStatus,
)
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.owner import Owner
from surplus_ai.database.models.property import Property
from surplus_ai.database.models.surplus_case import SurplusCase
from surplus_ai.leads.case_builder import CaseBuilder
from surplus_ai.leads.compliance_recorder import NO_RULES_VERSION, ComplianceRecorder
from surplus_ai.leads.county_registry import CountyRegistry
from surplus_ai.leads.dedupe import case_identity
from surplus_ai.leads.exceptions import CountyRegistrationError
from surplus_ai.leads.lead_builder import LeadBuilder, RejectionReason
from surplus_ai.leads.owner_builder import OwnerBuilder
from surplus_ai.leads.pipeline import LeadInventory, LeadPipeline
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.county_config import CountyConfig
from surplus_ai.parser.interpretation.models import (
    InterpretedDocument,
    InterpretedRow,
    InterpretedTable,
    RoutingDecision,
    SurplusResolution,
    SurplusSource,
)
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.persistence import DocumentPersister
from surplus_ai.parser.pipeline import ParsingPipeline
from tests.unit.parser.conftest import CORPUS_DIR

TODAY = date(2026, 6, 1)


# --------------------------------------------------------------------------------------
# Builders for interpreted rows, so a test can state exactly the one thing it is about
# --------------------------------------------------------------------------------------


def _row(
    *,
    surplus: Decimal | None = Decimal("5000.00"),
    owner: str | None = "STEFFEN, DEBORAH",
    parcel: str | None = "01-234567",
    sale_date: date | None = date(2024, 3, 1),
    routing: RoutingDecision = RoutingDecision.AUTO_ACCEPT,
    row_index: int = 0,
    extras: dict[CanonicalField, Decimal | date | str | None] | None = None,
    raw_values: dict[str, str] | None = None,
) -> InterpretedRow:
    canonical: dict[CanonicalField, Decimal | date | str | None] = {}
    if parcel is not None:
        canonical[CanonicalField.PARCEL_ID] = parcel
    if owner is not None:
        canonical[CanonicalField.OWNER_NAME] = owner
    if sale_date is not None:
        canonical[CanonicalField.SALE_DATE] = sale_date
    if surplus is not None:
        canonical[CanonicalField.SURPLUS_AMOUNT] = surplus
    canonical.update(extras or {})

    return InterpretedRow(
        raw_values=raw_values or {"Parcel": parcel or "", "Owner": owner or ""},
        canonical_values=canonical,
        surplus_amount=surplus,
        surplus_is_explicit=surplus is not None,
        surplus_source=SurplusSource.EXPLICIT if surplus is not None else SurplusSource.ABSENT,
        surplus_source_column="Surplus" if surplus is not None else None,
        source_pdf_path=Path("/corpus/test.pdf"),
        source_pdf_sha256="0" * 64,
        page_number=1,
        table_index=0,
        row_index_on_page=row_index,
        extraction_method=ExtractionMethod.TEXT,
        extraction_strategy="test",
        confidence=0.95,
        routing=routing,
    )


def _document(*rows: InterpretedRow) -> InterpretedDocument:
    return InterpretedDocument(
        source_path=Path("/corpus/test.pdf"),
        source_sha256="0" * 64,
        tables=(
            InterpretedTable(
                table_index=0,
                original_headers=("Parcel", "Owner"),
                mappings=(),
                surplus=SurplusResolution(source=SurplusSource.EXPLICIT),
                rows=rows,
            ),
        ),
    )


@pytest.fixture
def county(session: Session) -> County:
    county, _ = CountyRegistry(session).register("ZZ", "testville")
    return county


def _verified_rules(state: str = "ZZ") -> StateComplianceRules:
    """A state whose statutes have been recorded, used to test the permissive path."""
    return StateComplianceRules(
        state_code=state,
        state_name="Testland",
        verified=True,
        statute_citations=("Test Code § 1-100",),
        waiting_period_days=0,
        fee_cap_basis=FeeCapBasis.PERCENTAGE_OF_RECOVERY,
        max_contingency_fee_pct=20.0,
    )


class _StubLoader(ComplianceRulesLoader):
    """A loader that answers from memory, so a test never depends on shipped state files."""

    def __init__(self, rules: dict[str, StateComplianceRules]) -> None:
        super().__init__()
        self._rules = rules

    def load_or_none(self, state_code: str) -> StateComplianceRules | None:
        return self._rules.get(state_code.strip().upper())


# --------------------------------------------------------------------------------------
# County registration
# --------------------------------------------------------------------------------------


def test_registering_a_county_twice_reuses_it(session: Session) -> None:
    first, created_first = CountyRegistry(session).register("MD", "harford")
    second, created_second = CountyRegistry(session).register("MD", "harford")

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_the_same_county_name_in_two_states_stays_separate(session: Session) -> None:
    """Several states have a Washington County, and merging them would merge their money."""
    registry = CountyRegistry(session)
    md, _ = registry.register("MD", "washington")
    inn, _ = registry.register("IN", "washington")

    assert md.id != inn.id


def test_a_county_takes_its_details_from_its_config(session: Session) -> None:
    config = CountyConfig(
        county_name="Marion County",
        state="IN",
        fips_code="18097",
        source_type=CountySourceType.BULK_DOWNLOAD,
        source_url="https://example.invalid/list.pdf",
        publishing_frequency=PublishingFrequency.ANNUAL,
    )

    county, _ = CountyRegistry(session).register("IN", "marion", config)

    assert county.name == "Marion County"
    assert county.fips_code == "18097"
    assert county.source_type is CountySourceType.BULK_DOWNLOAD
    assert county.publishing_frequency is PublishingFrequency.ANNUAL


def test_a_county_without_a_config_gets_a_readable_name(session: Session) -> None:
    county, _ = CountyRegistry(session).register("MD", "st-marys")

    assert county.name == "St Marys"
    assert county.source_type is CountySourceType.MANUAL_UPLOAD


def test_registration_does_not_overwrite_an_existing_county(session: Session) -> None:
    """A stale local config must not quietly redescribe a county that is already in use."""
    registry = CountyRegistry(session)
    original, _ = registry.register("IN", "marion", CountyConfig(county_name="Marion", state="IN"))

    again, created = registry.register(
        "IN", "marion", CountyConfig(county_name="Somewhere Else", state="IN")
    )

    assert created is False
    assert again.name == "Marion"
    assert again.id == original.id


def test_a_deliberate_update_does_change_the_county(session: Session) -> None:
    registry = CountyRegistry(session)
    county, _ = registry.register("IN", "marion", CountyConfig(county_name="Marion", state="IN"))

    changed = registry.update_from_config(
        county,
        CountyConfig(
            county_name="Marion County", state="IN", source_type=CountySourceType.BULK_DOWNLOAD
        ),
    )

    assert "name" in changed
    assert county.name == "Marion County"


@pytest.mark.parametrize("state,slug", [("MARYLAND", "harford"), ("M1", "harford"), ("", "x")])
def test_an_unusable_state_code_is_refused(session: Session, state: str, slug: str) -> None:
    with pytest.raises(CountyRegistrationError):
        CountyRegistry(session).register(state, slug)


@pytest.mark.parametrize("slug", ["St. Mary's", "has spaces", ""])
def test_an_unusable_slug_is_refused(session: Session, slug: str) -> None:
    with pytest.raises(CountyRegistrationError):
        CountyRegistry(session).register("MD", slug)


# --------------------------------------------------------------------------------------
# Case identity
# --------------------------------------------------------------------------------------


def test_identity_comes_from_the_published_identifier() -> None:
    identity = case_identity(_row(parcel="01-234567"))

    assert identity.basis == "parcel_id"
    assert identity.identifier == "01-234567"
    assert identity.is_positional is False


def test_the_same_parcel_in_two_sale_years_is_two_cases() -> None:
    """Different sales over different money, even though the property is the same."""
    first = case_identity(_row(parcel="01-234567", sale_date=date(2023, 5, 1)))
    second = case_identity(_row(parcel="01-234567", sale_date=date(2024, 5, 1)))

    assert first.dedupe_hash != second.dedupe_hash


def test_the_same_parcel_and_sale_is_one_case() -> None:
    first = case_identity(_row(parcel="01-234567", owner="STEFFEN, DEBORAH"))
    second = case_identity(_row(parcel="01-234567", owner="STEFFEN DEBORAH A"))

    assert first.dedupe_hash == second.dedupe_hash


def test_a_row_with_no_identifier_falls_back_to_its_contents() -> None:
    """Recorded as positional, because it is a weaker key than a published identifier."""
    identity = case_identity(_row(parcel=None, sale_date=None))

    assert identity.is_positional is True
    assert identity.basis == "row_contents"


def test_two_owners_owed_the_same_amount_do_not_collide() -> None:
    """The alternative to a contents hash -- matching on name and amount -- merges people."""
    first = case_identity(
        _row(parcel=None, sale_date=None, raw_values={"Owner": "SMITH", "Amt": "500"})
    )
    second = case_identity(
        _row(parcel=None, sale_date=None, raw_values={"Owner": "JONES", "Amt": "500"})
    )

    assert first.dedupe_hash != second.dedupe_hash


# --------------------------------------------------------------------------------------
# Cases are records of fact
# --------------------------------------------------------------------------------------


def test_a_case_is_written_even_with_no_surplus(session: Session, county: County) -> None:
    """A county publishing no surplus still published a row, and the row is the record."""
    result = CaseBuilder(session).build(_row(surplus=None), county.id)

    assert result.created is True
    assert result.case.surplus_amount is None


def test_a_missing_surplus_is_never_filled_in_from_another_column(
    session: Session, county: County
) -> None:
    """The parser's most careful rule must not be undone one layer down."""
    row = _row(
        surplus=None,
        extras={
            CanonicalField.WINNING_BID: Decimal("15000.00"),
            CanonicalField.SALE_AMOUNT: Decimal("3743.93"),
        },
    )

    case = CaseBuilder(session).build(row, county.id).case

    assert case.surplus_amount is None
    assert case.winning_bid == Decimal("15000.00")
    assert case.sale_amount_published == Decimal("3743.93")


def test_each_published_money_figure_keeps_its_own_column(session: Session, county: County) -> None:
    row = _row(
        extras={
            CanonicalField.ASSESSED_VALUE: Decimal("120000.00"),
            CanonicalField.TAXES_DUE: Decimal("4200.00"),
            CanonicalField.REFUNDED_AMOUNT: Decimal("1000.00"),
        }
    )

    case = CaseBuilder(session).build(row, county.id).case

    assert case.assessed_value == Decimal("120000.00")
    assert case.taxes_due == Decimal("4200.00")
    assert case.refunded_amount == Decimal("1000.00")
    assert case.surplus_amount == Decimal("5000.00")


def test_rebuilding_the_same_row_updates_rather_than_duplicates(
    session: Session, county: County
) -> None:
    builder = CaseBuilder(session)
    builder.build(_row(surplus=Decimal("5000.00")), county.id)
    second = builder.build(_row(surplus=Decimal("5500.00")), county.id)

    cases = list(session.scalars(select(SurplusCase).where(SurplusCase.county_id == county.id)))
    assert len(cases) == 1
    assert second.created is False
    assert cases[0].surplus_amount == Decimal("5500.00")


def test_a_case_with_no_case_number_is_stored_with_none(session: Session, county: County) -> None:
    """Most counties publish no case number; inventing one would look like their own."""
    case = CaseBuilder(session).build(_row(), county.id).case

    assert case.case_number is None
    assert case.parcel_id == "01-234567"


def test_a_property_is_written_only_when_the_county_described_one(
    session: Session, county: County
) -> None:
    builder = CaseBuilder(session)
    with_parcel = builder.build(_row(parcel="01-234567"), county.id).case
    without = builder.build(_row(parcel=None, sale_date=None, owner="NOBODY"), county.id).case

    assert session.scalar(select(Property).where(Property.surplus_case_id == with_parcel.id))
    assert session.scalar(select(Property).where(Property.surplus_case_id == without.id)) is None


# --------------------------------------------------------------------------------------
# Owners
# --------------------------------------------------------------------------------------


def test_an_owner_is_written_and_classified(session: Session, county: County) -> None:
    case = CaseBuilder(session).build(_row(), county.id).case

    result = OwnerBuilder(session).build(case, _row())

    assert result.owner is not None
    assert result.owner_type is OwnerType.INDIVIDUAL
    assert result.is_pursuable is True


def test_a_joint_cell_stays_one_owner_row(session: Session, county: County) -> None:
    """Splitting "SMITH JOHN & MARY" would invent a boundary the county never drew."""
    row = _row(owner="SMITH JOHN & MARY")
    case = CaseBuilder(session).build(row, county.id).case

    OwnerBuilder(session).build(case, row)

    owners = list(session.scalars(select(Owner).where(Owner.surplus_case_id == case.id)))
    assert len(owners) == 1
    assert owners[0].raw_name == "SMITH JOHN & MARY"


def test_an_unreadable_name_is_stored_rather_than_dropped(session: Session, county: County) -> None:
    row = _row(owner="| &")
    case = CaseBuilder(session).build(row, county.id).case

    result = OwnerBuilder(session).build(case, row)

    assert result.owner is not None
    assert result.owner_type is OwnerType.UNKNOWN
    assert result.is_pursuable is False


def test_a_company_keeps_its_entity_name(session: Session, county: County) -> None:
    row = _row(owner="ACME HOLDINGS LLC")
    case = CaseBuilder(session).build(row, county.id).case

    result = OwnerBuilder(session).build(case, row)

    assert result.owner is not None
    assert result.owner.entity_name == "ACME HOLDINGS LLC"
    assert result.owner.first_name is None


def test_a_purchaser_is_never_read_as_the_owner(session: Session, county: County) -> None:
    """The purchaser took the property; the owner is owed the money. Opposite people."""
    row = _row(owner=None, extras={CanonicalField.PURCHASER_NAME: "BUYER LLC"})
    case = CaseBuilder(session).build(row, county.id).case

    result = OwnerBuilder(session).build(case, row)

    assert result.owner is None


def test_rebuilding_an_owner_does_not_duplicate_it(session: Session, county: County) -> None:
    row = _row()
    case = CaseBuilder(session).build(row, county.id).case
    builder = OwnerBuilder(session)
    builder.build(case, row)
    builder.build(case, row)

    owners = list(session.scalars(select(Owner).where(Owner.surplus_case_id == case.id)))
    assert len(owners) == 1


# --------------------------------------------------------------------------------------
# Compliance is recorded on every case, including the refused ones
# --------------------------------------------------------------------------------------


def test_a_blocked_case_still_gets_an_evaluation(session: Session, county: County) -> None:
    """A blocked case with no stored reason looks exactly like one nobody looked at."""
    case = CaseBuilder(session).build(_row(), county.id).case

    result = ComplianceRecorder(session, loader=_StubLoader({})).record(case, "ZZ", as_of=TODAY)

    stored = session.scalar(
        select(ComplianceEvaluation).where(ComplianceEvaluation.surplus_case_id == case.id)
    )
    assert result.is_eligible is False
    assert stored is not None
    assert stored.is_eligible is False
    assert "no_state_rules" in (stored.evaluation_notes or "")


def test_the_absence_of_rules_is_recorded_as_a_version(session: Session, county: County) -> None:
    case = CaseBuilder(session).build(_row(), county.id).case

    ComplianceRecorder(session, loader=_StubLoader({})).record(case, "ZZ", as_of=TODAY)

    stored = session.scalar(
        select(ComplianceEvaluation).where(ComplianceEvaluation.surplus_case_id == case.id)
    )
    assert stored is not None
    assert stored.state_rule_version == NO_RULES_VERSION


def test_the_rule_version_changes_when_a_statutory_value_changes() -> None:
    baseline = _verified_rules()
    changed = baseline.model_copy(update={"waiting_period_days": 90})

    assert baseline.version_hash() != changed.version_hash()


def test_the_rule_version_ignores_who_checked_it() -> None:
    """Re-checking a file must not make every past verdict look differently decided."""
    baseline = _verified_rules()
    rechecked = baseline.model_copy(
        update={"verified_by": "A. Counsel", "verified_on": date(2026, 1, 1)}
    )

    assert baseline.version_hash() == rechecked.version_hash()


# --------------------------------------------------------------------------------------
# The lead gate: each condition alone is enough to withhold a lead
# --------------------------------------------------------------------------------------


def _decide(
    session: Session,
    county: County,
    row: InterpretedRow,
    rules: StateComplianceRules | None = None,
):
    case = CaseBuilder(session).build(row, county.id).case
    owner = OwnerBuilder(session).build(case, row)
    loader = _StubLoader({"ZZ": rules} if rules else {})
    compliance = ComplianceRecorder(session, loader=loader).record(case, "ZZ", as_of=TODAY)
    return LeadBuilder(session).build(case, owner, row.routing, compliance)


def test_a_case_clears_every_gate_and_becomes_a_lead(session: Session, county: County) -> None:
    decision = _decide(session, county, _row(), _verified_rules())

    assert decision.is_lead is True
    assert decision.created is True


def test_no_surplus_means_no_lead(session: Session, county: County) -> None:
    decision = _decide(session, county, _row(surplus=None), _verified_rules())

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.NO_SURPLUS


def test_a_published_zero_means_no_lead(session: Session, county: County) -> None:
    """A fully refunded overbid genuinely reads 0.00: a real figure, not a missing one."""
    decision = _decide(session, county, _row(surplus=Decimal("0.00")), _verified_rules())

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.SURPLUS_NOT_CLAIMABLE


def test_a_company_owner_means_no_lead(session: Session, county: County) -> None:
    decision = _decide(session, county, _row(owner="ACME HOLDINGS LLC"), _verified_rules())

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.OWNER_NOT_PURSUABLE


def test_an_estate_owner_is_a_lead(session: Session, county: County) -> None:
    """The heirs are entitled to the money and frequently do not know it exists."""
    decision = _decide(session, county, _row(owner="ESTATE OF JERIMIAH GILBERT"), _verified_rules())

    assert decision.is_lead is True


def test_no_owner_means_no_lead(session: Session, county: County) -> None:
    decision = _decide(session, county, _row(owner=None), _verified_rules())

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.NO_OWNER


def test_a_row_awaiting_review_never_becomes_a_lead(session: Session, county: County) -> None:
    """Whatever else is true, an unreviewed reading must not reach a call list."""
    decision = _decide(session, county, _row(routing=RoutingDecision.REVIEW), _verified_rules())

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.NEEDS_REVIEW


def test_an_unverified_state_blocks_an_otherwise_perfect_case(
    session: Session, county: County
) -> None:
    """Everything else lines up. The state's statutes have not been recorded, so it holds."""
    unverified = StateComplianceRules(state_code="ZZ", state_name="Testland", verified=False)

    decision = _decide(session, county, _row(), unverified)

    assert decision.is_lead is False
    assert decision.reason is RejectionReason.COMPLIANCE_BLOCKED
    assert "verified" in decision.detail


def test_a_qualified_case_is_not_duplicated_on_a_second_run(
    session: Session, county: County
) -> None:
    _decide(session, county, _row(), _verified_rules())
    second = _decide(session, county, _row(), _verified_rules())

    leads = list(session.scalars(select(Lead)))
    assert len(leads) == 1
    assert second.created is False


def test_a_rejected_case_records_why_on_the_case_itself(session: Session, county: County) -> None:
    decision = _decide(session, county, _row(surplus=None), _verified_rules())
    case = session.get(SurplusCase, decision.case_id)

    assert case is not None
    assert case.status is SurplusCaseStatus.REJECTED


# --------------------------------------------------------------------------------------
# Promotion: a state coming online converts the backlog
# --------------------------------------------------------------------------------------


def test_promotion_turns_held_cases_into_leads(session: Session) -> None:
    """The point of the whole fail-closed design: nothing is lost while a state is unknown."""
    held = LeadPipeline(session, loader=_StubLoader({})).build(
        _document(_row()), state="ZZ", county_slug="testville", as_of=TODAY
    )
    assert held.leads_total == 0
    assert held.rejections[RejectionReason.COMPLIANCE_BLOCKED] == 1

    online = LeadPipeline(session, loader=_StubLoader({"ZZ": _verified_rules()}))
    report = online.promote(state="ZZ", county_slug="testville", as_of=TODAY)

    assert report.leads_created == 1


def test_promotion_skips_what_can_never_qualify(session: Session, county: County) -> None:
    """A company owner or an absent surplus cannot be fixed by a statute being recorded."""
    CaseBuilder(session).build(_row(surplus=None), county.id)

    report = LeadBuilder(session).promote(county_id=county.id, as_of=TODAY)

    assert report.cases_seen == 0
    assert report.leads_created == 0


# --------------------------------------------------------------------------------------
# The whole pipeline, on a real county
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def harford():
    parsed = ParsingPipeline().parse(CORPUS_DIR / "Harford_County_MD.pdf")
    return parsed, InterpretationPipeline().interpret(parsed, None)


def test_a_real_county_becomes_cases_and_owners(session: Session, harford) -> None:
    parsed, interpreted = harford
    stored = DocumentPersister(session).persist(parsed, interpreted)

    report = LeadPipeline(session).build(
        interpreted,
        state="MD",
        county_slug="harford",
        parsed_document_id=stored.document_id,
        as_of=TODAY,
    )

    assert report.cases_seen == 49
    assert report.cases_created == 49
    assert report.owners_written == 49


def test_the_real_funnel_matches_the_classifier(session: Session, harford) -> None:
    """Harford's 49 rows contain 35 pursuable owners, which Phase 3 pinned independently."""
    _, interpreted = harford

    report = LeadPipeline(session).build(
        interpreted, state="MD", county_slug="harford", as_of=TODAY
    )

    assert report.rejections[RejectionReason.OWNER_NOT_PURSUABLE] == 14
    assert report.rejections[RejectionReason.COMPLIANCE_BLOCKED] == 35


def test_no_county_produces_a_lead_today(session: Session, harford) -> None:
    """Not a defect. No state's statutes have been recorded, so every case is held.

    This test is expected to fail the day a real state is brought online, at which point
    whoever did it should assert the new count deliberately rather than delete the check.
    """
    _, interpreted = harford

    report = LeadPipeline(session).build(
        interpreted, state="MD", county_slug="harford", as_of=TODAY
    )

    assert report.leads_total == 0


def test_running_a_document_twice_creates_no_second_case(session: Session, harford) -> None:
    """Counties republish the same list, and a republished list is not new work."""
    _, interpreted = harford
    pipeline = LeadPipeline(session)
    pipeline.build(interpreted, state="MD", county_slug="harford", as_of=TODAY)

    second = pipeline.build(interpreted, state="MD", county_slug="harford", as_of=TODAY)

    assert second.cases_seen == 49
    assert second.cases_created == 0


def test_the_inventory_reports_what_was_built(session: Session, harford) -> None:
    _, interpreted = harford
    LeadPipeline(session).build(interpreted, state="MD", county_slug="harford", as_of=TODAY)
    county = CountyRegistry(session).find("MD", "harford")
    assert county is not None

    inventory = LeadInventory(session)

    assert inventory.case_count(county.id) == 49
    assert inventory.claimable_case_count(county.id) == 49
    assert inventory.lead_count(county.id) == 0


def test_cases_link_back_to_the_verbatim_row(session: Session, harford) -> None:
    """A figure on a case must always be traceable to the row the county published."""
    parsed, interpreted = harford
    stored = DocumentPersister(session).persist(parsed, interpreted)

    LeadPipeline(session).build(
        interpreted,
        state="MD",
        county_slug="harford",
        parsed_document_id=stored.document_id,
        as_of=TODAY,
    )

    county = CountyRegistry(session).find("MD", "harford")
    assert county is not None
    cases = list(session.scalars(select(SurplusCase).where(SurplusCase.county_id == county.id)))
    assert cases
    assert all(case.raw_row_id is not None for case in cases)


def test_an_unknown_county_promotes_nothing(session: Session) -> None:
    report = LeadPipeline(session).promote(state="ZZ", county_slug="nowhere", as_of=TODAY)

    assert report.cases_seen == 0


def test_a_case_belongs_to_exactly_one_county(session: Session, county: County) -> None:
    """The unique constraint is what stops two counties' identical parcels merging."""
    other, _ = CountyRegistry(session).register("MD", "other")
    builder = CaseBuilder(session)

    builder.build(_row(), county.id)
    builder.build(_row(), other.id)

    assert len(list(session.scalars(select(SurplusCase)))) == 2
