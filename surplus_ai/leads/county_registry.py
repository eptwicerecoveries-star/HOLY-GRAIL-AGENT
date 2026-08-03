from __future__ import annotations

import re

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.county import County
from surplus_ai.database.models.enums import CountySourceType, PublishingFrequency
from surplus_ai.leads.exceptions import CountyRegistrationError
from surplus_ai.parser.interpretation.county_config import CountyConfig, load_county_config

logger = structlog.get_logger(__name__)

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


class CountyRegistry:
    """Finds or creates the county a document's cases belong to.

    A county is identified by state and slug together, never by name: several states have a
    Washington County, and two of them sharing a row would merge unrelated cases into one
    county's figures.

    Registration is idempotent and does not overwrite. Once a county exists, later runs
    reuse it rather than rewriting its details from whatever config happens to be on disk,
    so a stale local file cannot silently change how a county is described. Details are
    changed deliberately through `update_from_config`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, state: str, slug: str) -> County | None:
        state_code, county_slug = _validate(state, slug)
        return self._session.scalar(
            select(County).where(County.state == state_code, County.slug == county_slug)
        )

    def register(
        self, state: str, slug: str, config: CountyConfig | None = None
    ) -> tuple[County, bool]:
        """Return the county for this state and slug, creating it if it is new.

        The second element says whether it was created, so a caller can report a county
        appearing for the first time rather than treating it as routine.
        """
        state_code, county_slug = _validate(state, slug)
        existing = self.find(state_code, county_slug)
        if existing is not None:
            return existing, False

        resolved = config if config is not None else load_county_config(state_code, county_slug)
        county = County(
            slug=county_slug,
            name=_display_name(resolved, county_slug),
            state=state_code,
            fips_code=resolved.fips_code if resolved else None,
            source_type=resolved.source_type if resolved else CountySourceType.MANUAL_UPLOAD,
            source_url=resolved.source_url if resolved else None,
            parsing_profile_key=county_slug,
            # Which state's statutes govern this county's cases. The same as the county's
            # own state in every case seen so far, but kept as its own field because the
            # compliance rules are looked up by it, and conflating the two would leave no
            # way to express a county whose funds are held under another state's rules.
            compliance_state_ref=state_code,
            publishing_frequency=(
                resolved.publishing_frequency if resolved else PublishingFrequency.IRREGULAR
            ),
            is_active=True,
        )
        self._session.add(county)
        self._session.flush()
        logger.info(
            "county_registered",
            state=state_code,
            slug=county_slug,
            name=county.name,
            from_config=resolved is not None,
        )
        return county, True

    def update_from_config(self, county: County, config: CountyConfig) -> tuple[str, ...]:
        """Apply a config file's registration details to an existing county.

        Returns the names of the fields that changed, so the caller can say what moved
        rather than reporting a silent update.
        """
        changed: list[str] = []
        for field, value in (
            ("name", config.county_name.strip() or county.name),
            ("fips_code", config.fips_code),
            ("source_type", config.source_type),
            ("source_url", config.source_url),
            ("publishing_frequency", config.publishing_frequency),
        ):
            if value is None:
                continue
            if getattr(county, field) != value:
                setattr(county, field, value)
                changed.append(field)
        if changed:
            self._session.flush()
            logger.info("county_updated", state=county.state, slug=county.slug, changed=changed)
        return tuple(changed)


def _validate(state: str, slug: str) -> tuple[str, str]:
    state_code = state.strip().upper()
    county_slug = slug.strip().lower()
    if len(state_code) != 2 or not state_code.isalpha():
        raise CountyRegistrationError(
            f"State must be a two-letter code, got {state!r}. Cases are looked up by state "
            "and slug together, so an unusable state code would file them under a county "
            "that does not exist."
        )
    if not _SLUG_PATTERN.match(county_slug):
        raise CountyRegistrationError(
            f"County slug must be lower-case letters, digits, hyphens or underscores, got "
            f"{slug!r}. The slug is also the config filename, so it cannot be free text."
        )
    return state_code, county_slug


def _display_name(config: CountyConfig | None, slug: str) -> str:
    """The county's published name, falling back to a readable form of its slug."""
    if config is not None and config.county_name.strip():
        return config.county_name.strip()
    return slug.replace("-", " ").replace("_", " ").title()
