from __future__ import annotations

from enum import Enum


class FieldKind(str, Enum):
    """What sort of value a canonical field holds, used to drive type inference."""

    MONEY = "money"
    DATE = "date"
    IDENTIFIER = "identifier"
    PERSON = "person"
    ADDRESS = "address"
    TEXT = "text"


class CanonicalField(str, Enum):
    """The universal schema every county's columns map into.

    The money fields are deliberately a superset with one field per published concept.
    Collapsing them would destroy the distinction that matters most in this domain: a sale
    price, a winning bid, an assessment and a surplus are different numbers, and only one
    of them is money owed back to a former owner. A county that publishes several of them
    keeps several of them.
    """

    # Identity
    PARCEL_ID = "parcel_id"
    CASE_NUMBER = "case_number"
    CERTIFICATE_NUMBER = "certificate_number"
    UNIQUE_ID = "unique_id"
    TAX_YEAR = "tax_year"

    # Parties
    OWNER_NAME = "owner_name"
    OWNER_MAILING_ADDRESS = "owner_mailing_address"
    PURCHASER_NAME = "purchaser_name"

    # Property
    PROPERTY_ADDRESS = "property_address"
    PROPERTY_CITY = "property_city"
    PROPERTY_ZIP = "property_zip"
    LEGAL_DESCRIPTION = "legal_description"

    # Dates
    SALE_DATE = "sale_date"
    FORECLOSURE_DATE = "foreclosure_date"
    REDEMPTION_DEADLINE = "redemption_deadline"
    CLAIM_DEADLINE = "claim_deadline"

    # Money -- each published concept keeps its own field
    SURPLUS_AMOUNT = "surplus_amount"
    SALE_AMOUNT = "sale_amount"
    WINNING_BID = "winning_bid"
    OPENING_BID = "opening_bid"
    JUDGMENT_AMOUNT = "judgment_amount"
    ASSESSED_VALUE = "assessed_value"
    APPRAISED_VALUE = "appraised_value"
    TAXES_DUE = "taxes_due"
    FEES_AMOUNT = "fees_amount"
    FACE_VALUE_AMOUNT = "face_value_amount"
    OVERBID_AMOUNT = "overbid_amount"
    PURCHASE_AMOUNT = "purchase_amount"
    REFUNDED_AMOUNT = "refunded_amount"
    REMAINING_AMOUNT = "remaining_amount"

    # Status and free text
    PARCEL_STATUS = "parcel_status"
    NOTES = "notes"


FIELD_KINDS: dict[CanonicalField, FieldKind] = {
    CanonicalField.PARCEL_ID: FieldKind.IDENTIFIER,
    CanonicalField.CASE_NUMBER: FieldKind.IDENTIFIER,
    CanonicalField.CERTIFICATE_NUMBER: FieldKind.IDENTIFIER,
    CanonicalField.UNIQUE_ID: FieldKind.IDENTIFIER,
    CanonicalField.TAX_YEAR: FieldKind.IDENTIFIER,
    CanonicalField.OWNER_NAME: FieldKind.PERSON,
    CanonicalField.OWNER_MAILING_ADDRESS: FieldKind.ADDRESS,
    CanonicalField.PURCHASER_NAME: FieldKind.PERSON,
    CanonicalField.PROPERTY_ADDRESS: FieldKind.ADDRESS,
    CanonicalField.PROPERTY_CITY: FieldKind.TEXT,
    CanonicalField.PROPERTY_ZIP: FieldKind.IDENTIFIER,
    CanonicalField.LEGAL_DESCRIPTION: FieldKind.TEXT,
    CanonicalField.SALE_DATE: FieldKind.DATE,
    CanonicalField.FORECLOSURE_DATE: FieldKind.DATE,
    CanonicalField.REDEMPTION_DEADLINE: FieldKind.DATE,
    CanonicalField.CLAIM_DEADLINE: FieldKind.DATE,
    CanonicalField.SURPLUS_AMOUNT: FieldKind.MONEY,
    CanonicalField.SALE_AMOUNT: FieldKind.MONEY,
    CanonicalField.WINNING_BID: FieldKind.MONEY,
    CanonicalField.OPENING_BID: FieldKind.MONEY,
    CanonicalField.JUDGMENT_AMOUNT: FieldKind.MONEY,
    CanonicalField.ASSESSED_VALUE: FieldKind.MONEY,
    CanonicalField.APPRAISED_VALUE: FieldKind.MONEY,
    CanonicalField.TAXES_DUE: FieldKind.MONEY,
    CanonicalField.FEES_AMOUNT: FieldKind.MONEY,
    CanonicalField.FACE_VALUE_AMOUNT: FieldKind.MONEY,
    CanonicalField.OVERBID_AMOUNT: FieldKind.MONEY,
    CanonicalField.PURCHASE_AMOUNT: FieldKind.MONEY,
    CanonicalField.REFUNDED_AMOUNT: FieldKind.MONEY,
    CanonicalField.REMAINING_AMOUNT: FieldKind.MONEY,
    CanonicalField.PARCEL_STATUS: FieldKind.TEXT,
    CanonicalField.NOTES: FieldKind.TEXT,
}

MONEY_FIELDS: frozenset[CanonicalField] = frozenset(
    field for field, kind in FIELD_KINDS.items() if kind is FieldKind.MONEY
)


def kind_of(field: CanonicalField) -> FieldKind:
    return FIELD_KINDS[field]
