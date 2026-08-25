"""The full risk picture for one transaction.

Assembles the whole pipeline - payment facts, both model signals, the
investigation with its evidence, the policy decision, and the audit trail - into
one response, so the detail page is a single round trip rather than six.

Everything here is a read. Nothing scores, decides or investigates: this module
reports what Phases 3 to 6 already stored.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Customer,
    Device,
    Investigation,
    IpAddress,
    Merchant,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.services.anomaly import ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL
from app.services.explorer import risk_level_for
from policy.loader import get_policy

logger = logging.getLogger(__name__)

#: How many audit entries the detail page carries. The full trail lives at
#: /api/audit; this is the recent context, bounded so one busy transaction
#: cannot return thousands of rows.
AUDIT_LIMIT = 25


def _transaction_block(session: Session, transaction: Transaction) -> dict[str, Any]:
    """Payment facts plus the entities involved, in one joined lookup."""
    row = session.execute(
        select(
            Customer.external_customer_id,
            Customer.country,
            Customer.historical_risk_level,
            Customer.account_created_at,
            Merchant.external_merchant_id,
            Merchant.name,
            Merchant.category,
        )
        .select_from(Transaction)
        .join(Customer, Customer.id == Transaction.customer_id)
        .join(Merchant, Merchant.id == Transaction.merchant_id)
        .where(Transaction.id == transaction.id)
    ).one()

    device = (
        session.get(Device, transaction.device_id) if transaction.device_id is not None else None
    )
    ip_record = (
        session.get(IpAddress, transaction.ip_address_id)
        if transaction.ip_address_id is not None
        else None
    )

    return {
        "transaction_id": transaction.transaction_id,
        "timestamp": transaction.transaction_timestamp,
        "amount": float(transaction.amount),
        "currency": transaction.currency,
        "status": str(transaction.status),
        "payment_method": str(transaction.payment_method),
        "is_fraud": bool(transaction.is_fraud),
        "failed_attempts": transaction.failed_attempts,
        "country": transaction.country,
        "city": transaction.city,
        "customer_id": row.external_customer_id,
        "customer_country": row.country,
        # Phase 2's *observed* historical band, not a prediction. Named
        # explicitly so it is never read as the policy's risk_level.
        "customer_historical_risk_level": str(row.historical_risk_level),
        "customer_since": row.account_created_at,
        "merchant_id": row.external_merchant_id,
        "merchant_name": row.name,
        "merchant_category": str(row.category),
        "device_id": device.device_id if device else None,
        "device_type": str(device.device_type) if device else None,
        "ip_address": ip_record.ip_address if ip_record else None,
        "ip_country": ip_record.country if ip_record else None,
        "ip_is_proxy": bool(ip_record.is_proxy) if ip_record else None,
    }


def _signals_block(session: Session, transaction: Transaction) -> dict[str, Any]:
    """Both model outputs, exactly as Phases 3 and 4 stored them."""
    prediction = session.scalar(
        select(RiskPrediction).where(RiskPrediction.transaction_id == transaction.id)
    )
    signals = session.execute(
        select(
            RiskSignal.signal_name, RiskSignal.signal_value, RiskSignal.severity, RiskSignal.source
        )
        .where(RiskSignal.transaction_id == transaction.id)
        .where(RiskSignal.signal_name.in_((ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL)))
    ).all()
    by_name = {name: (value, severity, source) for name, value, severity, source in signals}
    anomaly = by_name.get(ANOMALY_SIGNAL)
    deviation = by_name.get(CUSTOMER_DEVIATION_SIGNAL)
    probability = float(prediction.fraud_probability) if prediction else None

    return {
        "fraud_probability": probability,
        "risk_score": prediction.risk_score if prediction else None,
        "fraud_model_version": prediction.model_version if prediction else None,
        "risk_level": risk_level_for(probability, get_policy().thresholds),
        "anomaly_score": int(anomaly[0]) if anomaly else None,
        "anomaly_severity": str(anomaly[1]).upper() if anomaly else None,
        "anomaly_model_version": anomaly[2] if anomaly else None,
        "customer_deviation_score": int(deviation[0]) if deviation else None,
    }


def _investigation_block(session: Session, transaction: Transaction) -> dict[str, Any] | None:
    """The stored investigation, rendered verbatim.

    Findings and evidence are returned exactly as Phase 5 wrote them. The
    frontend renders what it is given and invents nothing.
    """
    row = session.scalar(
        select(Investigation).where(Investigation.transaction_id == transaction.id)
    )
    if row is None:
        return None

    report: dict[str, Any] = row.report or {}
    return {
        "investigation_id": row.public_id,
        "status": str(row.status),
        "risk_level": report.get("risk_level"),
        "confidence": float(row.confidence) if row.confidence is not None else None,
        "summary": row.summary,
        "recommended_action": report.get("recommended_action"),
        "agent_is_mock": bool(row.agent_is_mock),
        "iteration_count": row.iteration_count,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "findings": report.get("findings") or [],
        "evidence": report.get("evidence") or [],
        "confidence_basis": report.get("confidence_basis"),
        "trace": report.get("trace"),
    }


def _decision_block(session: Session, transaction: Transaction) -> dict[str, Any] | None:
    """The current decision, plus how many preceded it."""
    row = session.scalar(
        select(RiskDecision)
        .where(RiskDecision.transaction_id == transaction.id)
        .order_by(RiskDecision.decided_at.desc(), RiskDecision.id.desc())
        .limit(1)
    )
    if row is None:
        return None

    detail: dict[str, Any] = row.detail or {}
    history_count = (
        session.scalar(
            select(func.count(RiskDecision.id)).where(RiskDecision.transaction_id == transaction.id)
        )
        or 0
    )

    return {
        "history_count": history_count,
        "decision_id": row.public_id,
        "decision": str(row.action).upper(),
        "policy_version": row.policy_version,
        "decided_at": row.decided_at,
        "matched_rules": list(row.matched_rules),
        "deciding_rules": list(detail.get("deciding_rules") or []),
        "reason_codes": list(row.reason_codes),
        "rule_matches": detail.get("rule_matches") or [],
        "explanation": row.explanation,
        "requires_human_review": bool(row.requires_human_review),
        "input_digest": row.input_digest,
        "evaluation_ms": row.evaluation_ms,
    }


def _review_block(session: Session, transaction: Transaction) -> dict[str, Any] | None:
    """The review case, if the decision opened one."""
    case = session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    if case is None:
        return None
    return {
        "review_case_id": case.id,
        "status": str(case.status),
        "resolution": str(case.resolution) if case.resolution else None,
        "resolution_reason": case.resolution_reason,
        "created_at": case.created_at,
        "resolved_at": case.resolved_at,
    }


def _audit_block(session: Session, transaction: Transaction) -> list[dict[str, Any]]:
    """Recent audit entries for this transaction, newest first."""
    rows = session.execute(
        select(
            AuditLog.id,
            AuditLog.event_type,
            AuditLog.actor_type,
            AuditLog.actor_id,
            AuditLog.event_data,
            AuditLog.created_at,
        )
        .where(AuditLog.transaction_id == transaction.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(AUDIT_LIMIT)
    ).all()
    return [
        {
            "audit_id": entry_id,
            "event_type": event_type,
            "actor_type": str(actor_type),
            "actor_id": actor_id,
            "event_data": event_data,
            "created_at": created_at,
        }
        for entry_id, event_type, actor_type, actor_id, event_data, created_at in rows
    ]


def build_detail(session: Session, transaction: Transaction) -> dict[str, Any]:
    """The complete risk picture for one transaction."""
    return {
        "transaction": _transaction_block(session, transaction),
        "signals": _signals_block(session, transaction),
        "investigation": _investigation_block(session, transaction),
        "decision": _decision_block(session, transaction),
        "review": _review_block(session, transaction),
        "audit": _audit_block(session, transaction),
    }
