from __future__ import annotations

import structlog

from surplus_ai.parser.profiles.models import CountyProfile
from surplus_ai.parser.strategies.base import AbstractExtractionStrategy

logger = structlog.get_logger(__name__)


class ProfileResolver:
    """Turns what a county taught us last time into a head start for the next document.

    A profile is a prior, never a decision. It reorders the strategy cascade so the
    extractor that worked before is tried first, and it tells the caller which columns to
    expect. It cannot force an outcome: the winning strategy is still whichever scores best
    structurally, so a county that changes its layout is re-read correctly rather than
    forced into last year's shape.

    That distinction is what keeps the system honest as it accumulates profiles. A prior
    that could override evidence would make every future document look like the first one.
    """

    def order_strategies(
        self,
        strategies: list[AbstractExtractionStrategy],
        profile: CountyProfile | None,
    ) -> list[AbstractExtractionStrategy]:
        """Put the strategy that worked last time first, keeping the rest in order.

        This is a cost optimisation, not a correctness one. Every strategy still runs
        during selection; trying the likely winner first simply gets to a good score
        sooner.
        """
        if profile is None:
            return strategies
        preferred = profile.required_parsing_strategy
        if not preferred or not any(s.name == preferred for s in strategies):
            return strategies
        ordered = [s for s in strategies if s.name == preferred]
        ordered.extend(s for s in strategies if s.name != preferred)
        logger.debug("strategy_order_from_profile", preferred=preferred)
        return ordered

    def expected_columns(self, profile: CountyProfile | None) -> tuple[str, ...]:
        """The column names this county published last time."""
        return profile.original_column_names if profile else ()

    def layout_changed(self, profile: CountyProfile | None, headers: tuple[str, ...]) -> bool:
        """Whether the columns just read differ from the ones on record.

        Worth surfacing rather than absorbing: a county quietly renaming or adding a column
        is exactly the moment a pinned surplus column can start pointing at nothing.
        """
        if profile is None or not profile.original_column_names:
            return False
        return _normalized(profile.original_column_names) != _normalized(headers)

    def describe_drift(
        self, profile: CountyProfile | None, headers: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Columns that disappeared and columns that appeared, against the profile."""
        if profile is None:
            return (), ()
        known = set(_normalized(profile.original_column_names))
        seen = set(_normalized(headers))
        missing = tuple(
            h for h in profile.original_column_names if h.strip().casefold() not in seen
        )
        added = tuple(h for h in headers if h.strip().casefold() not in known)
        return missing, added


def _normalized(headers: tuple[str, ...]) -> list[str]:
    return [h.strip().casefold() for h in headers]
