"""Running investigations and persisting them.

The agent produces a structured record; this module stores it and writes an
audit entry. It is the only place investigation results reach the database, and
it writes to exactly three tables - ``investigations``, ``audit_logs`` and
nothing else. It never touches ``risk_predictions`` or ``risk_signals``: the
agent reads those signals, it does not revise them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.investigator import investigate_transaction
from agent.llm.base import LLMProvider
from agent.schemas.investigation import (
    Investigation as AgentInvestigation,
)
from agent.schemas.investigation import (
    RecommendedAction as AgentAction,
)
from app.core.metrics import investigation_latency, investigations_total, observe_stage
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import AuditLog, Investigation, RiskPrediction, RiskSignal, Transaction
from app.models.enums import ActorType, InvestigationStatus, RecommendedAction
from app.services.anomaly import ANOMALY_SIGNAL

logger = logging.getLogger(__name__)

AGENT_ACTOR = "risk-investigation-agent"

#: The agent speaks of APPROVE; the Phase 2 column calls the same outcome ALLOW.
#: Mapped here rather than by widening the stored enum, so the persisted
#: vocabulary stays the one the decision engine will read in a later phase.
_ACTION_TO_COLUMN: dict[AgentAction, RecommendedAction] = {
    AgentAction.APPROVE: RecommendedAction.ALLOW,
    AgentAction.STEP_UP: RecommendedAction.STEP_UP,
    AgentAction.REVIEW: RecommendedAction.REVIEW,
    AgentAction.BLOCK: RecommendedAction.BLOCK,
}

_STATUS_TO_COLUMN: dict[str, InvestigationStatus] = {
    "completed": InvestigationStatus.COMPLETED,
    "insufficient_evidence": InvestigationStatus.INSUFFICIENT_EVIDENCE,
    "agent_unavailable": InvestigationStatus.AGENT_UNAVAILABLE,
    "failed": InvestigationStatus.FAILED,
}


def collect_model_versions(session: Session, transaction: Transaction) -> dict[str, str]:
    """Which model versions produced the signals the agent will read."""
    versions: dict[str, str] = {}

    prediction = session.scalar(
        select(RiskPrediction).where(RiskPrediction.transaction_id == transaction.id)
    )
    if prediction is not None:
        versions["fraud_model"] = prediction.model_version

    signal = session.scalar(
        select(RiskSignal).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name == ANOMALY_SIGNAL,
        )
    )
    if signal is not None:
        versions["anomaly_model"] = signal.source

    return versions


def build_report(result: AgentInvestigation) -> dict[str, Any]:
    """The persisted JSON document.

    Deliberately excludes prompts, raw model output and anything derived from
    credentials: the trace records what was called and what it cost, not what
    was said.
    """
    return {
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
        "evidence": [item.model_dump(mode="json") for item in result.evidence],
        "confidence_basis": result.confidence_basis.model_dump(mode="json"),
        "risk_level": str(result.risk_level),
        "recommended_action": str(result.recommended_action),
        "trace": result.as_trace(),
    }


def store_investigation(
    session: Session, transaction: Transaction, result: AgentInvestigation
) -> Investigation:
    """Persist an investigation, replacing any earlier one for this transaction.

    ``investigations.transaction_id`` is unique, so the table holds the latest
    investigation per transaction - which is what the read endpoints promise.
    """
    row = session.scalar(
        select(Investigation).where(Investigation.transaction_id == transaction.id)
    )
    if row is None:
        row = Investigation(transaction_id=transaction.id)
        session.add(row)

    row.public_id = result.investigation_id
    row.status = _STATUS_TO_COLUMN[str(result.status)]
    row.started_at = result.started_at
    row.completed_at = result.completed_at or datetime.now(UTC)
    row.summary = result.summary
    row.confidence = Decimal(str(result.confidence)).quantize(Decimal("0.0001"))
    row.recommended_action = _ACTION_TO_COLUMN[result.recommended_action]
    row.iteration_count = result.iteration_count
    row.agent_is_mock = result.llm.is_mock
    row.report = build_report(result)

    session.flush()
    return row


def write_audit_entry(
    session: Session, transaction: Transaction, result: AgentInvestigation
) -> AuditLog:
    """Record that an investigation happened, for the timeline.

    ``event_data`` carries identifiers and outcomes only - no evidence text, no
    prompts, no secrets.
    """
    entry = AuditLog(
        transaction_id=transaction.id,
        actor_type=ActorType.AGENT,
        actor_id=AGENT_ACTOR,
        event_type="investigation.completed",
        event_data={
            "investigation_id": result.investigation_id,
            "status": str(result.status),
            "risk_level": str(result.risk_level),
            "recommended_action": str(result.recommended_action),
            "confidence": result.confidence,
            "iterations": result.iteration_count,
            "tools_used": [str(tool) for tool in result.tools_used],
            "evidence_count": len(result.evidence),
            "finding_count": len(result.findings),
            "llm_provider": result.llm.provider,
            "llm_model": result.llm.model,
            "llm_is_mock": result.llm.is_mock,
        },
    )
    session.add(entry)
    session.flush()
    return entry


def run_investigation(
    session: Session, transaction: Transaction, *, provider: LLMProvider | None = None
) -> tuple[AgentInvestigation, Investigation]:
    """Investigate a transaction, persist the result, and record the audit event."""
    log_lifecycle(
        LifecycleEvent.INVESTIGATION_STARTED,
        transaction_id=transaction.transaction_id,
    )
    versions = collect_model_versions(session, transaction)
    with observe_stage(investigation_latency):
        result = investigate_transaction(
            session, transaction, provider=provider, model_versions=versions
        )
        row = store_investigation(session, transaction, result)
        write_audit_entry(session, transaction, result)

    investigations_total.labels(status=str(result.status)).inc()
    log_lifecycle(
        LifecycleEvent.INVESTIGATION_COMPLETED,
        transaction_id=transaction.transaction_id,
        investigation_id=row.public_id,
        status=str(result.status),
        # The agent's risk_level and confidence are recorded because they are
        # part of what happened. Neither reaches the policy engine - Phase 6
        # takes findings only - and logging them does not change that.
        risk_level=str(result.risk_level),
        confidence=result.confidence,
        findings=len(result.findings),
        evidence=len(result.evidence),
        agent_is_mock=result.llm.is_mock,
    )
    return result, row


def load_by_public_id(session: Session, public_id: str) -> Investigation | None:
    return session.scalar(select(Investigation).where(Investigation.public_id == public_id))


def load_latest_for_transaction(session: Session, transaction: Transaction) -> Investigation | None:
    return session.scalar(
        select(Investigation).where(Investigation.transaction_id == transaction.id)
    )
