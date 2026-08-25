"""Tools that read the Phase 3 and Phase 4 model outputs.

Neither tool trains, retrains or tunes anything. Each prefers the stored result
and, when none exists, asks the already-loaded predictor to score the
transaction - the same call the risk endpoints make. The agent cannot change a
score, and the models remain the only source of a probability or an anomaly
value anywhere in the system.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from agent.schemas.evidence import EvidenceSeverity
from agent.tools.base import EvidenceDraft, ToolContext, ToolResult
from app.models import RiskPrediction, RiskSignal
from app.services.anomaly import ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL, score_transaction
from app.services.risk import score_transaction as score_fraud
from ml.inference.predictor import ModelNotAvailableError

logger = logging.getLogger(__name__)

#: Bands for turning a model output into evidence severity. These describe how
#: notable a score is, not what to do about it.
FRAUD_PROBABILITY_HIGH = 0.5
FRAUD_PROBABILITY_MEDIUM = 0.15
ANOMALY_SCORE_CRITICAL = 99
ANOMALY_SCORE_HIGH = 97
ANOMALY_SCORE_MEDIUM = 92


def get_ml_prediction(ctx: ToolContext) -> ToolResult:
    """The supervised fraud probability for this transaction."""
    stored = ctx.session.scalar(
        select(RiskPrediction).where(RiskPrediction.transaction_id == ctx.transaction.id)
    )

    if stored is not None:
        probability = float(stored.fraud_probability)
        risk_score = int(stored.risk_score)
        model_version = stored.model_version
        source = "stored"
    else:
        try:
            prediction = score_fraud(ctx.session, ctx.transaction)
        except ModelNotAvailableError:
            return ToolResult(payload={"available": False, "reason": "no fraud model is loaded"})
        probability = prediction.fraud_probability
        risk_score = prediction.risk_score
        model_version = prediction.model_version
        source = "computed"

    payload = {
        "available": True,
        "fraud_probability": round(probability, 6),
        "risk_score": risk_score,
        "model_version": model_version,
        "source": source,
    }

    if probability >= FRAUD_PROBABILITY_HIGH:
        severity = EvidenceSeverity.HIGH
        claim = f"Supervised fraud model scores this transaction {probability:.3f}"
    elif probability >= FRAUD_PROBABILITY_MEDIUM:
        severity = EvidenceSeverity.MEDIUM
        claim = f"Supervised fraud model scores this transaction {probability:.3f}, above baseline"
    else:
        severity = EvidenceSeverity.INFO
        claim = f"Supervised fraud model scores this transaction {probability:.3f}, near baseline"

    return ToolResult(
        payload=payload,
        evidence=[
            EvidenceDraft(
                claim=claim,
                severity=severity,
                value=round(probability, 6),
                details={"risk_score": risk_score, "model_version": model_version},
            )
        ],
    )


def get_anomaly_result(ctx: ToolContext) -> ToolResult:
    """The unsupervised behavioural anomaly assessment for this transaction."""
    rows = list(
        ctx.session.scalars(
            select(RiskSignal).where(
                RiskSignal.transaction_id == ctx.transaction.id,
                RiskSignal.signal_name.in_((ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL)),
            )
        )
    )
    stored = {row.signal_name: row for row in rows}

    if ANOMALY_SIGNAL in stored:
        row = stored[ANOMALY_SIGNAL]
        anomaly_score = int(row.signal_value)
        severity_label = str(row.severity).upper()
        model_version = row.source
        deviation_row = stored.get(CUSTOMER_DEVIATION_SIGNAL)
        deviation = int(deviation_row.signal_value) if deviation_row else None
        driver = None
        source = "stored"
        top_deviations: list[dict[str, object]] = []
    else:
        try:
            result = score_transaction(ctx.session, ctx.transaction)
        except Exception as exc:  # noqa: BLE001 - reported as unavailable, never fatal
            logger.warning("Anomaly model unavailable during investigation: %s", exc)
            return ToolResult(payload={"available": False, "reason": "no anomaly model is loaded"})
        anomaly_score = result.anomaly_score
        severity_label = str(result.severity)
        model_version = result.model_version
        deviation = result.customer_deviation_score
        driver = result.customer_deviation_driver
        source = "computed"
        top_deviations = [
            {
                "feature": item.feature,
                "value": item.value,
                "percentile": item.percentile,
            }
            for item in result.top_deviations[:5]
        ]

    payload = {
        "available": True,
        "anomaly_score": anomaly_score,
        "severity": severity_label,
        "model_version": model_version,
        "customer_deviation_score": deviation,
        "customer_deviation_driver": driver,
        "top_deviations": top_deviations,
        "source": source,
    }

    if anomaly_score >= ANOMALY_SCORE_CRITICAL:
        evidence_severity = EvidenceSeverity.CRITICAL
    elif anomaly_score >= ANOMALY_SCORE_HIGH:
        evidence_severity = EvidenceSeverity.HIGH
    elif anomaly_score >= ANOMALY_SCORE_MEDIUM:
        evidence_severity = EvidenceSeverity.MEDIUM
    else:
        evidence_severity = EvidenceSeverity.INFO

    evidence = [
        EvidenceDraft(
            claim=(
                f"Behavioural anomaly engine scores this transaction {anomaly_score}/100 "
                f"({severity_label}), meaning it is more unusual than {anomaly_score}% of "
                "known-normal behaviour"
            ),
            severity=evidence_severity,
            value=float(anomaly_score),
            details={"severity": severity_label, "model_version": model_version},
        )
    ]
    if driver and deviation is not None and deviation >= 90:
        evidence.append(
            EvidenceDraft(
                claim=(f"Customer-relative deviation is {deviation}/100, driven by {driver}"),
                severity=EvidenceSeverity.MEDIUM,
                value=float(deviation),
                details={"driver": driver},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)
