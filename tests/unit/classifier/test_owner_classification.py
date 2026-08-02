"""Owner classification.

The brief asks to remove companies and keep individuals. The cases below are drawn from
the real corpus, including the ones where a naive rule gets it wrong in the expensive
direction: turning a person into a company loses a lead permanently.
"""

from __future__ import annotations

import pytest

from surplus_ai.classifier.entity_keywords import (
    load_entity_keywords,
    matches_whole_word,
    normalize_name,
)
from surplus_ai.classifier.exceptions import ClassificationError, KeywordConfigError
from surplus_ai.classifier.models import ClassificationMethodName
from surplus_ai.classifier.owner_type_classifier import (
    ClassificationSummary,
    OwnerTypeClassifier,
)
from surplus_ai.classifier.rules import split_person_name
from surplus_ai.database.models.enums import OwnerType
from surplus_ai.utils.exceptions import AppError


@pytest.fixture(scope="module")
def classifier() -> OwnerTypeClassifier:
    return OwnerTypeClassifier()


# --------------------------------------------------------------------------------------
# Individuals, taken from the corpus
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "GIBSON STEWART D",
        "STEFFEN, DEBORAH ANDERS",
        "WHYTE, WALTER & CATHERINE",
        "BOONE, JEFFREY E.",
        "MC GOWAN, MARY ANN",
        "Selma Patricia Morrow",
        "Elisa P Amezcua",
        "COOPER LONNIE MINNIE",
        "CAVENY CATHERINE H",
        "WILLEY TIMOTHY DAVID",
    ],
)
def test_real_individuals_are_kept(classifier: OwnerTypeClassifier, name: str) -> None:
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.INDIVIDUAL
    assert result.is_pursuable is True


# --------------------------------------------------------------------------------------
# Companies, taken from the corpus
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "WYE RIVER GROUP INC",
        "PINTAIL POINT LLC.",
        "MCCOMAS INSTITUTE",
        "WICOMICO INC",
        "Macallan Properties, LLC",
        "Indiana Tax Auction, LLC",
        "S&C Financial Group, LLC",
        "Marion Assets 2020 LLC",
        "The Tax Lien Hedge LLC",
        "EBAR Investments",
    ],
)
def test_real_companies_are_excluded(classifier: OwnerTypeClassifier, name: str) -> None:
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.COMPANY
    assert result.is_pursuable is False
    assert result.confidence >= 0.9


# --------------------------------------------------------------------------------------
# The expensive mistake: a person read as a company
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "trap"),
    [
        ("VINCENT MARY", "inc"),
        ("COOPER LONNIE MINNIE", "co"),
        ("ALPERT DAVID", "lp"),
        ("INCERTO ANNA", "inc"),
        ("LTDANIEL SARAH", "ltd"),
        ("PCARSON JAMES", "pc"),
    ],
)
def test_markers_inside_a_surname_do_not_make_a_company(
    classifier: OwnerTypeClassifier, name: str, trap: str
) -> None:
    """Substring matching would turn each of these people into a business.

    That is the costly direction: an excluded person is a lead lost silently, whereas an
    included company is merely a wasted call.
    """
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.INDIVIDUAL, f"{name!r} was misread via {trap!r}"
    assert result.is_pursuable is True


def test_whole_word_matching_is_what_prevents_it() -> None:
    assert matches_whole_word("wye river group inc", "inc") is True
    assert matches_whole_word("vincent mary", "inc") is False
    assert matches_whole_word("cooper lonnie", "co") is False


# --------------------------------------------------------------------------------------
# Estates and trusts are leads, not companies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["ESTATE OF JERIMIAH GILBERT", "ESTATE OF LOIS BYRD", "HEIRS OF MARY SMITH"]
)
def test_estates_are_pursued(classifier: OwnerTypeClassifier, name: str) -> None:
    """An estate is arguably the best lead there is.

    The owner has died, the heirs are entitled to the money and are often unaware it
    exists. Folding estates into "not an individual" would discard exactly those.
    """
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.ESTATE
    assert result.is_pursuable is True


@pytest.mark.parametrize(
    "name", ["SMITH FAMILY REVOCABLE TRUST", "JONES LIVING TRUST", "MILLER TRUST"]
)
def test_trusts_are_pursued(classifier: OwnerTypeClassifier, name: str) -> None:
    """A trust has a human trustee to contact, so it is not simply discarded."""
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.TRUST
    assert result.is_pursuable is True


def test_an_estate_is_not_read_as_a_company(classifier: OwnerTypeClassifier) -> None:
    assert classifier.classify("ESTATE OF JERIMIAH GILBERT").owner_type is not OwnerType.COMPANY


# --------------------------------------------------------------------------------------
# Government
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["CALVERT COUNTY", "CITY OF BALTIMORE", "STATE OF MARYLAND", "HOUSING AUTHORITY"]
)
def test_government_owners_are_excluded(classifier: OwnerTypeClassifier, name: str) -> None:
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.GOVERNMENT
    assert result.is_pursuable is False


def test_government_is_checked_before_company(classifier: OwnerTypeClassifier) -> None:
    """ "County" would otherwise read as an ordinary business name."""
    assert classifier.classify("CALVERT COUNTY").owner_type is OwnerType.GOVERNMENT


# --------------------------------------------------------------------------------------
# Names that cannot be read
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["&", "| &", "_ | &", "", "   ", "---"])
def test_unreadable_names_are_unknown_not_guessed(
    classifier: OwnerTypeClassifier, name: str
) -> None:
    """Recognition leaves fragments behind; they must not become leads.

    They are recorded as unknown at zero confidence rather than dropped, which keeps them
    out of a call list without deleting the record.
    """
    result = classifier.classify(name)

    assert result.owner_type is OwnerType.UNKNOWN
    assert result.confidence == 0.0
    assert result.is_pursuable is False


def test_a_lone_surname_is_not_actionable(classifier: OwnerTypeClassifier) -> None:
    """ "MORDELL" alone came out of the scanned county; one token is not enough to call."""
    assert classifier.classify("MORDELL").owner_type is OwnerType.UNKNOWN


def test_a_very_long_string_is_not_a_person(classifier: OwnerTypeClassifier) -> None:
    assert classifier.classify("WORD " * 40).owner_type is OwnerType.UNKNOWN


# --------------------------------------------------------------------------------------
# Confidence and evidence
# --------------------------------------------------------------------------------------


def test_marker_matches_are_more_confident_than_inferred_people(
    classifier: OwnerTypeClassifier,
) -> None:
    """A suffix is positive evidence; a person is inferred from shape alone."""
    company = classifier.classify("PINTAIL POINT LLC.")
    person = classifier.classify("GIBSON STEWART D")

    assert company.confidence > person.confidence
    assert person.confidence > 0.5


def test_evidence_names_the_marker_that_decided(classifier: OwnerTypeClassifier) -> None:
    result = classifier.classify("WYE RIVER GROUP INC")

    assert "inc" in result.matched_markers or "group" in result.matched_markers
    assert result.evidence


def test_method_is_recorded(classifier: OwnerTypeClassifier) -> None:
    assert classifier.classify("GIBSON STEWART D").method is ClassificationMethodName.RULE


# --------------------------------------------------------------------------------------
# Name splitting
# --------------------------------------------------------------------------------------


def test_comma_form_splits(classifier: OwnerTypeClassifier) -> None:
    name = split_person_name("STEFFEN, DEBORAH ANDERS", load_entity_keywords())

    assert name.last == "STEFFEN"
    assert name.first == "DEBORAH"
    assert name.middle == "ANDERS"


def test_surname_first_without_a_comma_splits(classifier: OwnerTypeClassifier) -> None:
    name = split_person_name("GIBSON STEWART D", load_entity_keywords())

    assert name.last == "GIBSON"
    assert name.first == "STEWART"


def test_joint_owners_keep_every_claimant() -> None:
    """The money is owed to both parties, so the second must not be dropped."""
    name = split_person_name("WHYTE, WALTER & CATHERINE", load_entity_keywords())

    assert name.last == "WHYTE"
    assert name.first == "WALTER"
    assert name.additional_parties == ("CATHERINE",)


def test_suffixes_are_separated() -> None:
    name = split_person_name("JONES LAMBERTINE JR", load_entity_keywords())

    assert name.last == "JONES"
    assert name.suffix == "JR"


def test_the_raw_name_is_always_kept() -> None:
    name = split_person_name("MC GOWAN, MARY ANN", load_entity_keywords())

    assert name.raw == "MC GOWAN, MARY ANN"


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def test_pursuit_is_configuration_not_code() -> None:
    keywords = load_entity_keywords()

    assert keywords.is_pursuable(OwnerType.INDIVIDUAL) is True
    assert keywords.is_pursuable(OwnerType.ESTATE) is True
    assert keywords.is_pursuable(OwnerType.COMPANY) is False
    assert keywords.is_pursuable(OwnerType.UNKNOWN) is False


def test_keyword_config_loads() -> None:
    keywords = load_entity_keywords()

    assert "llc" in keywords.company_markers
    assert "estate" in keywords.estate_markers
    assert len(keywords.government_markers) > 5


def test_normalization_keeps_ampersands() -> None:
    """ "&" separates two owners, so erasing it would merge two claimants into one."""
    assert "&" in normalize_name("WHYTE, WALTER & CATHERINE")


def test_classification_errors_are_app_errors() -> None:
    assert issubclass(ClassificationError, AppError)
    assert issubclass(KeywordConfigError, ClassificationError)


# --------------------------------------------------------------------------------------
# Batch summary
# --------------------------------------------------------------------------------------


def test_summary_separates_pursuable_from_excluded(classifier: OwnerTypeClassifier) -> None:
    names = [
        "GIBSON STEWART D",
        "PINTAIL POINT LLC.",
        "ESTATE OF LOIS BYRD",
        "CALVERT COUNTY",
        "&",
    ]

    summary = ClassificationSummary(classifier.classify_many(names))

    assert summary.total == 5
    assert len(summary.pursuable) == 2
    assert len(summary.excluded) == 3
    assert summary.counts[OwnerType.COMPANY] == 1


def test_summary_handles_an_empty_batch() -> None:
    summary = ClassificationSummary([])

    assert summary.total == 0
    assert summary.share(OwnerType.INDIVIDUAL) == 0.0
