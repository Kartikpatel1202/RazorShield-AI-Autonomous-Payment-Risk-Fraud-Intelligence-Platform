"""Request and response models for analyst feedback and monitoring.

The recurring theme: every metric travels with the evidence needed to judge it -
the sample size behind it, the window it covers, and whether there was enough
data to compute it at all. A rate returned without those is a number a reader
cannot check.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import FeedbackOutcome, FeedbackReason
from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN

#: Public identifiers are opaque business keys. Constrained so a hostile value
#: cannot reach a query even though it is always bound.
PUBLIC_ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------
class FeedbackCreateRequest(BaseModel):
    """An analyst recording what they concluded about a transaction."""

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=TRANSACTION_REFERENCE_PATTERN,
        examples=["TXN_SCENARIO_C_CURRENT_1"],
    )
    outcome: FeedbackOutcome
    reason_code: FeedbackReason = Field(
        description=(
            "Must be a reason permitted for the chosen outcome. The pair is validated, "
            "so an incoherent combination is rejected rather than stored."
        )
    )
    notes: str | None = Field(
        default=None,
        max_length=4000,
        description="The analyst's own words. Stored verbatim and never aggregated.",
    )
    analyst_id: int | None = Field(default=None, ge=1)
    review_case_id: int | None = Field(
        default=None, ge=1, description="Links this label to the case it came from."
    )


class FeedbackRead(BaseModel):
    """One recorded analyst conclusion."""

    feedback_id: str
    transaction_id: str
    decision_id: str | None = None
    review_case_id: int | None = None
    analyst_id: int | None = None
    outcome: str
    reason_code: str
    notes: str | None = None
    machine_decision: str | None = Field(
        default=None,
        description="The engine's decision at the time. Unchanged by this feedback.",
    )
    policy_version: str | None = None
    created_at: datetime


class ReasonBucket(BaseModel):
    reason_code: str
    count: int = Field(ge=0)


class FeedbackSummary(BaseModel):
    """Counts per outcome, plus the coverage that qualifies them."""

    total_feedback: int = Field(ge=0)
    confirmed_fraud: int = Field(ge=0)
    legitimate: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    insufficient_evidence: int = Field(ge=0)
    escalated: int = Field(ge=0)
    ground_truth_labels: int = Field(
        ge=0,
        description=(
            "Feedback that asserts what actually happened. Excludes "
            "INSUFFICIENT_EVIDENCE and ESCALATED, which leave the question open."
        ),
    )
    total_transactions: int = Field(ge=0)
    total_review_cases: int = Field(ge=0)
    labelled_share_of_transactions: float = Field(ge=0.0, le=1.0)
    by_reason: list[ReasonBucket]


class ConfusionCell(BaseModel):
    machine_decision: str
    outcome: str
    actually_fraud: bool
    count: int = Field(ge=0)


class ConfusionMatrix(BaseModel):
    """Machine decision against analyst ground truth.

    Only ground-truth outcomes are included; open outcomes are counted in
    ``excluded_open_outcomes`` rather than assigned to a class they were never
    given. Unlabelled transactions do not appear at all - they are not negatives.
    """

    cells: list[ConfusionCell]
    machine_actions: list[str]
    true_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    labelled_included: int = Field(ge=0)
    excluded_open_outcomes: int = Field(ge=0)


class FeedbackSummaryResponse(BaseModel):
    summary: FeedbackSummary
    confusion_matrix: ConfusionMatrix


# --------------------------------------------------------------------------
# Model metrics
# --------------------------------------------------------------------------
class ModelMetricsResponse(BaseModel):
    """Performance against analyst labels, or an honest refusal to guess."""

    sufficient: bool = Field(
        description="False when too few labels exist for the metrics to mean anything."
    )
    message: str | None = Field(
        default=None, description="Why the metrics were withheld, when they were."
    )
    selection_bias_note: str | None = Field(
        default=None,
        description=(
            "Set when the labelled sample cannot support every metric - for example "
            "when all labels came from the review queue, so no un-flagged examples "
            "exist and recall is 1.0 by construction."
        ),
    )
    labelled_flagged: int | None = Field(
        default=None, description="Labelled transactions the system flagged."
    )
    labelled_unflagged: int | None = Field(
        default=None, description="Labelled transactions the system approved."
    )

    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    false_positive_rate: float | None = None
    false_negative_rate: float | None = None
    true_positive: int | None = None
    false_positive: int | None = None
    true_negative: int | None = None
    false_negative: int | None = None

    labelled_samples: int = Field(ge=0)
    total_feedback: int = Field(ge=0)
    open_outcome_labels: int = Field(ge=0)
    unlabelled_transactions: int = Field(
        ge=0, description="Excluded from every metric above. Not treated as negatives."
    )
    total_transactions: int = Field(ge=0)
    minimum_required: int = Field(ge=1)
    label_source: str


class LabelCoverage(BaseModel):
    """The three label categories, kept apart deliberately."""

    total_transactions: int = Field(ge=0)
    confirmed_labels: int = Field(ge=0)
    analyst_feedback_total: int = Field(ge=0)
    open_outcome_labels: int = Field(ge=0)
    unlabelled: int = Field(ge=0)
    simulated_fraud_flags: int = Field(
        ge=0, description="The dataset's generation-time flag. Never used as ground truth."
    )
    simulated_label_note: str


class ModelMonitoringResponse(BaseModel):
    metrics: ModelMetricsResponse
    coverage: LabelCoverage


# --------------------------------------------------------------------------
# Score windows and drift
# --------------------------------------------------------------------------
class WindowSummary(BaseModel):
    """One window's score summary, computed from stored timestamps."""

    from_: datetime = Field(alias="from")
    to: datetime
    scored_transactions: int = Field(ge=0)
    mean_fraud_probability: float | None = None
    high_risk_count: int = Field(ge=0)
    high_risk_percent: float | None = None
    anomaly_scored_transactions: int = Field(ge=0)
    mean_anomaly_score: float | None = None
    critical_anomaly_count: int = Field(ge=0)
    critical_anomaly_percent: float | None = None

    model_config = {"populate_by_name": True}


class ScoreWindowsResponse(BaseModel):
    baseline: WindowSummary | None = None
    current: WindowSummary | None = None
    high_risk_threshold: float | None = None
    critical_anomaly_threshold: float | None = None
    thresholds: dict[str, Any]


class DriftFeature(BaseModel):
    feature: str
    kind: str
    psi: float | None = Field(default=None, description="Population Stability Index.")
    status: str = Field(description="NORMAL, WATCH, DRIFT_DETECTED or INSUFFICIENT_DATA.")
    baseline_count: int = Field(ge=0)
    current_count: int = Field(ge=0)
    baseline_mean: float | None = None
    current_mean: float | None = None


class DriftResponse(BaseModel):
    features: list[DriftFeature]
    baseline_from: datetime | None = None
    baseline_to: datetime | None = None
    current_from: datetime | None = None
    current_to: datetime | None = None
    thresholds: dict[str, Any]
    note: str = Field(description="Drift is distribution change. It is not evidence of fraud.")


# --------------------------------------------------------------------------
# Policy effectiveness
# --------------------------------------------------------------------------
class RulePerformance(BaseModel):
    rule_id: str
    description: str
    primary_action: str
    triggers: int = Field(ge=0)
    approve_count: int = Field(ge=0)
    step_up_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    override_count: int = Field(ge=0)
    override_rate: float | None = Field(
        default=None, description="Withheld below the reporting floor."
    )
    override_rate_reportable: bool
    flagged_high_override: bool


class PolicyEffectivenessResponse(BaseModel):
    rules: list[RulePerformance]
    high_override_threshold: float
    min_rule_triggers: int
    policy_version: str
    override_note: str = Field(
        description="What an override does and does not mean, so a rate near zero is readable."
    )


# --------------------------------------------------------------------------
# High-risk funnel
# --------------------------------------------------------------------------
class FunnelStage(BaseModel):
    stage: str
    count: int = Field(ge=0)
    description: str


class HighRiskFunnelResponse(BaseModel):
    """Why a high model score does not automatically become a BLOCK."""

    stages: list[FunnelStage]
    withheld_pending_investigation: int = Field(ge=0)
    final_actions: dict[str, int]
    block_threshold: float
    min_independent_sources: int
    policy_version: str
    explanation: str


# --------------------------------------------------------------------------
# Recommendations and assistant
# --------------------------------------------------------------------------
class Recommendation(BaseModel):
    """An analytical suggestion. Never an executed action."""

    id: str
    severity: str
    title: str
    detail: str
    metric_source: str
    action_required: str = Field(
        description="What a human might do. Nothing is performed automatically."
    )


class RecommendationsResponse(BaseModel):
    recommendations: list[Recommendation]
    note: str = Field(
        default=(
            "Recommendations are analytical only. No model is retrained, no threshold "
            "moved, no policy edited and no decision revised by this system."
        )
    )


class AssistantQuestion(BaseModel):
    topic: str
    question: str


class AssistantAnswer(BaseModel):
    """A grounded answer, with everything needed to check it."""

    topic: str
    question: str
    answer: str
    metric_sources: list[str] = Field(description="Endpoints the figures came from.")
    time_window: str
    data_availability: str
    sufficient: bool = Field(description="False when the data could not answer the question.")
    figures: dict[str, Any] = Field(default_factory=dict)


class AssistantQuestionsResponse(BaseModel):
    questions: list[AssistantQuestion]
    note: str = Field(
        default=(
            "The assistant answers from structured backend metrics only. No language "
            "model is involved and no figure is generated."
        )
    )
