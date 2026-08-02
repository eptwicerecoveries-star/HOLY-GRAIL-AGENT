"""Storing a parsed document, and the queue of rows that still need a person."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models import (
    DocumentColumnMapping,
    ParsedDocument,
    ParseReviewItem,
    RawSurplusRow,
)
from surplus_ai.database.models.enums import ReviewStatus, RoutingDecisionType
from surplus_ai.parser.evaluation import Evaluator
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.persistence import DocumentPersister, ReviewQueue
from surplus_ai.parser.pipeline import ParsingPipeline
from surplus_ai.utils.hashing import stable_row_hash
from tests.unit.parser.conftest import CORPUS_DIR


@pytest.fixture(scope="module")
def harford():
    """A small text-layer county: every row auto-accepts, so nothing should queue."""
    parsed = ParsingPipeline().parse(CORPUS_DIR / "Harford_County_MD.pdf")
    return parsed, InterpretationPipeline().interpret(parsed, None)


@pytest.fixture(scope="module")
def marion_unconfigured():
    """Marion without its config: 950 rows, none of which may be worked unattended.

    Module-scoped because parsing a 49-page document costs about 25 seconds, and every
    test below needs the same result rather than a fresh one.
    """
    parsed = ParsingPipeline().parse(CORPUS_DIR / "Marion_County_IN_2023.pdf")
    return parsed, InterpretationPipeline().interpret(parsed, None)


# --------------------------------------------------------------------------------------
# Row hashing
# --------------------------------------------------------------------------------------


def test_row_hash_ignores_key_order() -> None:
    """A county reordering its columns has not changed its records."""
    assert stable_row_hash({"a": "1", "b": "2"}) == stable_row_hash({"b": "2", "a": "1"})


def test_row_hash_distinguishes_values() -> None:
    assert stable_row_hash({"a": "1"}) != stable_row_hash({"a": "2"})


def test_row_hash_does_not_normalise_values() -> None:
    """Two genuinely different records must not collide through tidying."""
    assert stable_row_hash({"a": "1"}) != stable_row_hash({"a": " 1 "})


# --------------------------------------------------------------------------------------
# Persisting a document
# --------------------------------------------------------------------------------------


def test_document_and_rows_are_stored(session: Session, harford) -> None:
    parsed, interpreted = harford

    report = DocumentPersister(session).persist(parsed, interpreted)

    assert report.rows == 49
    assert report.mappings == 6
    document = session.get(ParsedDocument, report.document_id)
    assert document is not None
    assert document.row_count == 49
    assert document.winning_strategy == "pdfplumber_lines"


def test_rows_keep_their_verbatim_values(session: Session, harford) -> None:
    parsed, interpreted = harford
    DocumentPersister(session).persist(parsed, interpreted)

    row = session.scalars(select(RawSurplusRow).limit(1)).one()
    assert "SURPLUS" in row.raw_data
    assert row.original_headers is not None
    assert "SURPLUS" in row.original_headers
    assert row.source_pdf_sha256 == parsed.profile.source_sha256


def test_column_mappings_record_their_evidence(session: Session, harford) -> None:
    parsed, interpreted = harford
    DocumentPersister(session).persist(parsed, interpreted)

    mappings = session.scalars(select(DocumentColumnMapping)).all()
    surplus = next(m for m in mappings if m.original_header == "SURPLUS")

    assert surplus.canonical_field == "surplus_amount"
    assert surplus.evidence


def test_re_persisting_the_same_file_is_a_no_op(session: Session, harford) -> None:
    """Counties republish the same list under new names, so contents decide identity."""
    parsed, interpreted = harford
    persister = DocumentPersister(session)

    first = persister.persist(parsed, interpreted)
    second = persister.persist(parsed, interpreted)

    assert second.already_present is True
    assert second.document_id == first.document_id
    assert len(session.scalars(select(ParsedDocument)).all()) == 1
    assert len(session.scalars(select(RawSurplusRow)).all()) == 49


def test_persisting_without_interpretation_still_stores_rows(session: Session, harford) -> None:
    """Extraction is usable on its own; interpretation is a separate layer."""
    parsed, _ = harford

    report = DocumentPersister(session).persist(parsed, None)

    assert report.rows == 49
    assert report.mappings == 0
    assert report.queued_for_review == 0


# --------------------------------------------------------------------------------------
# The review queue
# --------------------------------------------------------------------------------------


def test_confident_rows_are_not_queued(session: Session, harford) -> None:
    parsed, interpreted = harford

    report = DocumentPersister(session).persist(parsed, interpreted)

    assert report.queued_for_review == 0
    assert ReviewQueue(session).pending_count() == 0


def test_unresolved_surplus_queues_every_row(session: Session, marion_unconfigured) -> None:
    """Marion without its configuration cannot say what the surplus is, so nothing ships."""
    parsed, interpreted = marion_unconfigured

    report = DocumentPersister(session).persist(parsed, interpreted)

    assert report.queued_for_review == 950
    item = session.scalars(select(ParseReviewItem).limit(1)).one()
    assert item.routing is RoutingDecisionType.REVIEW
    assert item.surplus_source is SurplusSource.AMBIGUOUS.value or item.surplus_amount is None


def test_queued_rows_explain_themselves(session: Session, marion_unconfigured) -> None:
    parsed, interpreted = marion_unconfigured
    DocumentPersister(session).persist(parsed, interpreted)

    item = session.scalars(select(ParseReviewItem).limit(1)).one()

    assert "Surplus column could not be determined" in item.reason
    assert item.raw_values


def test_queueing_does_not_withhold_the_row(session: Session, marion_unconfigured) -> None:
    """A queued row is a work item, not a quarantined record: it is stored either way."""
    parsed, interpreted = marion_unconfigured

    DocumentPersister(session).persist(parsed, interpreted)

    assert len(session.scalars(select(RawSurplusRow)).all()) == 950
    assert ReviewQueue(session).pending_count() == 950


def test_pending_shows_least_confident_first(session: Session, marion_unconfigured) -> None:
    parsed, interpreted = marion_unconfigured
    DocumentPersister(session).persist(parsed, interpreted)

    items = ReviewQueue(session).pending(limit=10)
    confidences = [i.confidence for i in items]

    assert confidences == sorted(confidences)


def test_resolving_closes_an_item_without_touching_the_row(
    session: Session, marion_unconfigured
) -> None:
    parsed, interpreted = marion_unconfigured
    DocumentPersister(session).persist(parsed, interpreted)
    queue = ReviewQueue(session)
    item = queue.pending(limit=1)[0]

    assert queue.resolve(item.id, resolved_by="alex", notes="confirmed") is True

    session.expire(item)
    assert item.status is ReviewStatus.RESOLVED
    assert item.resolved_by == "alex"
    assert queue.pending_count() == 949
    assert len(session.scalars(select(RawSurplusRow)).all()) == 950


def test_rejecting_is_recorded_separately(session: Session, marion_unconfigured) -> None:
    parsed, interpreted = marion_unconfigured
    DocumentPersister(session).persist(parsed, interpreted)
    queue = ReviewQueue(session)
    item = queue.pending(limit=1)[0]

    queue.resolve(item.id, resolved_by="alex", status=ReviewStatus.REJECTED)

    session.expire(item)
    assert item.status is ReviewStatus.REJECTED


def test_resolving_an_unknown_item_reports_failure(session: Session) -> None:
    assert ReviewQueue(session).resolve(uuid.uuid4(), resolved_by="alex") is False


# --------------------------------------------------------------------------------------
# Generalisation: how a county fares with its configuration withheld
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def marion_holdout():
    return Evaluator().compare_holdout(CORPUS_DIR / "Marion_County_IN_2023.pdf", "in", "marion")


def test_extraction_does_not_depend_on_county_configuration(marion_holdout) -> None:
    """The coupling the design forbids, measured rather than assumed.

    Configuration says what columns mean. If withholding it changed how many rows came
    back, county knowledge would have leaked into extraction.
    """
    assert marion_holdout.rows_match
    assert marion_holdout.configured.rows == 950
    assert marion_holdout.cold_start.rows == 950


def test_columns_still_resolve_without_configuration(marion_holdout) -> None:
    """The generic alias registry alone understands a county it has no file for."""
    assert marion_holdout.cold_start.column_resolution_rate == 1.0


def test_withheld_configuration_costs_an_answer_not_a_wrong_one(marion_holdout) -> None:
    """Cold, Marion reports ambiguous rather than naming one of its three overbid columns."""
    assert marion_holdout.configured.surplus_source is SurplusSource.COUNTY_CONFIG
    assert marion_holdout.cold_start.surplus_source is SurplusSource.AMBIGUOUS
    assert marion_holdout.surplus_degraded_safely


def test_a_county_needing_no_configuration_is_unaffected() -> None:
    """Harford names its surplus outright, so holding out a config it does not have
    changes nothing at all."""
    comparison = Evaluator().compare_holdout(CORPUS_DIR / "Harford_County_MD.pdf", "md", "harford")

    assert comparison.rows_match
    assert comparison.configured.surplus_source is SurplusSource.EXPLICIT
    assert comparison.cold_start.surplus_source is SurplusSource.EXPLICIT
