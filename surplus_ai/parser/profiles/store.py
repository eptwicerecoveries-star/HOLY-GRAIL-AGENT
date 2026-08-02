from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.parsing_profile import ParsingProfileVersion
from surplus_ai.parser.profiles.models import CountyProfile, StoredProfileVersion

logger = structlog.get_logger(__name__)


class ProfileStore:
    """Persists county profiles as an append-only history.

    A stored profile is never edited. Seeing the same layout again records another
    observation against the existing version; seeing a different layout writes a new
    version and marks the previous one superseded. Nothing is deleted, so any past parse
    stays reproducible and a layout change is visible rather than silently absorbed.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self, county_id: uuid.UUID, profile: CountyProfile, source_file_hash: str | None = None
    ) -> StoredProfileVersion:
        """Record an observation of a profile, creating a version only if it is new."""
        version_hash = profile.version_hash()
        existing = self._session.scalar(
            select(ParsingProfileVersion).where(
                ParsingProfileVersion.county_id == county_id,
                ParsingProfileVersion.version_hash == version_hash,
            )
        )

        if existing is not None:
            self._record_observation(existing, source_file_hash)
            logger.info(
                "profile_observation_recorded",
                county_id=str(county_id),
                version=version_hash[:12],
                times_observed=existing.times_observed,
            )
            return _to_stored(existing, profile)

        self._supersede_previous(county_id)
        row = ParsingProfileVersion(
            county_id=county_id,
            version_hash=version_hash,
            profile_json=_serialize(profile),
            times_observed=1,
            observed_file_hashes=[source_file_hash] if source_file_hash else [],
            is_approved=False,
            superseded=False,
        )
        self._session.add(row)
        self._session.flush()
        logger.info("profile_version_created", county_id=str(county_id), version=version_hash[:12])
        return _to_stored(row, profile)

    def latest(self, county_id: uuid.UUID) -> StoredProfileVersion | None:
        """The newest version that has not been superseded."""
        row = self._session.scalar(
            select(ParsingProfileVersion)
            .where(
                ParsingProfileVersion.county_id == county_id,
                ParsingProfileVersion.superseded.is_(False),
            )
            .order_by(ParsingProfileVersion.created_at.desc())
        )
        return _to_stored(row, None) if row is not None else None

    def latest_approved(self, county_id: uuid.UUID) -> StoredProfileVersion | None:
        """The newest version a person has approved, which is what priors are drawn from."""
        row = self._session.scalar(
            select(ParsingProfileVersion)
            .where(
                ParsingProfileVersion.county_id == county_id,
                ParsingProfileVersion.is_approved.is_(True),
            )
            .order_by(ParsingProfileVersion.created_at.desc())
        )
        return _to_stored(row, None) if row is not None else None

    def history(self, county_id: uuid.UUID) -> list[StoredProfileVersion]:
        """Every version ever recorded for a county, oldest first."""
        rows = self._session.scalars(
            select(ParsingProfileVersion)
            .where(ParsingProfileVersion.county_id == county_id)
            .order_by(ParsingProfileVersion.created_at.asc())
        ).all()
        return [_to_stored(row, None) for row in rows]

    def approve(self, county_id: uuid.UUID, version_hash: str) -> bool:
        """Mark a version as trusted enough to inform future parses."""
        row = self._session.scalar(
            select(ParsingProfileVersion).where(
                ParsingProfileVersion.county_id == county_id,
                ParsingProfileVersion.version_hash == version_hash,
            )
        )
        if row is None:
            return False
        row.is_approved = True
        self._session.flush()
        logger.info("profile_approved", county_id=str(county_id), version=version_hash[:12])
        return True

    def _record_observation(self, row: ParsingProfileVersion, source_file_hash: str | None) -> None:
        """Count another sighting without touching the stored layout."""
        row.times_observed += 1
        if source_file_hash:
            seen = list(row.observed_file_hashes or [])
            if source_file_hash not in seen:
                row.observed_file_hashes = [*seen, source_file_hash]
        self._session.flush()

    def _supersede_previous(self, county_id: uuid.UUID) -> None:
        for row in self._session.scalars(
            select(ParsingProfileVersion).where(
                ParsingProfileVersion.county_id == county_id,
                ParsingProfileVersion.superseded.is_(False),
            )
        ).all():
            row.superseded = True


def _serialize(profile: CountyProfile) -> dict[str, Any]:
    return dict(profile.model_dump(mode="json"))


def _to_stored(row: ParsingProfileVersion, profile: CountyProfile | None) -> StoredProfileVersion:
    resolved = profile if profile is not None else CountyProfile(**row.profile_json)
    return StoredProfileVersion(
        county_slug=resolved.county_slug,
        version_hash=row.version_hash,
        profile=resolved,
        times_observed=row.times_observed,
        observed_file_hashes=tuple(row.observed_file_hashes or []),
        is_approved=row.is_approved,
        superseded=row.superseded,
        created_at=row.created_at,
    )
