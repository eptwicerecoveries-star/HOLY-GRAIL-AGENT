"""Read-only research review endpoints (operational metadata only)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session, require_active_user
from surplus_ai.api.errors import not_found
from surplus_ai.api.pagination import pagination_params
from surplus_ai.api.queries import get_review, list_reviews
from surplus_ai.api.schemas import ResearchReviewResponse
from surplus_ai.database.models.research_review_item import ResearchReviewItem

router = APIRouter(
    tags=["research-reviews"], dependencies=[Depends(require_active_user)]
)


def _to_review(item: ResearchReviewItem) -> ResearchReviewResponse:
    return ResearchReviewResponse(
        id=item.id,
        surplus_case_id=item.surplus_case_id,
        research_result_id=item.research_result_id,
        provider=item.provider,
        reason=item.reason.value,
        reason_detail=item.reason_detail,
        status=item.status.value,
        resolution=item.resolution.value if item.resolution is not None else None,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/research/reviews", response_model=list[ResearchReviewResponse])
def get_reviews(
    pagination: tuple[int, int] = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> list[ResearchReviewResponse]:
    limit, offset = pagination
    return [_to_review(item) for item in list_reviews(session, limit=limit, offset=offset)]


@router.get("/research/reviews/{review_id}", response_model=ResearchReviewResponse)
def get_review_detail(
    review_id: UUID,
    session: Session = Depends(get_db_session),
) -> ResearchReviewResponse:
    item = get_review(session, review_id)
    if item is None:
        raise not_found("Research review")
    return _to_review(item)
