"""Tests for the surplus rule.

This is the component where a mistake costs the most: naming the wrong column surplus
invents money that is not owed, and failing to name the right one loses every real lead in
a county. The cases below are written as regression guards against both directions.
"""

from __future__ import annotations

import pytest

from surplus_ai.parser.interpretation.alias_registry import load_surplus_vocabulary
from surplus_ai.parser.interpretation.county_config import CountyConfig, load_county_config
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.interpretation.surplus_resolver import SurplusResolver

CALVERT = ("PARCEL", "OWNER", "LOCATION", "ASSESSMENT", "SALE AMOUNT", "BID AMOUNT")
HARFORD = (
    "TAX SALE YEAR",
    "DATE OF FORECLOSURE",
    "PROPERTY OWNER AT TIME OF SALE",
    "ACCT #",
    "LAST KNOWN ADDRESS",
    "SURPLUS",
)
MARION = (
    "Unique #",
    "Tax Year",
    "Parcel Number",
    "Parcel Status",
    "Sold Date",
    "Entity Name",
    "Face Value",
    "Overbid",
    "Purchase Amount",
    "Refunded Overbid",
    "Remaining Overbid",
)


@pytest.fixture(scope="module")
def resolver() -> SurplusResolver:
    return SurplusResolver()


# --------------------------------------------------------------------------------------
# A county that publishes no surplus
# --------------------------------------------------------------------------------------


def test_no_surplus_column_yields_absent(resolver: SurplusResolver) -> None:
    result = resolver.resolve(CALVERT)

    assert result.source is SurplusSource.ABSENT
    assert result.source_column is None
    assert result.is_explicit is False


def test_surplus_is_never_derived_from_bid_minus_sale(resolver: SurplusResolver) -> None:
    """Calvert bids $15,000.00 against a sale amount of $3,743.93 and states no surplus.

    A surplus plainly exists arithmetically. It is still not ours to compute: liens, fees
    and costs are paid before any residue reaches the former owner, so the difference is
    not the amount owed. The correct output is nothing at all.
    """
    result = resolver.resolve(CALVERT)

    assert result.source is SurplusSource.ABSENT
    assert result.source_column is None
    assert "derived" in result.reason or "difference" in result.reason


def test_denied_columns_are_recorded_as_rejected(resolver: SurplusResolver) -> None:
    result = resolver.resolve(CALVERT)

    assert "SALE AMOUNT" in result.rejected
    assert "BID AMOUNT" in result.rejected
    assert "ASSESSMENT" in result.rejected


# --------------------------------------------------------------------------------------
# A county that names surplus explicitly
# --------------------------------------------------------------------------------------


def test_explicit_surplus_column_is_used(resolver: SurplusResolver) -> None:
    result = resolver.resolve(HARFORD)

    assert result.source is SurplusSource.EXPLICIT
    assert result.source_column == "SURPLUS"
    assert result.is_explicit is True


@pytest.mark.parametrize(
    "header",
    [
        "SURPLUS",
        "Surplus",
        "surplus amount",
        "Excess Funds",
        "EXCESS PROCEEDS",
        "Overage",
        "Overplus",
        "Balance of Bid",
        "Funds Due Owner",
        "Unclaimed Surplus",
    ],
)
def test_explicit_vocabulary_is_recognised(resolver: SurplusResolver, header: str) -> None:
    result = resolver.resolve(("PARCEL", "OWNER", header))

    assert result.source is SurplusSource.EXPLICIT
    assert result.source_column == header


# --------------------------------------------------------------------------------------
# Columns that must never become surplus
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Sale Amount",
        "Sale Price",
        "Winning Bid",
        "High Bid",
        "Bid Amount",
        "Opening Bid",
        "Minimum Bid",
        "Assessment",
        "Assessed Value",
        "Appraised Value",
        "Taxes Due",
        "Judgment Amount",
        "Face Value",
        "Purchase Amount",
        "Refunded Overbid",
        "Court Costs",
    ],
)
def test_denied_headers_never_become_surplus(resolver: SurplusResolver, header: str) -> None:
    result = resolver.resolve(("PARCEL", "OWNER", header))

    assert result.source is SurplusSource.ABSENT
    assert result.source_column is None


def test_similar_wording_does_not_match_by_similarity(resolver: SurplusResolver) -> None:
    """`sale amount` and `surplus amount` share enough tokens to fool a fuzzy matcher.

    Surplus is matched exactly for this reason, so a near miss resolves to nothing rather
    than to money.
    """
    result = resolver.resolve(("PARCEL", "Surplas Amount"))

    assert result.source is SurplusSource.ABSENT


def test_denied_term_wins_over_explicit_term() -> None:
    """A header on both lists is refused. The safe reading wins by construction."""
    vocabulary = load_surplus_vocabulary()

    assert vocabulary.is_denied("Refunded Overbid")
    assert vocabulary.is_explicit("Refunded Overbid") is False


# --------------------------------------------------------------------------------------
# Rival columns: the Marion case
# --------------------------------------------------------------------------------------


def test_rival_overbid_columns_are_ambiguous(resolver: SurplusResolver) -> None:
    """Marion publishes Overbid, Refunded Overbid and Remaining Overbid.

    Exact matching alone would pick the gross Overbid, which on a redeemed parcel has
    already been paid back in full. Nothing is chosen without a human decision.
    """
    result = resolver.resolve(MARION)

    assert result.source is SurplusSource.AMBIGUOUS
    assert result.source_column is None
    assert set(result.candidates) == {"Overbid", "Refunded Overbid", "Remaining Overbid"}


def test_ambiguity_reason_tells_the_operator_what_to_do(resolver: SurplusResolver) -> None:
    result = resolver.resolve(MARION)

    assert "surplus_column" in result.reason
    assert "configuration" in result.reason


def test_county_config_settles_the_ambiguity(resolver: SurplusResolver) -> None:
    config = load_county_config("in", "marion")
    assert config is not None

    result = resolver.resolve(MARION, config)

    assert result.source is SurplusSource.COUNTY_CONFIG
    assert result.source_column == "Remaining Overbid"
    assert result.is_explicit is True


def test_shipped_marion_config_pins_the_column_still_held() -> None:
    """Guards the business decision itself, not just the mechanism.

    Remaining Overbid is money the county still holds. Overbid is the gross figure and
    Refunded Overbid has already left. Repointing this at either would send the team after
    money that is not there.
    """
    config = load_county_config("in", "marion")

    assert config is not None
    assert config.surplus_column == "Remaining Overbid"


def test_bare_balance_is_not_a_global_surplus_term(resolver: SurplusResolver) -> None:
    vocabulary = load_surplus_vocabulary()

    assert not vocabulary.is_explicit("Balance")
    assert not vocabulary.is_explicit("balance")
    result = resolver.resolve(("Tax Deed Number", "Owner Name", "Balance"))
    assert result.source is SurplusSource.ABSENT
    assert result.source_column is None


def test_lee_config_pins_balance_without_a_global_surplus_alias(
    resolver: SurplusResolver,
) -> None:
    config = load_county_config("fl", "lee")
    assert config is not None
    assert config.surplus_column == "Balance"

    result = resolver.resolve(
        (
            "Tax Deed Number",
            "Sale Date",
            "Balance",
            "Balance Date",
            "Property Address",
            "Parcel ID",
            "Owner Name",
            "Lienholder Claim Period Expires",
        ),
        config,
    )

    assert result.source is SurplusSource.COUNTY_CONFIG
    assert result.source_column == "Balance"


# --------------------------------------------------------------------------------------
# County configuration behaviour
# --------------------------------------------------------------------------------------


def test_config_can_assert_a_county_publishes_no_surplus(resolver: SurplusResolver) -> None:
    config = CountyConfig(
        county_name="Test", state="XX", surplus_column=None, surplus_column_declared=True
    )

    result = resolver.resolve(HARFORD, config)

    assert result.source is SurplusSource.ABSENT
    assert result.source_column is None


def test_undeclared_config_falls_back_to_generic_rules(resolver: SurplusResolver) -> None:
    """A config file that simply omits the key must not be read as asserting 'none'."""
    config = CountyConfig(county_name="Test", state="XX", surplus_column_declared=False)

    result = resolver.resolve(HARFORD, config)

    assert result.source is SurplusSource.EXPLICIT
    assert result.source_column == "SURPLUS"


def test_pinned_column_missing_from_document_is_ambiguous(resolver: SurplusResolver) -> None:
    """If a county changes its layout, the old pin must not silently select nothing."""
    config = CountyConfig(
        county_name="Test",
        state="XX",
        surplus_column="Remaining Overbid",
        surplus_column_declared=True,
    )

    result = resolver.resolve(CALVERT, config)

    assert result.source is SurplusSource.AMBIGUOUS
    assert result.source_column is None
    assert "layout may have changed" in result.reason


def test_config_pin_overrides_an_otherwise_explicit_column(resolver: SurplusResolver) -> None:
    headers = ("PARCEL", "SURPLUS", "Remaining Overbid")
    config = CountyConfig(
        county_name="Test",
        state="XX",
        surplus_column="Remaining Overbid",
        surplus_column_declared=True,
    )

    result = resolver.resolve(headers, config)

    assert result.source is SurplusSource.COUNTY_CONFIG
    assert result.source_column == "Remaining Overbid"


# --------------------------------------------------------------------------------------
# Duplicate explicit columns
# --------------------------------------------------------------------------------------


def test_two_explicit_surplus_columns_are_ambiguous(resolver: SurplusResolver) -> None:
    result = resolver.resolve(("PARCEL", "Surplus", "Excess Funds"))

    assert result.source is SurplusSource.AMBIGUOUS
    assert set(result.candidates) == {"Surplus", "Excess Funds"}


def test_empty_header_set_is_absent(resolver: SurplusResolver) -> None:
    result = resolver.resolve(())

    assert result.source is SurplusSource.ABSENT
