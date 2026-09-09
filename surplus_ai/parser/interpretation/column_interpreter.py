from __future__ import annotations

from typing import Any

import structlog

from surplus_ai.parser.interpretation.alias_registry import (
    AliasRegistry,
    load_alias_registry,
    load_confidence_config,
    normalize_header,
)
from surplus_ai.parser.interpretation.canonical import (
    MONEY_FIELDS,
    CanonicalField,
    FieldKind,
    kind_of,
)
from surplus_ai.parser.interpretation.county_config import CountyConfig
from surplus_ai.parser.interpretation.models import (
    ColumnMapping,
    MappingMethod,
    SurplusResolution,
)
from surplus_ai.parser.interpretation.type_inference import infer_field_kind

logger = structlog.get_logger(__name__)

# Proposed when value inference identifies a kind but no header names the field. Money is
# absent on purpose: an unlabelled money column must never become a specific money field,
# least of all the surplus.
KIND_DEFAULTS: dict[FieldKind, CanonicalField] = {
    FieldKind.DATE: CanonicalField.SALE_DATE,
    FieldKind.IDENTIFIER: CanonicalField.PARCEL_ID,
    FieldKind.PERSON: CanonicalField.OWNER_NAME,
    FieldKind.ADDRESS: CanonicalField.PROPERTY_ADDRESS,
}

# Whole-word tokens that mean a date column is not the sale date. Value inference
# otherwise maps every unnamed date column to sale_date, which would collapse
# "Balance Date" and a lienholder-expiration column into the sale.
_NON_SALE_DATE_TOKENS = frozenset(
    {
        "balance",
        "expire",
        "expires",
        "expiration",
        "expired",
        "deadline",
        "recorded",
        "recording",
        "filed",
        "filing",
        "notice",
        "lien",
        "lienholder",
        "claim",
        "period",
        "redemption",
        "foreclosure",
        "judgment",
    }
)


class ColumnInterpreter:
    """Resolves each published column to a canonical field, with evidence.

    Precedence, highest first:

    1. a county configuration override -- a human assertion about this county
    2. the surplus resolver's verdict, which is the only route to `surplus_amount`
    3. an exact alias match on the normalized header
    4. a fuzzy alias match above threshold
    5. inference from the column's values, when the header says nothing useful
    6. unresolved -- the column is preserved verbatim and reported, never dropped

    An unresolved column is a visibility problem, not a data problem. It keeps its
    published name and its values, and shows up in `unresolved_headers` so a person can add
    one line to the alias registry and improve every county that follows.
    """

    def __init__(
        self,
        registry: AliasRegistry | None = None,
        confidence_config: dict[str, Any] | None = None,
    ) -> None:
        self._registry = registry or load_alias_registry()
        config = confidence_config or load_confidence_config()
        self._method_confidence: dict[str, float] = {
            str(k): float(v) for k, v in config["method_confidence"].items()
        }
        self._fuzzy_threshold = float(config.get("fuzzy_threshold", 88.0))

    def interpret(
        self,
        headers: tuple[str, ...],
        column_values: dict[str, list[str]],
        surplus: SurplusResolution,
        county_config: CountyConfig | None = None,
    ) -> tuple[ColumnMapping, ...]:
        """Map every published header, in precedence order."""
        mappings: list[ColumnMapping] = []
        for header in headers:
            mappings.append(
                self._interpret_one(
                    header=header,
                    values=column_values.get(header, []),
                    surplus=surplus,
                    county_config=county_config,
                )
            )
        self._warn_on_duplicate_fields(mappings)
        return tuple(mappings)

    def _interpret_one(
        self,
        header: str,
        values: list[str],
        surplus: SurplusResolution,
        county_config: CountyConfig | None,
    ) -> ColumnMapping:
        override = county_config.override_for(header) if county_config else None
        if override is not None:
            return ColumnMapping(
                original_header=header,
                canonical_field=override,
                method=MappingMethod.COUNTY_OVERRIDE,
                confidence=self._confidence(MappingMethod.COUNTY_OVERRIDE),
                evidence="pinned by this county's configuration file",
            )

        exact = self._registry.match_exact(header)
        if exact is not None:
            return ColumnMapping(
                original_header=header,
                canonical_field=exact,
                method=MappingMethod.EXACT_ALIAS,
                confidence=self._confidence(MappingMethod.EXACT_ALIAS),
                evidence="exact match in the global alias registry",
            )

        if surplus.source_column is not None and header.strip() == surplus.source_column.strip():
            return ColumnMapping(
                original_header=header,
                canonical_field=CanonicalField.SURPLUS_AMOUNT,
                method=MappingMethod.SURPLUS_EXPLICIT,
                confidence=self._confidence(MappingMethod.SURPLUS_EXPLICIT),
                evidence=surplus.reason,
            )

        fuzzy = self._registry.match_fuzzy(header, self._fuzzy_threshold)
        if fuzzy is not None:
            field, score = fuzzy
            return ColumnMapping(
                original_header=header,
                canonical_field=field,
                method=MappingMethod.FUZZY_ALIAS,
                confidence=self._confidence(MappingMethod.FUZZY_ALIAS) * (score / 100.0),
                evidence=f"similar to a known alias for {field.value} (score {score:.0f})",
            )

        inferred = self._infer_from_values(header, values)
        if inferred is not None:
            return inferred

        return ColumnMapping(
            original_header=header,
            canonical_field=None,
            method=MappingMethod.UNRESOLVED,
            confidence=self._confidence(MappingMethod.UNRESOLVED),
            evidence=(
                "no alias matched and the values were not distinctive enough to infer a "
                "field; the column is preserved under its published name"
            ),
        )

    def _infer_from_values(self, header: str, values: list[str]) -> ColumnMapping | None:
        """Propose a field from the column's contents when the header is uninformative."""
        kind = infer_field_kind(values)
        if kind is None:
            return None
        if kind is FieldKind.MONEY:
            # Knowing a column holds money says nothing about which money it is, and the
            # costly error in this domain is calling the wrong figure a surplus.
            return None
        if kind is FieldKind.DATE and _header_blocks_sale_date_inference(header):
            return None
        field = KIND_DEFAULTS.get(kind)
        if field is None:
            return None
        return ColumnMapping(
            original_header=header,
            canonical_field=field,
            method=MappingMethod.VALUE_INFERENCE,
            confidence=self._confidence(MappingMethod.VALUE_INFERENCE),
            evidence=f"values in {header!r} look like {kind.value}",
        )

    def _warn_on_duplicate_fields(self, mappings: list[ColumnMapping]) -> None:
        """Two columns claiming one money field means one of them is mislabelled."""
        seen: dict[CanonicalField, str] = {}
        for mapping in mappings:
            field = mapping.canonical_field
            if field is None or field not in MONEY_FIELDS:
                continue
            if field in seen:
                logger.warning(
                    "duplicate_money_field",
                    field=field.value,
                    first=seen[field],
                    second=mapping.original_header,
                )
            else:
                seen[field] = mapping.original_header

    def _confidence(self, method: MappingMethod) -> float:
        return self._method_confidence.get(method.value, 0.5)


def _header_blocks_sale_date_inference(header: str) -> bool:
    """True when the published label names a date that is not the sale date."""
    tokens = set(normalize_header(header).split())
    return bool(tokens & _NON_SALE_DATE_TOKENS)


def money_fields_in(mappings: tuple[ColumnMapping, ...]) -> tuple[CanonicalField, ...]:
    """Which distinct money fields a table resolved, for diagnostics and tests."""
    return tuple(
        m.canonical_field
        for m in mappings
        if m.canonical_field is not None and m.canonical_field in MONEY_FIELDS
    )


def field_kind_for(field: CanonicalField) -> FieldKind:
    return kind_of(field)
