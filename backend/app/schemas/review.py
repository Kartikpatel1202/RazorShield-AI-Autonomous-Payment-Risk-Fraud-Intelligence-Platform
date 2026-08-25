"""Request and response models for the human review queue.

Note that :class:`ReviewCaseRead` carries the machine decision *and* the human
resolution as separate fields. That is the contract: an analyst's answer is
recorded next to the engine's, never in place of it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    FeedbackOutcome,
    FeedbackReason,
    ReviewCaseStatus,
    ReviewResolution,
)
from app.schemas.common import ORMModel


class ReviewDecisionSummary(BaseModel):
    """The machine decision that put this case in the queue."""

    decision_id: str
    decision: str
    policy_version: str
    matched_rules: list[str]
    reason_codes: list[str]
    requires_human_review: bool
    fraud_probability: float | None = None
    anomaly_score: int | None = None
    investigation_id: str | None = None
    decided_at: datetime


class ReviewCaseRead(ORMModel):
    """One case in the review queue."""

    review_case_id: int
    transaction_id: str
    status: ReviewCaseStatus
    reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolution: ReviewResolution | None = None
    resolution_reason: str | None = None
    assigned_to: int | None = None
    #: Absent only for cases created before a decision was linked.
    decision: ReviewDecisionSummary | None = None


class ResolveReviewRequest(BaseModel):
    """An analyst settling a case.

    ``resolution`` describes the fate of the *transaction*: APPROVED lets the
    payment proceed, REJECTED stops it, ESCALATED passes it to a senior
    reviewer and leaves the case open.
    """

    resolution: ReviewResolution
    analyst_id: int | None = Field(
        default=None, ge=1, description="The analyst recording this resolution, when known."
    )
    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Why the analyst reached this outcome. Stored verbatim.",
    )

    # --- optional structured feedback ---------------------------------------
    # Separate from `resolution` on purpose. The resolution says what was *done*
    # with the payment; the outcome says what the analyst concluded was *true*.
    # A case can be REJECTED while the outcome is INSUFFICIENT_EVIDENCE - the
    # analyst acted cautiously without confirming fraud. Merging the two would
    # turn every cautious block into a fraud label and corrupt every metric
    # computed downstream.
    feedback_outcome: FeedbackOutcome | None = Field(
        default=None, description="What the analyst concluded actually happened."
    )
    feedback_reason: FeedbackReason | None = Field(
        default=None, description="Required when feedback_outcome is given."
    )
    feedback_notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _require_reason_with_outcome(self) -> ResolveReviewRequest:
        if self.feedback_outcome is not None and self.feedback_reason is None:
            raise ValueError("feedback_reason is required when feedback_outcome is provided")
        if self.feedback_reason is not None and self.feedback_outcome is None:
            raise ValueError("feedback_outcome is required when feedback_reason is provided")
        return self


class ResolveReviewResponse(BaseModel):
    """The case after resolution, with the machine decision left intact."""

    review_case_id: int
    transaction_id: str
    status: ReviewCaseStatus
    resolution: ReviewResolution
    resolution_reason: str | None = None
    resolved_at: datetime | None = None
    #: The engine's original outcome, unchanged by this resolution.
    machine_decision: str | None = None
    machine_decision_id: str | None = None
    #: True when the analyst reached a different outcome than the engine.
    overrides_machine_decision: bool
    #: Present when the resolution also recorded structured feedback.
    feedback_id: str | None = None
    feedback_outcome: str | None = None
    feedback_reason: str | None = None
    feedback_notes: str | None = None
