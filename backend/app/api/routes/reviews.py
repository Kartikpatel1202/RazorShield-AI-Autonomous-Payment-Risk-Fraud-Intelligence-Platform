"""The human review queue.

Two endpoints: list what needs a person, and record what the person decided.
Resolving a case writes to ``review_cases`` and ``analyst_decisions``; it never
writes to ``risk_decisions``, and the append-only guard would raise if it tried.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require, review_rate_limit
from app.core.permissions import Permission
from app.db.session import get_db
from app.models import ReviewCase
from app.models.enums import DecisionAction, ReviewCaseStatus, ReviewResolution
from app.schemas.common import DEFAULT_PAGE_SIZE, Page, PageNumber, PageSize
from app.schemas.review import (
    ResolveReviewRequest,
    ResolveReviewResponse,
    ReviewCaseRead,
    ReviewDecisionSummary,
)
from app.services.feedback import FeedbackValidationError, record_feedback
from app.services.pagination import paginate
from app.services.review import (
    ReviewCaseError,
    is_override,
    load_case,
    queue_statement,
    resolve_case,
)

router = APIRouter(
    prefix="/reviews",
    tags=["reviews"],
    # The queue is readable by viewers; resolving a case is not - see the
    # extra dependency on the resolve endpoint.
    dependencies=[Depends(require(Permission.REVIEWS_READ))],
)


def _to_read(case: ReviewCase) -> ReviewCaseRead:
    decision = case.risk_decision
    return ReviewCaseRead(
        review_case_id=case.id,
        transaction_id=case.transaction.transaction_id,
        status=case.status,
        reason=case.reason,
        created_at=case.created_at,
        resolved_at=case.resolved_at,
        resolution=case.resolution,
        resolution_reason=case.resolution_reason,
        assigned_to=case.assigned_to,
        decision=(
            None
            if decision is None
            else ReviewDecisionSummary(
                decision_id=decision.public_id,
                decision=str(decision.action).upper(),
                policy_version=decision.policy_version,
                matched_rules=list(decision.matched_rules),
                reason_codes=list(decision.reason_codes),
                requires_human_review=decision.requires_human_review,
                fraud_probability=(
                    float(decision.fraud_probability)
                    if decision.fraud_probability is not None
                    else None
                ),
                anomaly_score=decision.anomaly_score,
                investigation_id=decision.investigation_public_id,
                decided_at=decision.decided_at,
            )
        ),
    )


@router.get(
    "",
    response_model=Page[ReviewCaseRead],
    summary="List transactions awaiting or completed human review",
)
def list_reviews(
    session: Session = Depends(get_db),
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    case_status: Annotated[
        ReviewCaseStatus | None, Query(alias="status", description="Queue state")
    ] = None,
    resolution: Annotated[
        ReviewResolution | None, Query(description="How the case was settled")
    ] = None,
    decision: Annotated[
        DecisionAction | None, Query(description="The machine decision that opened the case")
    ] = None,
    transaction_id: Annotated[
        str | None, Query(max_length=64, description="Exact transaction reference")
    ] = None,
    created_after: Annotated[datetime | None, Query(description="Inclusive lower bound")] = None,
    created_before: Annotated[datetime | None, Query(description="Inclusive upper bound")] = None,
) -> Page[ReviewCaseRead]:
    """One page of the review queue, newest first.

    Every filter is optional and applied server-side as a bound parameter.
    """
    statement = queue_statement(
        status=case_status,
        resolution=resolution,
        decision=decision,
        transaction_id=transaction_id,
        created_after=created_after,
        created_before=created_before,
    )
    result = paginate(session, statement, page, page_size)
    return Page[ReviewCaseRead](items=[_to_read(case) for case in result.items], meta=result.meta)


@router.post(
    "/{review_id}/resolve",
    response_model=ResolveReviewResponse,
    summary="Record an analyst's resolution of a review case",
    dependencies=[
        Depends(require(Permission.REVIEWS_RESOLVE)),
        Depends(review_rate_limit),
    ],
    responses={
        404: {"description": "No such review case"},
        409: {"description": "The case has already been resolved"},
        429: {"description": "Rate limit exceeded"},
    },
)
def resolve_review(
    review_id: int,
    payload: ResolveReviewRequest,
    session: Session = Depends(get_db),
) -> ResolveReviewResponse:
    """Settle a case, leaving the machine decision exactly as it was.

    The resolution is written to ``review_cases`` and appended to
    ``analyst_decisions``. The linked ``risk_decisions`` row is not modified -
    it cannot be - so the pair "engine decided X, analyst decided Y" stays
    visible and countable.
    """
    case = load_case(session, review_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No review case {review_id}")

    machine_decision = case.risk_decision
    try:
        resolve_case(
            session,
            case,
            payload.resolution,
            analyst_id=payload.analyst_id,
            reason=payload.reason,
        )
    except ReviewCaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # The analyst's structured conclusion, when they gave one. Written to its own
    # table - the machine decision above is untouched by this, and the
    # append-only guard would raise if it were not.
    feedback_row = None
    if payload.feedback_outcome is not None and payload.feedback_reason is not None:
        try:
            feedback_row = record_feedback(
                session,
                transaction=case.transaction,
                outcome=payload.feedback_outcome,
                reason=payload.feedback_reason,
                notes=payload.feedback_notes,
                analyst_id=payload.analyst_id,
                review_case=case,
                decision=machine_decision,
            )
        except FeedbackValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    session.commit()
    session.refresh(case)
    if feedback_row is not None:
        session.refresh(feedback_row)

    stored_action = str(machine_decision.action) if machine_decision else None
    machine_action = stored_action.upper() if stored_action else None

    return ResolveReviewResponse(
        review_case_id=case.id,
        transaction_id=case.transaction.transaction_id,
        status=case.status,
        resolution=payload.resolution,
        resolution_reason=case.resolution_reason,
        resolved_at=case.resolved_at,
        machine_decision=machine_action,
        machine_decision_id=machine_decision.public_id if machine_decision else None,
        overrides_machine_decision=is_override(stored_action, payload.resolution),
        feedback_id=feedback_row.public_id if feedback_row else None,
        feedback_outcome=str(feedback_row.outcome) if feedback_row else None,
        feedback_reason=str(feedback_row.reason_code) if feedback_row else None,
        feedback_notes=feedback_row.notes if feedback_row else None,
    )
