"""Analyst feedback and the closed-loop monitoring built on it.

One endpoint mutates (`POST /api/feedback`, which appends a label). Everything
else is GET, because everything else measures.

Nothing here retrains a model, moves a threshold, edits a policy or revises a
decision. The recommendations endpoint returns text for a human to read.
"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import feedback_rate_limit, require
from app.core.permissions import Permission
from app.db.session import get_db
from app.models import RiskDecision
from app.models.enums import FeedbackOutcome, FeedbackReason
from app.schemas.common import DEFAULT_PAGE_SIZE, Page, PageMeta, PageNumber, PageSize
from app.schemas.feedback import (
    PUBLIC_ID_PATTERN,
    AssistantAnswer,
    AssistantQuestionsResponse,
    ConfusionMatrix,
    DriftResponse,
    FeedbackCreateRequest,
    FeedbackRead,
    FeedbackSummary,
    FeedbackSummaryResponse,
    HighRiskFunnelResponse,
    LabelCoverage,
    ModelMetricsResponse,
    ModelMonitoringResponse,
    PolicyEffectivenessResponse,
    RecommendationsResponse,
    ScoreWindowsResponse,
)
from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN
from app.services import assistant as assistant_service
from app.services import catalog, monitoring
from app.services import feedback as feedback_service
from app.services.feedback import FeedbackValidationError
from app.services.monitoring_config import MonitoringConfigError, get_monitoring_config
from app.services.review import load_case

router = APIRouter(tags=["feedback"])

ReferenceParam = Annotated[str | None, Query(max_length=64, pattern=TRANSACTION_REFERENCE_PATTERN)]
PublicIdParam = Annotated[str | None, Query(max_length=40, pattern=PUBLIC_ID_PATTERN)]

#: Drift and score windows are bounded so a caller cannot request an unbounded
#: scan by asking for a very wide comparison.
WindowDays = Annotated[int, Query(ge=1, le=365)]


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------
@router.post(
    "/feedback",
    dependencies=[
        Depends(require(Permission.FEEDBACK_WRITE)),
        Depends(feedback_rate_limit),
    ],
    response_model=FeedbackRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an analyst's structured conclusion about a transaction",
    responses={
        404: {"description": "No such transaction or review case"},
        422: {"description": "The outcome and reason are not a valid pair"},
    },
)
def create_feedback(
    payload: FeedbackCreateRequest, session: Session = Depends(get_db)
) -> FeedbackRead:
    """Append one label.

    The machine decision is left exactly as it was. Feedback is written to its
    own table and references the decision by id; the append-only guard on
    ``risk_decisions`` would raise if this tried to do otherwise.
    """
    transaction = catalog.get_transaction(session, payload.transaction_id)

    review_case = None
    if payload.review_case_id is not None:
        review_case = load_case(session, payload.review_case_id)
        if review_case is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"No review case {payload.review_case_id}",
            )

    try:
        row = feedback_service.record_feedback(
            session,
            transaction=transaction,
            outcome=payload.outcome,
            reason=payload.reason_code,
            notes=payload.notes,
            analyst_id=payload.analyst_id,
            review_case=review_case,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    session.commit()
    session.refresh(row)

    decision = (
        session.get(RiskDecision, row.risk_decision_id)
        if row.risk_decision_id is not None
        else None
    )
    return FeedbackRead(
        feedback_id=row.public_id,
        transaction_id=transaction.transaction_id,
        decision_id=decision.public_id if decision else None,
        review_case_id=row.review_case_id,
        analyst_id=row.analyst_id,
        outcome=str(row.outcome),
        reason_code=str(row.reason_code),
        notes=row.notes,
        machine_decision=str(decision.action).upper() if decision else None,
        policy_version=decision.policy_version if decision else None,
        created_at=row.created_at,
    )


@router.get(
    "/feedback",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=Page[FeedbackRead],
    summary="List recorded analyst feedback",
)
def list_feedback(
    session: Session = Depends(get_db),
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    outcome: Annotated[FeedbackOutcome | None, Query()] = None,
    reason_code: Annotated[FeedbackReason | None, Query()] = None,
    transaction_id: ReferenceParam = None,
    decision_id: PublicIdParam = None,
    analyst_id: Annotated[int | None, Query(ge=1)] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
) -> Page[FeedbackRead]:
    """One page of feedback, newest first. Every filter is a bound parameter."""
    statement = feedback_service.statement(
        outcome=outcome,
        reason=reason_code,
        transaction_id=transaction_id,
        decision_id=decision_id,
        analyst_id=analyst_id,
        created_after=created_after,
        created_before=created_before,
    )
    items, total = feedback_service.page(session, statement, page, page_size)
    total_pages = ceil(total / page_size) if total else 0
    return Page[FeedbackRead](
        items=[FeedbackRead(**item) for item in items],
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        ),
    )


@router.get(
    "/feedback/summary",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=FeedbackSummaryResponse,
    summary="Feedback counts and the machine-vs-human confusion matrix",
)
def feedback_summary(session: Session = Depends(get_db)) -> FeedbackSummaryResponse:
    """Outcome counts plus the confusion matrix over ground-truth labels only."""
    return FeedbackSummaryResponse(
        summary=FeedbackSummary(**feedback_service.summary(session)),
        confusion_matrix=ConfusionMatrix(**feedback_service.confusion_matrix(session)),
    )


# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------
def _config_or_503() -> None:
    try:
        get_monitoring_config()
    except MonitoringConfigError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The monitoring configuration is invalid.",
        ) from exc


@router.get(
    "/monitoring/models",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=ModelMonitoringResponse,
    summary="Model performance against analyst labels",
    responses={503: {"description": "The monitoring configuration is invalid"}},
)
def model_monitoring(session: Session = Depends(get_db)) -> ModelMonitoringResponse:
    """Precision, recall and error rates over labelled data only.

    When too few labels exist the metrics are withheld and the response says so.
    Unlabelled transactions are never counted as legitimate examples.
    """
    _config_or_503()
    return ModelMonitoringResponse(
        metrics=ModelMetricsResponse(**monitoring.model_metrics(session)),
        coverage=LabelCoverage(**monitoring.label_coverage(session)),
    )


@router.get(
    "/monitoring/scores",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=ScoreWindowsResponse,
    summary="Fraud and anomaly score summaries, baseline against current",
)
def score_monitoring(
    session: Session = Depends(get_db),
    baseline_days: WindowDays | None = None,
    current_days: WindowDays | None = None,
) -> ScoreWindowsResponse:
    """Both windows are read from stored transaction timestamps."""
    _config_or_503()
    result = monitoring.score_windows(
        session, baseline_days=baseline_days, current_days=current_days
    )
    return ScoreWindowsResponse(**result)


@router.get(
    "/monitoring/drift",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=DriftResponse,
    summary="Distribution drift across six monitored features",
)
def drift_monitoring(
    session: Session = Depends(get_db),
    baseline_days: WindowDays | None = None,
    current_days: WindowDays | None = None,
) -> DriftResponse:
    """PSI per feature, banded by configured thresholds.

    Drift means the distribution moved. It is not evidence of fraud, and the
    response says so explicitly.
    """
    _config_or_503()
    return DriftResponse(
        **monitoring.drift_report(session, baseline_days=baseline_days, current_days=current_days)
    )


@router.get(
    "/monitoring/policy",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=PolicyEffectivenessResponse,
    summary="Per-rule trigger counts, decision mix and human override rate",
)
def policy_monitoring(session: Session = Depends(get_db)) -> PolicyEffectivenessResponse:
    """Evidence about the policy. The policy itself is never changed here."""
    _config_or_503()
    return PolicyEffectivenessResponse(**monitoring.policy_effectiveness(session))


@router.get(
    "/monitoring/high-risk-funnel",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=HighRiskFunnelResponse,
    summary="Why a high model score does not automatically become a BLOCK",
)
def high_risk_funnel(session: Session = Depends(get_db)) -> HighRiskFunnelResponse:
    """Each stage between a high fraud score and an executed block, counted."""
    return HighRiskFunnelResponse(**monitoring.high_risk_funnel(session))


@router.get(
    "/monitoring/recommendations",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=RecommendationsResponse,
    summary="Analytical recommendations for a human to consider",
)
def monitoring_recommendations(session: Session = Depends(get_db)) -> RecommendationsResponse:
    """Suggestions derived from measured metrics.

    Nothing here is executed. Each item names the metric behind it so a reader
    can check the reasoning before acting.
    """
    _config_or_503()
    return RecommendationsResponse(recommendations=monitoring.recommendations(session))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Assistant
# --------------------------------------------------------------------------
@router.get(
    "/assistant/questions",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=AssistantQuestionsResponse,
    summary="The questions the assistant can answer",
)
def assistant_questions() -> AssistantQuestionsResponse:
    """A closed set. The assistant does not interpret free-form questions."""
    return AssistantQuestionsResponse(questions=assistant_service.available_questions())  # type: ignore[arg-type]


@router.get(
    "/assistant/answer",
    dependencies=[Depends(require(Permission.MONITORING_READ))],
    response_model=AssistantAnswer,
    summary="Answer one question from structured backend metrics",
)
def assistant_answer(
    topic: Annotated[assistant_service.QuestionTopic, Query()],
    session: Session = Depends(get_db),
) -> AssistantAnswer:
    """Every figure comes from a query, and every source is named in the response.

    No language model is involved. When the data cannot answer the question the
    response says so and sets ``sufficient`` to false.
    """
    return AssistantAnswer(**assistant_service.answer(session, topic).as_dict())
