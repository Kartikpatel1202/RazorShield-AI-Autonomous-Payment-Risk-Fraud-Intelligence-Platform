"""Response models for the operations analytics endpoints.

Every figure carries its scope. A count without a denominator or a time range is
the kind of number that looks authoritative and means nothing, so the schemas
force each one to travel with the context needed to read it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BucketRead(BaseModel):
    """One labelled count, with the range it covers when the bucket is numeric."""

    label: str
    count: int = Field(ge=0)
    lower: float | None = None
    upper: float | None = None


class OverviewResponse(BaseModel):
    """Headline dashboard counters."""

    total_transactions: int = Field(ge=0, description="Every transaction in the dataset.")
    decided_transactions: int = Field(
        ge=0,
        description=(
            "Transactions with at least one policy decision. The four action counts "
            "below sum to this, not to total_transactions."
        ),
    )
    approved: int = Field(ge=0)
    step_up: int = Field(ge=0)
    review: int = Field(ge=0)
    blocked: int = Field(ge=0)

    high_risk_transactions: int = Field(
        ge=0, description="Fraud probability at or above the policy's high threshold."
    )
    critical_anomalies: int = Field(
        ge=0, description="Anomaly score at or above the policy's critical threshold."
    )
    open_review_cases: int = Field(ge=0, description="Cases open or in review.")
    escalated_review_cases: int = Field(ge=0)
    completed_investigations: int = Field(ge=0)

    avg_decision_latency_ms: float | None = Field(
        default=None,
        description=(
            "Mean policy evaluation time across current decisions. Measures the "
            "engine alone, not the surrounding request. Null when unmeasured."
        ),
    )
    min_decision_latency_ms: float | None = None
    max_decision_latency_ms: float | None = None
    latency_sample_size: int = Field(ge=0, description="Decisions carrying a latency measurement.")

    policy_version: str
    high_risk_threshold: float = Field(
        ge=0.0, le=1.0, description="The policy threshold behind high_risk_transactions."
    )
    critical_anomaly_threshold: float = Field(
        ge=0.0, le=100.0, description="The policy threshold behind critical_anomalies."
    )
    data_from: datetime | None = Field(
        default=None, description="Earliest transaction timestamp in the dataset."
    )
    data_to: datetime | None = Field(
        default=None, description="Latest transaction timestamp in the dataset."
    )


class RiskDistributionResponse(BaseModel):
    """Four distributions, each computed by SQL aggregation."""

    decisions: list[BucketRead]
    fraud_probability: list[BucketRead]
    anomaly_severity: list[BucketRead]
    risk_level: list[BucketRead]
    policy_version: str


class TrendPoint(BaseModel):
    """One day of transaction volume, split by disposition."""

    day: datetime
    volume: int = Field(ge=0)
    high_risk: int = Field(ge=0)
    review: int = Field(ge=0)
    blocked: int = Field(ge=0)
    step_up: int = Field(ge=0)
    approved: int = Field(ge=0)


class TrendResponse(BaseModel):
    """A bounded time series."""

    window_days: int = Field(ge=1, description="Days covered, after clamping.")
    points: list[TrendPoint]
    #: Present so the caller can tell "no traffic" from "window out of range".
    data_from: datetime | None = None
    data_to: datetime | None = None


class TopRiskItem(BaseModel):
    """One high-risk transaction, joined with its merchant and customer."""

    transaction_id: str
    timestamp: datetime
    amount: float
    currency: str
    merchant_name: str
    customer_id: str
    decision: str
    fraud_probability: float | None = None
    anomaly_score: int | None = None
    anomaly_severity: str | None = None


class TopRiskResponse(BaseModel):
    items: list[TopRiskItem]


class DecisionAnalyticsResponse(BaseModel):
    """Decision mix plus the reason codes actually driving it."""

    distribution: list[BucketRead]
    reason_codes: list[BucketRead]
    policy_version: str
    decided_transactions: int = Field(ge=0)
