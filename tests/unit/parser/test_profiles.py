"""County profile learning: what is recorded, and that history is never rewritten."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models import County, ParsingProfileVersion
from surplus_ai.database.models.enums import CountySourceType, PdfType, PublishingFrequency
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.profiles.learner import ProfileLearner
from surplus_ai.parser.profiles.models import (
    CountyProfile,
    OwnerTypeObservation,
    TableStructure,
)
from surplus_ai.parser.profiles.resolver import ProfileResolver
from surplus_ai.parser.profiles.store import ProfileStore
from surplus_ai.parser.strategies.pdfplumber_strategies import (
    PdfplumberLinesStrategy,
    PdfplumberTextStrategy,
)
from surplus_ai.parser.strategies.word_cluster import WordClusterStrategy


def _profile(**overrides: object) -> CountyProfile:
    defaults: dict[str, object] = {
        "county_slug": "demo",
        "county_name": "Demo County",
        "state": "XX",
        "pdf_type": PdfType.SEARCHABLE,
        "ocr_required": False,
        "required_parsing_strategy": "pdfplumber_lines",
        "original_column_names": ("Parcel", "Owner", "Surplus"),
        "table_structures": (
            TableStructure(
                table_index=0, column_count=3, original_columns=("Parcel", "Owner", "Surplus")
            ),
        ),
        "surplus_explicitly_listed": True,
        "surplus_source": SurplusSource.EXPLICIT,
        "surplus_source_column": "Surplus",
        "typical_row_count": 100,
    }
    defaults.update(overrides)
    return CountyProfile(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def county(session: Session) -> County:
    row = County(
        slug="demo",
        name="Demo County",
        state="XX",
        source_type=CountySourceType.BULK_DOWNLOAD,
        parsing_profile_key="demo",
        compliance_state_ref="XX",
        publishing_frequency=PublishingFrequency.ANNUAL,
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------------------------
# What identifies a profile
# --------------------------------------------------------------------------------------


def test_version_hash_is_stable() -> None:
    assert _profile().version_hash() == _profile().version_hash()


def test_row_count_does_not_change_the_version() -> None:
    """Two files of different sizes in the same layout are the same profile.

    Marion published 950 records one year and 917 the next in an identical layout. If the
    record count were part of the identity, every year would look like a new layout and
    the history would fill with noise.
    """
    assert _profile(typical_row_count=950).version_hash() == (
        _profile(typical_row_count=917).version_hash()
    )


def test_confidence_does_not_change_the_version() -> None:
    assert _profile(mean_extraction_confidence=0.9).version_hash() == (
        _profile(mean_extraction_confidence=1.0).version_hash()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"original_column_names": ("Parcel", "Owner", "Excess Funds")},
        {"required_parsing_strategy": "ocr_table"},
        {"pdf_type": PdfType.HYBRID},
        {"ocr_required": True},
        {"surplus_source_column": "Excess Funds"},
    ],
)
def test_layout_changes_produce_a_new_version(change: dict[str, object]) -> None:
    assert _profile(**change).version_hash() != _profile().version_hash()


# --------------------------------------------------------------------------------------
# Append-only storage
# --------------------------------------------------------------------------------------


def test_first_observation_creates_a_version(session: Session, county: County) -> None:
    stored = ProfileStore(session).record(county.id, _profile(), "hash-1")

    assert stored.times_observed == 1
    assert stored.observed_file_hashes == ("hash-1",)
    assert stored.is_approved is False


def test_same_layout_records_an_observation_not_a_new_version(
    session: Session, county: County
) -> None:
    """The second file from a county must not duplicate its profile."""
    store = ProfileStore(session)
    store.record(county.id, _profile(typical_row_count=950), "hash-2023")
    store.record(county.id, _profile(typical_row_count=917), "hash-2024")

    rows = session.scalars(select(ParsingProfileVersion)).all()
    assert len(rows) == 1
    assert rows[0].times_observed == 2
    assert set(rows[0].observed_file_hashes or []) == {"hash-2023", "hash-2024"}


def test_repeating_one_file_does_not_duplicate_its_hash(session: Session, county: County) -> None:
    store = ProfileStore(session)
    store.record(county.id, _profile(), "hash-1")
    store.record(county.id, _profile(), "hash-1")

    row = session.scalars(select(ParsingProfileVersion)).one()
    assert row.times_observed == 2
    assert row.observed_file_hashes == ["hash-1"]


def test_changed_layout_creates_a_second_version(session: Session, county: County) -> None:
    store = ProfileStore(session)
    store.record(county.id, _profile(), "hash-1")
    store.record(county.id, _profile(original_column_names=("A", "B")), "hash-2")

    rows = session.scalars(select(ParsingProfileVersion)).all()
    assert len(rows) == 2


def test_previous_versions_are_never_altered(session: Session, county: County) -> None:
    """The core promise: history is added to, never rewritten."""
    store = ProfileStore(session)
    original = _profile()
    store.record(county.id, original, "hash-1")
    first_json = dict(session.scalars(select(ParsingProfileVersion)).one().profile_json)

    store.record(county.id, _profile(original_column_names=("A", "B")), "hash-2")

    stored = {row.version_hash: row for row in session.scalars(select(ParsingProfileVersion)).all()}
    assert stored[original.version_hash()].profile_json == first_json


def test_a_new_version_supersedes_the_previous_one(session: Session, county: County) -> None:
    store = ProfileStore(session)
    old = _profile()
    store.record(county.id, old, "hash-1")
    store.record(county.id, _profile(original_column_names=("A", "B")), "hash-2")

    rows = {r.version_hash: r for r in session.scalars(select(ParsingProfileVersion)).all()}
    assert rows[old.version_hash()].superseded is True


def test_history_keeps_every_version(session: Session, county: County) -> None:
    store = ProfileStore(session)
    store.record(county.id, _profile(), "h1")
    store.record(county.id, _profile(original_column_names=("A", "B")), "h2")
    store.record(county.id, _profile(original_column_names=("A", "B", "C")), "h3")

    assert len(store.history(county.id)) == 3


def test_latest_returns_the_current_version(session: Session, county: County) -> None:
    store = ProfileStore(session)
    store.record(county.id, _profile(), "h1")
    newest = _profile(original_column_names=("A", "B"))
    store.record(county.id, newest, "h2")

    latest = store.latest(county.id)
    assert latest is not None
    assert latest.version_hash == newest.version_hash()


def test_profiles_start_unapproved_and_can_be_approved(session: Session, county: County) -> None:
    """A freshly learned profile is a record of what happened, not yet a trusted prior."""
    store = ProfileStore(session)
    profile = _profile()
    store.record(county.id, profile, "h1")

    assert store.latest_approved(county.id) is None
    assert store.approve(county.id, profile.version_hash()) is True

    approved = store.latest_approved(county.id)
    assert approved is not None
    assert approved.version_hash == profile.version_hash()


def test_approving_an_unknown_version_reports_failure(session: Session, county: County) -> None:
    assert ProfileStore(session).approve(county.id, "nope") is False


def test_profiles_are_scoped_per_county(session: Session, county: County) -> None:
    other = County(
        slug="other",
        name="Other County",
        state="YY",
        source_type=CountySourceType.BULK_DOWNLOAD,
        parsing_profile_key="other",
        compliance_state_ref="YY",
        publishing_frequency=PublishingFrequency.ANNUAL,
    )
    session.add(other)
    session.flush()
    store = ProfileStore(session)

    store.record(county.id, _profile(), "h1")
    store.record(other.id, _profile(county_slug="other"), "h2")

    assert len(store.history(county.id)) == 1
    assert len(store.history(other.id)) == 1


def test_stored_profile_round_trips(session: Session, county: County) -> None:
    store = ProfileStore(session)
    store.record(county.id, _profile(), "h1")

    latest = store.latest(county.id)
    assert latest is not None
    assert latest.profile.original_column_names == ("Parcel", "Owner", "Surplus")
    assert latest.profile.surplus_source is SurplusSource.EXPLICIT
    assert latest.profile.pdf_type is PdfType.SEARCHABLE


def test_unknown_county_has_no_profile(session: Session) -> None:
    assert ProfileStore(session).latest(uuid.uuid4()) is None


# --------------------------------------------------------------------------------------
# Using a profile as a prior
# --------------------------------------------------------------------------------------


def _strategies() -> list[object]:
    return [PdfplumberLinesStrategy(), PdfplumberTextStrategy(), WordClusterStrategy()]


def test_known_strategy_is_tried_first() -> None:
    ordered = ProfileResolver().order_strategies(
        _strategies(),  # type: ignore[arg-type]
        _profile(required_parsing_strategy="word_cluster"),
    )

    assert ordered[0].name == "word_cluster"
    assert len(ordered) == 3


def test_no_profile_leaves_the_cascade_alone() -> None:
    strategies = _strategies()
    ordered = ProfileResolver().order_strategies(strategies, None)  # type: ignore[arg-type]

    assert [s.name for s in ordered] == [s.name for s in strategies]  # type: ignore[attr-defined]


def test_unavailable_preferred_strategy_is_ignored() -> None:
    """A profile naming a strategy this run cannot offer must not empty the cascade."""
    ordered = ProfileResolver().order_strategies(
        _strategies(),  # type: ignore[arg-type]
        _profile(required_parsing_strategy="ocr_table"),
    )

    assert len(ordered) == 3


def test_layout_drift_is_detected() -> None:
    resolver = ProfileResolver()
    profile = _profile()

    assert resolver.layout_changed(profile, ("Parcel", "Owner", "Surplus")) is False
    assert resolver.layout_changed(profile, ("Parcel", "Owner", "Excess Funds")) is True


def test_drift_names_what_changed() -> None:
    missing, added = ProfileResolver().describe_drift(
        _profile(), ("Parcel", "Owner", "Excess Funds")
    )

    assert missing == ("Surplus",)
    assert added == ("Excess Funds",)


def test_drift_ignores_case_and_whitespace() -> None:
    assert ProfileResolver().layout_changed(_profile(), ("parcel", " OWNER ", "surplus")) is False


# --------------------------------------------------------------------------------------
# Owner-type observation
# --------------------------------------------------------------------------------------


def test_owner_observation_reports_a_share() -> None:
    observation = OwnerTypeObservation(sampled=10, person_like=3, entity_like=7)

    assert observation.entity_share == pytest.approx(0.7)


def test_empty_owner_observation_is_safe() -> None:
    assert OwnerTypeObservation().entity_share == 0.0


def test_learner_requires_no_interpretation() -> None:
    """A profile can be learned from extraction alone, before columns are understood."""
    assert ProfileLearner() is not None
