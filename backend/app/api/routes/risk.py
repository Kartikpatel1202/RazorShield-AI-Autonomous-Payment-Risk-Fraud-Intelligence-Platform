"""Fraud risk scoring and decision endpoints.

``/predict`` and ``/anomaly`` produce model output and nothing else: neither
builds rules, applies adjustments or takes a decision. ``/decision`` is where
acting on those scores happens, and it does so through the deterministic policy
engine in ``policy/`` - never through a language model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.db.session import get_db
from app.models import ReviewCase, RiskDecision
from app.schemas.anomaly import AnomalyRequest, AnomalyResponse, FeatureDeviationRead
from app.schemas.decision import (
    DecisionRequest,
    DecisionResponse,
    DecisionSignals,
    RuleMatchRead,
)
from app.schemas.risk import RiskPredictionRequest, RiskPredictionResponse
from app.services import catalog
from app.services.anomaly import score_and_store
from app.services.decision import decide_and_store
from app.services.risk import predict_and_store
from policy.engine import PolicyResult
from policy.loader import get_policy
from policy.schema import PolicyValidationError

router = APIRouter(
    prefix="/risk",
    tags=["risk"],
    # These endpoints write predictions, signals and decisions. Scoring is an
    # analyst action, not a read.
    dependencies=[Depends(require(Permission.RISK_SCORE))],
)


def decision_row_to_response(row: RiskDecision, transaction_id: str) -> DecisionResponse:
    """Render a stored decision, replaying it from the persisted row alone.

    Used by the history endpoint. Everything comes out of the row - including
    the per-rule detail captured at decision time - so a historical decision
    reads exactly as it did when made, even if the policy has since changed.
    """
    detail: dict[str, Any] = row.detail or {}
    matches: list[dict[str, Any]] = detail.get("rule_matches") or []
    return DecisionResponse(
        decision_id=row.public_id,
        transaction_id=transaction_id,
        decision=str(row.action).upper(),
        policy_version=row.policy_version,
        matched_rules=list(row.matched_rules),
        deciding_rules=list(detail.get("deciding_rules") or []),
        reason_codes=list(row.reason_codes),
        rule_matches=[
            RuleMatchRead(
                rule_id=str(match.get("rule_id", "")),
                action=str(match.get("action", "")),
                reason_codes=list(match.get("reason_codes") or []),
                conditions=list(match.get("conditions") or []),
            )
            for match in matches
        ],
        explanation=row.explanation,
        requires_human_review=row.requires_human_review,
        review_case_id=None,
        signals=DecisionSignals(
            fraud_probability=(
                float(row.fraud_probability) if row.fraud_probability is not None else None
            ),
            risk_score=row.risk_score,
            fraud_model_version=row.fraud_model_version,
            anomaly_score=row.anomaly_score,
            anomaly_severity=row.anomaly_severity,
            anomaly_model_version=row.anomaly_model_version,
            investigation_id=row.investigation_public_id,
            investigation_confidence=(
                float(row.investigation_confidence)
                if row.investigation_confidence is not None
                else None
            ),
            investigation_status=(detail.get("risk_summary") or {}).get("investigation_status"),
        ),
        input_digest=row.input_digest,
        decided_at=row.decided_at,
    )


def _to_decision_response(
    result: PolicyResult, row: RiskDecision, review_case_id: int | None
) -> DecisionResponse:
    """Render a decision for the API.

    Every value comes from the stored row or the pure result - nothing is
    recomputed here, so the response cannot drift from what was persisted.
    """
    summary = result.risk_summary
    return DecisionResponse(
        decision_id=row.public_id,
        transaction_id=result.transaction_id,
        decision=str(result.action),
        policy_version=result.policy_version,
        matched_rules=list(result.matched_rules),
        deciding_rules=list(result.deciding_rules),
        reason_codes=list(result.reason_codes),
        rule_matches=[
            RuleMatchRead(
                rule_id=match.rule_id,
                action=str(match.action),
                reason_codes=list(match.reason_codes),
                conditions=list(match.conditions),
            )
            for match in result.rule_matches
        ],
        explanation=result.explanation,
        requires_human_review=result.requires_human_review,
        review_case_id=review_case_id,
        signals=DecisionSignals(
            fraud_probability=summary.get("fraud_probability"),
            risk_score=summary.get("risk_score"),
            fraud_model_version=summary.get("fraud_model_version"),
            anomaly_score=summary.get("anomaly_score"),
            anomaly_severity=summary.get("anomaly_severity"),
            anomaly_model_version=summary.get("anomaly_model_version"),
            investigation_id=summary.get("investigation_id"),
            investigation_confidence=summary.get("investigation_confidence"),
            investigation_status=summary.get("investigation_status"),
        ),
        input_digest=result.input_digest,
        decided_at=row.decided_at,
    )


@router.post(
    "/predict",
    response_model=RiskPredictionResponse,
    summary="Score a transaction with the trained fraud model",
    responses={
        404: {"description": "No such transaction"},
        503: {"description": "No trained model is currently available"},
    },
)
def predict_risk(
    payload: RiskPredictionRequest, session: Session = Depends(get_db)
) -> RiskPredictionResponse:
    """Generate and store a fraud probability for one transaction.

    Features are rebuilt point-in-time from data that existed strictly before
    the transaction, so the score reflects what was knowable at the moment of
    payment. The result is written to ``risk_predictions``, replacing any
    earlier score for the same transaction.
    """
    transaction = catalog.get_transaction(session, payload.transaction_id)
    prediction, row = predict_and_store(session, transaction)
    session.commit()

    return RiskPredictionResponse(
        transaction_id=transaction.transaction_id,
        fraud_probability=prediction.fraud_probability,
        risk_score=prediction.risk_score,
        model_version=prediction.model_version,
        threshold=prediction.threshold,
        exceeds_threshold=prediction.exceeds_threshold,
        created_at=row.created_at,
    )


@router.post(
    "/anomaly",
    response_model=AnomalyResponse,
    summary="Assess a transaction's behaviour with the Isolation Forest",
    responses={
        404: {"description": "No such transaction"},
        503: {"description": "No anomaly model is currently available"},
    },
)
def detect_anomaly(payload: AnomalyRequest, session: Session = Depends(get_db)) -> AnomalyResponse:
    """Score how unusual a transaction's behaviour is, independent of the fraud model.

    Uses the same point-in-time features as the supervised engine, narrowed to a
    behavioral subset, so the assessment reflects only what was knowable before
    the payment. The result is written to ``risk_signals``; it neither reads nor
    overwrites the supervised prediction in ``risk_predictions``.
    """
    transaction = catalog.get_transaction(session, payload.transaction_id)
    result, _ = score_and_store(session, transaction)
    session.commit()

    return AnomalyResponse(
        transaction_id=transaction.transaction_id,
        anomaly_score=result.anomaly_score,
        severity=str(result.severity),
        model_version=result.model_version,
        threshold=result.threshold,
        exceeds_threshold=result.exceeds_threshold,
        customer_deviation_score=result.customer_deviation_score,
        customer_deviation_driver=result.customer_deviation_driver,
        top_deviations=[
            FeatureDeviationRead(**deviation.as_dict()) for deviation in result.top_deviations
        ],
    )


@router.post(
    "/decision",
    response_model=DecisionResponse,
    summary="Decide a transaction with the deterministic policy engine",
    responses={
        404: {"description": "No such transaction"},
        503: {"description": "The policy configuration is invalid and could not be loaded"},
    },
)
def decide_transaction(
    payload: DecisionRequest, session: Session = Depends(get_db)
) -> DecisionResponse:
    """Apply the versioned policy to one transaction and record the outcome.

    The engine reads the stored fraud probability, anomaly score and
    investigation counts, applies typed rules, and returns exactly one of
    APPROVE, STEP_UP, REVIEW or BLOCK. No language model is consulted: the
    agent's recommendation is not even an input, and there is no field through
    which it could become one.

    The decision is appended to ``risk_decisions``, which is immutable - a
    re-decision adds a row rather than editing the previous one.
    """
    transaction = catalog.get_transaction(session, payload.transaction_id)
    try:
        policy = get_policy()
    except PolicyValidationError as exc:
        # Fail closed and loudly: an invalid policy must never fall back to a
        # default that nobody configured.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The risk policy configuration is invalid; no decision was made.",
        ) from exc

    result, row = decide_and_store(session, transaction, policy=policy)
    review_case = session.scalar(
        select(ReviewCase).where(ReviewCase.transaction_id == transaction.id)
    )
    review_case_id = review_case.id if review_case is not None else None
    session.commit()

    return _to_decision_response(result, row, review_case_id)
