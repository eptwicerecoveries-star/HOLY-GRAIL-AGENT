from __future__ import annotations

from collections import defaultdict

import structlog

from surplus_ai.parser.interpretation.alias_registry import (
    SurplusVocabulary,
    load_surplus_vocabulary,
    normalize_header,
)
from surplus_ai.parser.interpretation.county_config import CountyConfig
from surplus_ai.parser.interpretation.models import SurplusResolution, SurplusSource

logger = structlog.get_logger(__name__)


class SurplusResolver:
    """Decides which published column, if any, holds claimable surplus funds.

    This is the most conservative component in the system, deliberately. Every other
    mistake costs an enrichment detail; a mistake here invents money owed to a former
    owner, which sends the business chasing funds that do not exist and, in the opposite
    direction, quietly loses the leads that do.

    Four rules, in order of authority:

    1. A county configuration file wins outright. It is a human assertion about a specific
       county and overrides everything the generic vocabulary would conclude.
    2. Denied labels can never become surplus. A sale price, a winning bid, an assessment
       and an already-refunded amount are not surplus regardless of what else matches.
    3. Explicit terms are matched against the whole normalized header, never fuzzily.
       "sale amount" and "surplus amount" share enough tokens that any similarity measure
       will eventually confuse them, and that confusion is exactly the expensive one.
    4. When several columns lay claim to the same idea, none is chosen. The county is
       reported ambiguous and the amount stays null until a person settles it in config.

    Arithmetic is never used to invent a surplus. A bid exceeding a debt does not imply a
    surplus of the difference: liens, fees and costs are paid first, and the residue is
    whatever the county says it is. Calvert County publishes a bid of $15,000.00 against a
    sale amount of $3,743.93 and states no surplus at all; the correct output there is null.
    """

    def __init__(self, vocabulary: SurplusVocabulary | None = None) -> None:
        self._vocabulary = vocabulary or load_surplus_vocabulary()

    def resolve(
        self, headers: tuple[str, ...], county_config: CountyConfig | None = None
    ) -> SurplusResolution:
        """Determine the surplus column for a table's published headers."""
        denied = tuple(h for h in headers if self._vocabulary.is_denied(h))

        pinned = self._resolve_from_config(headers, county_config, denied)
        if pinned is not None:
            return pinned

        explicit = tuple(h for h in headers if self._vocabulary.is_explicit(h))
        rivals = self._rival_groups(headers)

        contested = self._contested_headers(explicit, rivals)
        if contested:
            logger.info("surplus_ambiguous", candidates=list(contested))
            return SurplusResolution(
                source=SurplusSource.AMBIGUOUS,
                candidates=contested,
                rejected=denied,
                reason=(
                    "More than one published column lays claim to the surplus "
                    f"({', '.join(contested)}). No column is chosen; set surplus_column in "
                    "this county's configuration file to settle it."
                ),
            )

        if len(explicit) == 1:
            column = explicit[0]
            logger.info("surplus_explicit", column=column)
            return SurplusResolution(
                source=SurplusSource.EXPLICIT,
                source_column=column,
                is_explicit=True,
                candidates=explicit,
                rejected=denied,
                reason=f"{column!r} names surplus explicitly and no other column rivals it.",
            )

        logger.info("surplus_absent", headers=len(headers))
        return SurplusResolution(
            source=SurplusSource.ABSENT,
            rejected=denied,
            reason=(
                "No published column names surplus funds. The amount is left null rather "
                "than derived, because the difference between a bid and a debt is not the "
                "surplus."
            ),
        )

    def _resolve_from_config(
        self,
        headers: tuple[str, ...],
        county_config: CountyConfig | None,
        denied: tuple[str, ...],
    ) -> SurplusResolution | None:
        """Apply a county's explicit assertion about its own surplus column."""
        if county_config is None or not county_config.surplus_column_declared:
            return None

        if county_config.surplus_column is None:
            return SurplusResolution(
                source=SurplusSource.ABSENT,
                rejected=denied,
                reason="This county's configuration states that it publishes no surplus column.",
            )

        target = county_config.surplus_column
        match = next((h for h in headers if h.strip() == target.strip()), None)
        if match is None:
            logger.warning("surplus_config_column_missing", expected=target, headers=list(headers))
            return SurplusResolution(
                source=SurplusSource.AMBIGUOUS,
                candidates=(),
                rejected=denied,
                reason=(
                    f"This county's configuration pins {target!r} as the surplus column, but "
                    "no such column was published in this document. The layout may have "
                    "changed; the amount is left null rather than guessed."
                ),
            )

        logger.info("surplus_from_county_config", column=match)
        return SurplusResolution(
            source=SurplusSource.COUNTY_CONFIG,
            source_column=match,
            is_explicit=True,
            candidates=(match,),
            rejected=denied,
            reason=f"This county's configuration pins {match!r} as the surplus column.",
        )

    def _rival_groups(self, headers: tuple[str, ...]) -> dict[str, list[str]]:
        """Group headers by the surplus-ish token they contain.

        Marion County publishes Overbid, Refunded Overbid and Remaining Overbid. Only the
        last is money the county still holds; the gross Overbid on a redeemed parcel has
        already been paid back in full. Exact matching alone would pick the gross figure,
        so the presence of rivals is itself the signal that a human must decide.
        """
        groups: dict[str, list[str]] = defaultdict(list)
        for header in headers:
            token = self._vocabulary.ambiguity_token_in(header)
            if token is not None:
                groups[token].append(header)
        return groups

    def _contested_headers(
        self, explicit: tuple[str, ...], rivals: dict[str, list[str]]
    ) -> tuple[str, ...]:
        """Headers that cannot be told apart, either as rivals or as duplicate claims."""
        if len(explicit) > 1:
            return explicit

        contested: list[str] = []
        for members in rivals.values():
            if len(members) > 1:
                contested.extend(members)

        if not contested:
            return ()

        # Rivals only matter when one of them would otherwise have been chosen.
        if explicit and not any(h in contested for h in explicit):
            return ()
        if not explicit:
            # No column names surplus outright, so nothing was about to be chosen. A
            # cluster of look-alike columns is left to the interpreter's normal aliasing.
            return ()

        seen: set[str] = set()
        ordered: list[str] = []
        for header in contested:
            if header not in seen:
                seen.add(header)
                ordered.append(header)
        return tuple(ordered)


def normalized(header: str) -> str:
    """Exposed for tests and diagnostics."""
    return normalize_header(header)
