"""End-to-end interpretation against the real county corpus.

The extraction tests prove the right cells were read. These prove the right meaning was
attached to them, and in particular that no money is invented and none is lost.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from surplus_ai.parser.interpretation.canonical import CanonicalField
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.parser.interpretation.models import (
    InterpretedDocument,
    RoutingDecision,
    SurplusSource,
)
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.pipeline import ParsingPipeline
from tests.unit.parser.conftest import CORPUS_DIR

# Counties with a shipped configuration file, mapped to where it lives.
COUNTY_CONFIGS: dict[str, tuple[str, str]] = {
    "Marion_County_IN_2023": ("in", "marion"),
    "Marion_County_IN_2024": ("in", "marion"),
}

INTERPRETABLE = [
    "Calvert_County_MD",
    "Harford_County_MD",
    "Marion_County_IN_2023",
    "Marion_County_IN_2024",
]


@pytest.fixture(scope="module")
def documents() -> dict[str, InterpretedDocument]:
    """Parse and interpret each county once, with its configuration applied."""
    parsing, interpreting = ParsingPipeline(), InterpretationPipeline()
    results: dict[str, InterpretedDocument] = {}
    for stem in INTERPRETABLE:
        parsed = parsing.parse(CORPUS_DIR / f"{stem}.pdf")
        config = None
        if stem in COUNTY_CONFIGS:
            config = load_county_config(*COUNTY_CONFIGS[stem])
        results[stem] = interpreting.interpret(parsed, config)
    return results


@pytest.fixture(scope="module")
def marion_unconfigured() -> InterpretedDocument:
    """Marion with no county configuration, to prove the cold-start behaviour."""
    parsed = ParsingPipeline().parse(CORPUS_DIR / "Marion_County_IN_2023.pdf")
    return InterpretationPipeline().interpret(parsed, None)


# --------------------------------------------------------------------------------------
# Nothing is lost in interpretation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_row_counts_survive_interpretation(
    stem: str, documents: dict[str, InterpretedDocument]
) -> None:
    expected = {
        "Calvert_County_MD": 96,
        "Harford_County_MD": 49,
        "Marion_County_IN_2023": 950,
        "Marion_County_IN_2024": 917,
    }
    assert documents[stem].total_rows == expected[stem]


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_verbatim_values_are_retained_alongside_canonical_ones(
    stem: str, documents: dict[str, InterpretedDocument]
) -> None:
    """The county's own wording stays available after mapping."""
    table = documents[stem].tables[0]
    row = table.rows[0]

    assert set(table.original_headers) <= set(row.raw_values)


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_every_published_column_is_accounted_for(
    stem: str, documents: dict[str, InterpretedDocument]
) -> None:
    """Every header either maps to a field or is reported unresolved. None vanishes."""
    table = documents[stem].tables[0]

    assert len(table.mappings) == len(table.original_headers)
    assert {m.original_header for m in table.mappings} == set(table.original_headers)


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_corpus_columns_all_resolve(stem: str, documents: dict[str, InterpretedDocument]) -> None:
    """Every column in the current corpus is understood.

    An unresolved column is not a failure of the design -- it is preserved either way --
    but on a corpus this small it means an alias is missing, so it is worth failing on.
    """
    assert documents[stem].tables[0].unresolved_headers == ()


# --------------------------------------------------------------------------------------
# A county that publishes no surplus
# --------------------------------------------------------------------------------------


def test_calvert_has_no_surplus_and_none_is_invented(
    documents: dict[str, InterpretedDocument],
) -> None:
    document = documents["Calvert_County_MD"]
    table = document.tables[0]

    assert table.surplus.source is SurplusSource.ABSENT
    assert document.rows_with_surplus == 0
    assert all(row.surplus_amount is None for row in table.rows)
    assert all(row.surplus_is_explicit is False for row in table.rows)


def test_calvert_keeps_its_three_money_columns(
    documents: dict[str, InterpretedDocument],
) -> None:
    """The published figures survive even though none of them is the surplus."""
    row = documents["Calvert_County_MD"].tables[0].rows[0]

    assert row.value(CanonicalField.ASSESSED_VALUE) == Decimal("133000")
    assert row.value(CanonicalField.SALE_AMOUNT) == Decimal("3743.93")
    assert row.value(CanonicalField.WINNING_BID) == Decimal("15000.00")
    assert row.surplus_amount is None


def test_calvert_bid_exceeds_sale_yet_surplus_stays_null(
    documents: dict[str, InterpretedDocument],
) -> None:
    """The arithmetic temptation, made explicit as a regression guard."""
    row = documents["Calvert_County_MD"].tables[0].rows[0]
    bid = row.value(CanonicalField.WINNING_BID)
    sale = row.value(CanonicalField.SALE_AMOUNT)

    assert isinstance(bid, Decimal) and isinstance(sale, Decimal)
    assert bid > sale
    assert row.surplus_amount is None


# --------------------------------------------------------------------------------------
# A county that names surplus explicitly
# --------------------------------------------------------------------------------------


def test_harford_surplus_is_explicit(documents: dict[str, InterpretedDocument]) -> None:
    document = documents["Harford_County_MD"]
    table = document.tables[0]

    assert table.surplus.source is SurplusSource.EXPLICIT
    assert table.surplus.source_column == "SURPLUS"
    assert document.rows_with_surplus == 49


def test_harford_amounts_are_typed(documents: dict[str, InterpretedDocument]) -> None:
    row = documents["Harford_County_MD"].tables[0].rows[0]

    assert row.surplus_amount == Decimal("491.33")
    assert row.surplus_is_explicit is True
    assert row.surplus_source is SurplusSource.EXPLICIT


def test_harford_owner_and_address_map(documents: dict[str, InterpretedDocument]) -> None:
    row = documents["Harford_County_MD"].tables[0].rows[0]

    assert row.value(CanonicalField.OWNER_NAME) == "ESTATE OF JERIMIAH GILBERT"
    assert row.value(CanonicalField.PARCEL_ID) == "01-038036"
    assert row.value(CanonicalField.OWNER_MAILING_ADDRESS) == (
        "608 DEMBYTOWN ROAD, JOPPA, MD 21085"
    )


def test_acct_hash_maps_to_parcel_id(documents: dict[str, InterpretedDocument]) -> None:
    """ "ACCT #" and "Parcel Number" are the same idea in different counties."""
    harford = documents["Harford_County_MD"].tables[0]
    marion = documents["Marion_County_IN_2023"].tables[0]

    assert CanonicalField.PARCEL_ID in harford.resolved_fields
    assert CanonicalField.PARCEL_ID in marion.resolved_fields


# --------------------------------------------------------------------------------------
# The Marion case: rival columns and the money that is actually left
# --------------------------------------------------------------------------------------


def test_marion_without_config_refuses_to_choose(
    marion_unconfigured: InterpretedDocument,
) -> None:
    table = marion_unconfigured.tables[0]

    assert table.surplus.source is SurplusSource.AMBIGUOUS
    assert marion_unconfigured.rows_with_surplus == 0
    assert all(row.surplus_amount is None for row in table.rows)


def test_marion_without_config_is_never_auto_accepted(
    marion_unconfigured: InterpretedDocument,
) -> None:
    """An unresolved surplus must not be presented as a row ready to work."""
    counts = marion_unconfigured.routing_counts()

    assert counts[RoutingDecision.AUTO_ACCEPT] == 0
    assert counts[RoutingDecision.REVIEW] == 950


def test_marion_with_config_uses_the_money_still_held(
    documents: dict[str, InterpretedDocument],
) -> None:
    table = documents["Marion_County_IN_2023"].tables[0]

    assert table.surplus.source is SurplusSource.COUNTY_CONFIG
    assert table.surplus.source_column == "Remaining Overbid"


@pytest.mark.parametrize(
    ("stem", "claimable"),
    [("Marion_County_IN_2023", 130), ("Marion_County_IN_2024", 139)],
)
def test_marion_claimable_counts(
    stem: str, claimable: int, documents: dict[str, InterpretedDocument]
) -> None:
    """Most Marion records have already been refunded and are not opportunities.

    Counting every row with a figure would report 950 leads where 130 exist. Counting the
    gross Overbid instead would report money that has already gone back to the bidder.
    """
    document = documents[stem]

    assert document.rows_with_claimable_surplus == claimable
    assert document.rows_with_surplus > claimable


def test_marion_refunded_row_reads_zero_not_missing(
    documents: dict[str, InterpretedDocument],
) -> None:
    """A refunded parcel has a real $0.00, which is different from an unknown amount."""
    row = documents["Marion_County_IN_2023"].tables[0].rows[0]

    assert row.raw_values["Refunded Overbid"] == "$3,203.00"
    assert row.raw_values["Remaining Overbid"] == "$0.00"
    assert row.surplus_amount == Decimal("0.00")
    assert row.surplus_amount is not None


def test_marion_money_columns_keep_their_own_identities(
    documents: dict[str, InterpretedDocument],
) -> None:
    """Designating a surplus column does not overwrite what the county calls it."""
    row = documents["Marion_County_IN_2023"].tables[0].rows[0]

    assert row.value(CanonicalField.FACE_VALUE_AMOUNT) == Decimal("5118.74")
    assert row.value(CanonicalField.OVERBID_AMOUNT) == Decimal("3203.00")
    assert row.value(CanonicalField.PURCHASE_AMOUNT) == Decimal("8321.74")
    assert row.value(CanonicalField.REFUNDED_AMOUNT) == Decimal("3203.00")
    assert row.value(CanonicalField.REMAINING_AMOUNT) == Decimal("0.00")


def test_marion_arithmetic_is_internally_consistent(
    documents: dict[str, InterpretedDocument],
) -> None:
    """Face value plus overbid equals purchase amount, which confirms the column mapping.

    This is what settles the wrapped-header question: had the labels been misassigned, the
    sum would not hold.
    """
    row = documents["Marion_County_IN_2023"].tables[0].rows[0]
    face = row.value(CanonicalField.FACE_VALUE_AMOUNT)
    overbid = row.value(CanonicalField.OVERBID_AMOUNT)
    purchase = row.value(CanonicalField.PURCHASE_AMOUNT)

    assert isinstance(face, Decimal)
    assert isinstance(overbid, Decimal)
    assert isinstance(purchase, Decimal)
    assert face + overbid == purchase


def test_marion_dates_survive_their_timestamps(
    documents: dict[str, InterpretedDocument],
) -> None:
    from datetime import date as date_type

    row = documents["Marion_County_IN_2023"].tables[0].rows[0]

    assert row.raw_values["Sold Date"] == "10/02/2023 09:40:45 AM EDT"
    assert row.value(CanonicalField.SALE_DATE) == date_type(2023, 10, 2)


# --------------------------------------------------------------------------------------
# Corpus-wide safety property
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_surplus_only_ever_comes_from_a_named_column(
    stem: str, documents: dict[str, InterpretedDocument]
) -> None:
    """The core safety property: an amount exists only when a column supplied it."""
    for table in documents[stem].tables:
        for row in table.rows:
            if row.surplus_amount is not None:
                assert row.surplus_source_column is not None
                assert row.surplus_source_column in row.raw_values
            else:
                assert row.surplus_source in {
                    SurplusSource.ABSENT,
                    SurplusSource.AMBIGUOUS,
                }


@pytest.mark.parametrize("stem", INTERPRETABLE)
def test_surplus_value_equals_its_source_column(
    stem: str, documents: dict[str, InterpretedDocument]
) -> None:
    """The figure is copied from the designated column, never computed."""
    from surplus_ai.parser.interpretation.type_inference import parse_money

    for table in documents[stem].tables:
        column = table.surplus.source_column
        if column is None:
            continue
        for row in table.rows[:50]:
            assert row.surplus_amount == parse_money(row.raw_values[column])


def test_corpus_has_a_county_of_each_surplus_kind(
    documents: dict[str, InterpretedDocument], marion_unconfigured: InterpretedDocument
) -> None:
    """The corpus exercises all three outcomes, so none of them is untested."""
    sources = {documents[stem].tables[0].surplus.source for stem in INTERPRETABLE}
    sources.add(marion_unconfigured.tables[0].surplus.source)

    assert SurplusSource.ABSENT in sources
    assert SurplusSource.EXPLICIT in sources
    assert SurplusSource.COUNTY_CONFIG in sources
    assert SurplusSource.AMBIGUOUS in sources


def test_corpus_dir_is_the_shared_one() -> None:
    assert CORPUS_DIR.is_dir()
    assert isinstance(CORPUS_DIR, Path)
