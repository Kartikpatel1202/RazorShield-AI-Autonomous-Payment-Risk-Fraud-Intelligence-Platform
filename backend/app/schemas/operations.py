"""Response models for the operations surfaces: explorer, detail, audit, policy.

These schemas describe what the risk console renders. They are deliberately
verbose about provenance - model versions, policy version, decision digest -
because the console's job is to make a decision checkable, and a field the
reader cannot trace back is decoration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Explorer
# --------------------------------------------------------------------------


class ExplorerRow(BaseModel):
    """One row of the transaction explorer, joined across five tables."""

    transaction_id: str
    timestamp: datetime
    amount: float
    currency: str
    status: str
    is_fraud: bool = Field(description="Ground-truth label from the simulated dataset.")
    customer_id: str
    merchant_id: str
    merchant_name: str
    fraud_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: str | None = Field(
        default=None, description="Band derived from the active policy's thresholds."
    )
    anomaly_score: int | None = Field(default=None, ge=0, le=100)
    anomaly_severity: str | None = None
    decision: str | None = Field(default=None, description="Null when never decided.")
    policy_version: str | None = None
    requires_human_review: bool | None = None


# --------------------------------------------------------------------------
# Transaction detail
# --------------------------------------------------------------------------


class DetailTransaction(BaseModel):
    """Stored payment facts and the entities involved."""

    transaction_id: str
    timestamp: datetime
    amount: float
    currency: str
    status: str
    payment_method: str
    is_fraud: bool
    failed_attempts: int
    country: str
    city: str
    customer_id: str
    customer_country: str
    customer_historical_risk_level: str = Field(
        description="Phase 2's observed band for this customer. Not a prediction."
    )
    customer_since: datetime | None = None
    merchant_id: str
    merchant_name: str
    merchant_category: str
    device_id: str | None = None
    device_type: str | None = None
    ip_address: str | None = None
    ip_country: str | None = None
    ip_is_proxy: bool | None = None


class DetailSignals(BaseModel):
    """Both model outputs, with the versions that produced them."""

    fraud_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_score: int | None = Field(default=None, ge=0, le=100)
    fraud_model_version: str | None = None
    risk_level: str | None = None
    anomaly_score: int | None = Field(default=None, ge=0, le=100)
    anomaly_severity: str | None = None
    anomaly_model_version: str | None = None
    customer_deviation_score: int | None = None


class DetailInvestigation(BaseModel):
    """The stored investigation, rendered verbatim.

    ``findings`` and ``evidence`` pass through exactly as Phase 5 wrote them.
    They are not re-typed here because re-typing would mean re-interpreting, and
    the console must show what the agent actually produced.
    """

    investigation_id: str | None = None
    status: str
    risk_level: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str | None = None
    recommended_action: str | None = Field(
        default=None,
        description=(
            "The agent's suggestion. Advisory only - the policy engine never reads it, "
            "and it is shown separately from the decision for exactly that reason."
        ),
    )
    agent_is_mock: bool
    iteration_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence_basis: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None


class DetailDecision(BaseModel):
    """The current policy decision and its full justification."""

    decision_id: str
    decision: str
    policy_version: str
    decided_at: datetime
    matched_rules: list[str]
    deciding_rules: list[str]
    reason_codes: list[str]
    rule_matches: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str
    requires_human_review: bool
    input_digest: str
    evaluation_ms: float | None = None
    history_count: int = Field(
        ge=0, description="How many decisions exist for this transaction, including this one."
    )


class DetailReview(BaseModel):
    """The review case, when the decision opened one."""

    review_case_id: int
    status: str
    resolution: str | None = None
    resolution_reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class AuditEntry(BaseModel):
    """One audit event."""

    audit_id: int
    event_type: str
    actor_type: str
    actor_id: str | None = None
    transaction_id: str | None = None
    created_at: datetime
    decision: str | None = None
    decision_id: str | None = None
    policy_version: str | None = None
    investigation_id: str | None = None
    resolution: str | None = None
    event_data: dict[str, Any] = Field(default_factory=dict)


class TransactionDetailResponse(BaseModel):
    """The whole risk pipeline for one transaction, in one round trip."""

    transaction: DetailTransaction
    signals: DetailSignals
    investigation: DetailInvestigation | None = None
    decision: DetailDecision | None = None
    review: DetailReview | None = None
    audit: list[AuditEntry] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


class AuditSummaryResponse(BaseModel):
    """Counts per event type, for the audit page's filter chips."""

    counts: dict[str, int]
    known_event_types: list[str]


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


class PolicyRuleRead(BaseModel):
    """One rule as the read-only viewer shows it."""

    rule_id: str
    action: str
    enabled: bool
    description: str


class PolicyThresholds(BaseModel):
    fraud_block: float
    fraud_high: float
    fraud_medium: float
    anomaly_critical: float
    anomaly_high: float
    anomaly_medium: float


class PolicyEvidence(BaseModel):
    min_independent_sources_for_block: int
    min_high_findings_for_review: int
    min_investigation_confidence: float


class PolicyFailSafe(BaseModel):
    missing_supervised_signal: str
    missing_anomaly_signal: str
    missing_investigation: str
    require_investigation_for_block: bool


class PolicyResponse(BaseModel):
    """The active policy, read-only.

    Phase 7 deliberately exposes no way to edit this. A policy change must be a
    reviewed change to a versioned file, not a form submission.
    """

    policy_version: str
    description: str
    source: str
    thresholds: PolicyThresholds
    evidence: PolicyEvidence
    fail_safe: PolicyFailSafe
    action_precedence: list[str]
    default_action: str
    human_review_required_for: list[str]
    rules: list[PolicyRuleRead]
    reason_codes: list[str]
    editable: bool = Field(
        default=False,
        description="Always false in Phase 7. The viewer is read-only by design.",
    )


# --------------------------------------------------------------------------
# System health
# --------------------------------------------------------------------------


class ComponentHealth(BaseModel):
    """One subsystem's status."""

    name: str
    status: str = Field(description="ok, degraded or unavailable")
    detail: str | None = None
    version: str | None = None


class SystemHealthResponse(BaseModel):
    """Every subsystem the console reports on."""

    status: str = Field(description="Worst component status, rolled up.")
    components: list[ComponentHealth]
