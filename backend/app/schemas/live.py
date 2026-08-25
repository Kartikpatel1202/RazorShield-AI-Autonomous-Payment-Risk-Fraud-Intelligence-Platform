"""Request and response models for ingestion, the simulator and the live stream.

The ingestion schema is the security boundary of this phase. It accepts the
fields a payment processor would legitimately send and nothing else - notably
not ``is_fraud`` (the dataset's ground-truth label), not a fraud probability,
not an anomaly score and not a decision. A caller cannot assert a risk outcome,
only describe a payment.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    DeviceType,
    PaymentMethod,
    SimulatorScenario,
    TransactionStatus,
)
from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN

#: Device fingerprints and IPs arrive from outside, so their charset is
#: constrained before they reach a query or a log line.
IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
IP_PATTERN = r"^[0-9a-fA-F.:]{3,45}$"

#: City is the one free-text field a submitter controls, and it travels a long
#: way: into a log line, into the transaction detail page, and into a tool
#: payload the investigation agent reads. An allowlist of letters, marks,
#: digits, spaces and ordinary name punctuation keeps "Sao Paulo" and
#: "Stratford-upon-Avon" working while excluding newlines, angle brackets and
#: the other characters an injected instruction needs to look like structure.
CITY_PATTERN = r"^[\w .,'()/-]{1,64}$"


class TransactionEventIn(BaseModel):
    """One inbound payment event."""

    model_config = {"extra": "forbid"}

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=TRANSACTION_REFERENCE_PATTERN,
        description="Unique reference. Re-submitting one is a no-op, not a second decision.",
    )
    amount: Decimal = Field(gt=0, le=Decimal("100000000"), description="Positive, in `currency`.")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    customer_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER_PATTERN)
    merchant_id: str = Field(min_length=1, max_length=64, pattern=IDENTIFIER_PATTERN)
    payment_method: PaymentMethod
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    city: str = Field(min_length=1, max_length=64, pattern=CITY_PATTERN)
    timestamp: datetime
    device_id: str | None = Field(default=None, max_length=64, pattern=IDENTIFIER_PATTERN)
    device_type: DeviceType | None = None
    ip_address: str | None = Field(default=None, max_length=45, pattern=IP_PATTERN)
    ip_country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    ip_is_proxy: bool = False
    status: TransactionStatus = TransactionStatus.PENDING
    failed_attempts: int = Field(default=0, ge=0, le=100)

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """A naive timestamp is ambiguous, and the feature layer orders by it.

        Rejecting is safer than assuming UTC: a wrong ordering silently changes
        what the point-in-time features count as history.
        """
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone offset")
        return value


class StageLatencies(BaseModel):
    """Measured milliseconds per pipeline stage."""

    persistence: float | None = None
    risk_scoring: float | None = None
    anomaly_detection: float | None = None
    policy_load: float | None = None
    investigation: float | None = None
    decision: float | None = None


class IngestResponse(BaseModel):
    """What the pipeline did with one submitted event."""

    transaction_id: str
    accepted: bool
    duplicate: bool = Field(
        description="True when this reference was already processed. No second decision was made."
    )
    simulated: bool
    fraud_probability: float | None = None
    risk_score: int | None = None
    anomaly_score: int | None = None
    anomaly_severity: str | None = None
    investigated: bool = False
    investigation_id: str | None = None
    decision: str | None = None
    decision_id: str | None = None
    matched_rules: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    stage_latencies_ms: StageLatencies = Field(default_factory=StageLatencies)
    total_ms: float
    error: str | None = None
    failed_stage: str | None = Field(
        default=None,
        description="Which pipeline stage raised, when `error` is set.",
    )
    correlation_id: str | None = Field(
        default=None,
        description=(
            "The id this run's log lines carry. Quoting it in a bug report is "
            "enough to retrieve the whole trace."
        ),
    )


class RiskEventRead(BaseModel):
    """One event from the live stream."""

    event_id: str
    sequence: int = Field(description="Monotonic across the stream; the SSE `id` field.")
    transaction_id: str
    event_type: str
    transaction_sequence: int = Field(
        description="Position within this transaction's own ordering, starting at 1."
    )
    timestamp: datetime
    payload: dict[str, Any]


class EventPage(BaseModel):
    events: list[RiskEventRead]
    latest_sequence: int = Field(description="Highest sequence in the stream, for resuming.")


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


class SimulatorStartRequest(BaseModel):
    """A run request. Every field is bounded."""

    model_config = {"extra": "forbid"}

    scenario: SimulatorScenario = SimulatorScenario.NORMAL
    transactions_per_second: float = Field(default=2.0, ge=0.1, le=50.0)
    max_transactions: int = Field(
        default=50,
        ge=1,
        le=5000,
        description="Hard cap. The simulator never runs indefinitely by default.",
    )
    seed: int = Field(default=42, ge=0, le=2**31 - 1, description="Same seed, same sequence.")


class SimulatorDecisionCounts(BaseModel):
    approve: int = 0
    step_up: int = 0
    review: int = 0
    block: int = 0


class SimulatorRecentResult(BaseModel):
    """One recently processed transaction, newest first."""

    transaction_id: str
    decision: str | None = None
    fraud_probability: float | None = None
    anomaly_score: int | None = None
    investigated: bool = False
    duplicate: bool = False
    error: str | None = None
    total_ms: float


class SimulatorStatus(BaseModel):
    """Live simulator state. Every figure is observed, none estimated."""

    state: str
    run_id: str | None = None
    scenario: str | None = None
    transactions_per_second: float | None = Field(
        default=None, description="Requested rate. Compare with observed_tps."
    )
    max_transactions: int | None = None
    seed: int | None = None
    generated: int = 0
    processed: int = 0
    duplicates: int = 0
    failed: int = 0
    queue_depth: int = Field(description="Events waiting. Sustained depth means saturation.")
    queue_capacity: int
    observed_tps: float = Field(description="Completions per second over the recent window.")
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    uptime_seconds: float | None = None
    decisions: SimulatorDecisionCounts = Field(default_factory=SimulatorDecisionCounts)
    investigations: int = 0
    recent: list[SimulatorRecentResult] = Field(default_factory=list)


class ScenarioRead(BaseModel):
    """A documented scenario.

    ``expected_signal`` describes the behaviour the pipeline should notice - not
    the decision. The decision is measured from the run.
    """

    scenario: str
    title: str
    behaviour: str
    expected_signal: str


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioRead]
    note: str


# --------------------------------------------------------------------------
# Live metrics
# --------------------------------------------------------------------------


class LiveMetrics(BaseModel):
    """Counters for the live surface. All read from the database or the engine."""

    transactions_processed: int = Field(description="Simulated transactions decided so far.")
    transactions_per_second: float
    high_risk_count: int
    review_count: int
    block_count: int
    approve_count: int
    step_up_count: int
    active_investigations: int = Field(
        description="Investigations recorded for simulated transactions."
    )
    queue_depth: int
    queue_capacity: int
    uptime_seconds: float | None = None
    simulator_state: str
    scenario: str | None = None
    connected_clients: int
    dropped_deliveries: int = Field(
        description="Events skipped for clients too slow to keep up. The durable copy remains."
    )
    total_events: int
    latest_sequence: int
