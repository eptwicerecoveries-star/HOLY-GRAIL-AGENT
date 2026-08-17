"""Thin read-only query helpers for the P1 API. No writes, no provider calls."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models.contact import Contact
from surplus_ai.database.models.county import County
from surplus_ai.database.models.lead import Lead
from surplus_ai.database.models.research_review_item import ResearchReviewItem
from surplus_ai.database.models.surplus_case import SurplusCase


def list_cases(
    session: Session, *, limit: int, offset: int
) -> list[tuple[SurplusCase, County, bool]]:
    lead_exists = (
        select(Lead.id).where(Lead.surplus_case_id == SurplusCase.id).exists().label("has_lead")
    )
    rows = session.execute(
        select(SurplusCase, County, lead_exists)
        .join(County, SurplusCase.county_id == County.id)
        .order_by(SurplusCase.created_at.desc(), SurplusCase.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [(case, county, bool(has_lead)) for case, county, has_lead in rows]


def get_case(
    session: Session, case_id: uuid.UUID
) -> tuple[SurplusCase, County, bool] | None:
    lead_exists = (
        select(Lead.id).where(Lead.surplus_case_id == SurplusCase.id).exists().label("has_lead")
    )
    row = session.execute(
        select(SurplusCase, County, lead_exists)
        .join(County, SurplusCase.county_id == County.id)
        .where(SurplusCase.id == case_id)
    ).one_or_none()
    if row is None:
        return None
    case, county, has_lead = row
    return case, county, bool(has_lead)


def list_leads(
    session: Session, *, limit: int, offset: int
) -> list[tuple[Lead, int]]:
    contact_count = (
        select(func.count())
        .select_from(Contact)
        .where(Contact.lead_id == Lead.id)
        .correlate(Lead)
        .scalar_subquery()
        .label("contact_count")
    )
    rows = session.execute(
        select(Lead, contact_count)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [(lead, int(count or 0)) for lead, count in rows]


def get_lead(session: Session, lead_id: uuid.UUID) -> tuple[Lead, int] | None:
    contact_count = (
        select(func.count())
        .select_from(Contact)
        .where(Contact.lead_id == Lead.id)
        .correlate(Lead)
        .scalar_subquery()
        .label("contact_count")
    )
    row = session.execute(
        select(Lead, contact_count).where(Lead.id == lead_id)
    ).one_or_none()
    if row is None:
        return None
    lead, count = row
    return lead, int(count or 0)


def list_reviews(
    session: Session, *, limit: int, offset: int
) -> list[ResearchReviewItem]:
    return list(
        session.scalars(
            select(ResearchReviewItem)
            .order_by(ResearchReviewItem.created_at.desc(), ResearchReviewItem.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def get_review(session: Session, review_id: uuid.UUID) -> ResearchReviewItem | None:
    return session.get(ResearchReviewItem, review_id)


def list_contacts(session: Session, *, limit: int, offset: int) -> list[Contact]:
    return list(
        session.scalars(
            select(Contact)
            .order_by(Contact.created_at.desc(), Contact.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def get_contact(session: Session, contact_id: uuid.UUID) -> Contact | None:
    return session.get(Contact, contact_id)
