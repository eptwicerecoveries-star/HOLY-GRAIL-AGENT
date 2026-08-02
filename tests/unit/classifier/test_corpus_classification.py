"""Classification against the real corpus, and the funnel it produces.

This is where the phases meet: extraction reads the rows, interpretation decides which
money is claimable, and classification decides which owners are worth contacting. The
number that matters to the business is the intersection.
"""

from __future__ import annotations

import pytest

from surplus_ai.classifier.owner_type_classifier import (
    ClassificationSummary,
    OwnerTypeClassifier,
)
from surplus_ai.database.models.enums import OwnerType
from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.pipeline import ParsingPipeline
from tests.unit.parser.conftest import CORPUS_DIR

COUNTY_CONFIGS = {"Marion_County_IN_2023": ("in", "marion")}


def _owner_names(stem: str) -> list[str]:
    config = None
    if stem in COUNTY_CONFIGS:
        config = load_county_config(*COUNTY_CONFIGS[stem])
    parsed = ParsingPipeline().parse(CORPUS_DIR / f"{stem}.pdf")
    document = InterpretationPipeline().interpret(parsed, config)
    return [
        value
        for table in document.tables
        for row in table.rows
        if isinstance(value := row.value(CanonicalField.OWNER_NAME), str) and value.strip()
    ]


@pytest.fixture(scope="module")
def harford() -> ClassificationSummary:
    return ClassificationSummary(
        OwnerTypeClassifier().classify_many(_owner_names("Harford_County_MD"))
    )


@pytest.fixture(scope="module")
def calvert() -> ClassificationSummary:
    return ClassificationSummary(
        OwnerTypeClassifier().classify_many(_owner_names("Calvert_County_MD"))
    )


@pytest.fixture(scope="module")
def marion() -> ClassificationSummary:
    return ClassificationSummary(
        OwnerTypeClassifier().classify_many(_owner_names("Marion_County_IN_2023"))
    )


def test_every_owner_is_classified(harford: ClassificationSummary) -> None:
    """No name is skipped: each gets a verdict, even if that verdict is unknown."""
    assert harford.total == 49
    assert sum(harford.counts.values()) == 49


def test_calvert_is_mostly_real_homeowners(calvert: ClassificationSummary) -> None:
    """A tax sale of occupied property produces individuals, which is the good case."""
    assert calvert.share(OwnerType.INDIVIDUAL) > 0.7
    assert len(calvert.pursuable) > 60


def test_marion_is_dominated_by_institutional_bidders(marion: ClassificationSummary) -> None:
    """A lien auction is bought by businesses, and most of its rows are not leads.

    Worth asserting because it is the opposite shape from Calvert, and a classifier tuned
    on one would look fine while failing the other.
    """
    assert marion.share(OwnerType.COMPANY) > 0.7
    assert len(marion.excluded) > len(marion.pursuable) * 3


def test_estates_are_found_in_real_data(harford: ClassificationSummary) -> None:
    assert harford.counts[OwnerType.ESTATE] >= 2
    assert all(r.is_pursuable for r in harford.results if r.owner_type is OwnerType.ESTATE)


def test_companies_are_excluded_in_real_data(harford: ClassificationSummary) -> None:
    companies = [r for r in harford.results if r.owner_type is OwnerType.COMPANY]

    assert companies
    assert not any(r.is_pursuable for r in companies)


def test_no_real_person_is_excluded_by_a_substring(marion: ClassificationSummary) -> None:
    """Nothing classified as an individual may carry a company marker, and vice versa."""
    for result in marion.results:
        if result.owner_type is OwnerType.INDIVIDUAL:
            assert result.matched_markers == ()


@pytest.mark.parametrize(
    ("stem", "expected_leads"),
    [("Harford_County_MD", 35), ("Marion_County_IN_2023", 47), ("Calvert_County_MD", 0)],
)
def test_the_funnel_end_to_end(stem: str, expected_leads: int) -> None:
    """Rows that are both claimable and contactable -- the actual output of the system.

    Calvert yields nothing because it publishes no surplus, however many individuals it
    lists. Marion falls from 950 rows to a few dozen because most overbids were already
    refunded and most bidders are companies. Neither reduction is a bug; both are the
    filtering the business needs, and pinning them here means a change that quietly
    loosens either one fails.
    """
    config = None
    if stem in COUNTY_CONFIGS:
        config = load_county_config(*COUNTY_CONFIGS[stem])
    parsed = ParsingPipeline().parse(CORPUS_DIR / f"{stem}.pdf")
    document = InterpretationPipeline().interpret(parsed, config)
    classifier = OwnerTypeClassifier()

    leads = 0
    for table in document.tables:
        for row in table.rows:
            name = row.value(CanonicalField.OWNER_NAME)
            if not isinstance(name, str) or not name.strip():
                continue
            has_money = row.surplus_amount is not None and row.surplus_amount > 0
            if has_money and classifier.classify(name).is_pursuable:
                leads += 1

    assert leads == expected_leads


def test_a_county_publishing_no_surplus_yields_no_leads() -> None:
    """Individuals alone are not a lead. Without a published amount there is nothing to
    claim, and inventing one is precisely what the surplus rule forbids."""
    parsed = ParsingPipeline().parse(CORPUS_DIR / "Calvert_County_MD.pdf")
    document = InterpretationPipeline().interpret(parsed, None)

    assert document.rows_with_claimable_surplus == 0
