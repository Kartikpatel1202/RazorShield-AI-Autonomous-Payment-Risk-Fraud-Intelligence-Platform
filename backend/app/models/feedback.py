"""The feedback loop: what analysts concluded, tied to what the system decided.

Two tables live here and they answer different questions.

``ModelFeedback`` (Phase 2) ties a confirmed real-world outcome to the
*prediction* that preceded it - the shape a retraining job would want.

``AnalystFeedback`` (Phase 8) ties a structured analyst judgement to the
*decision* and the *review case* it came from - the shape monitoring wants. It
carries the reason code and the analyst's notes, which retraining has no use for
and an operations dashboard cannot do without.

Neither ever writes to ``risk_decisions``. Feedback is recorded *beside* the
machine decision, and the append-only guard enforces that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, enum_column
from app.models.enums import (
    ActualOutcome,
    AnalystDecisionType,
    FeedbackOutcome,
    FeedbackReason,
)

if TYPE_CHECKING:
    from app.models.risk import RiskPrediction
    from app.models.transaction import Transaction


class ModelFeedback(PkMixin, CreatedAtMixin, Base):
    """Ties a confirmed real-world outcome back to the prediction that preceded it."""

    __tablename__ = "model_feedback"

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_predictions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    actual_outcome: Mapped[ActualOutcome] = mapped_column(
        enum_column(ActualOutcome), index=True, nullable=False
    )
    analyst_decision: Mapped[AnalystDecisionType | None] = mapped_column(
        enum_column(AnalystDecisionType), nullable=True
    )

    transaction: Mapped[Transaction] = relationship(back_populates="model_feedback")
    prediction: Mapped[RiskPrediction | None] = relationship(back_populates="feedback")

    def __repr__(self) -> str:
        return f"<ModelFeedback id={self.id} outcome={self.actual_outcome}>"


class AnalystFeedback(PkMixin, CreatedAtMixin, Base):
    """One analyst's structured conclusion about one decided transaction.

    Deliberately separate from the decision it refers to. A decision is what the
    machine did; feedback is what a person later concluded. Storing the second
    inside the first would mean editing history, which the immutability guard
    forbids and an audit would never accept.
    """

    __tablename__ = "analyst_feedback"
    __table_args__ = (
        Index("ix_analyst_feedback_outcome_created", "outcome", "created_at"),
        Index("ix_analyst_feedback_decision_outcome", "risk_decision_id", "outcome"),
    )

    #: Stable public identifier returned by the API.
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: The machine decision this feedback judges. Nullable because a transaction
    #: can be labelled before it was ever decided.
    risk_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_decisions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    review_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_cases.id", ondelete="SET NULL"), index=True, nullable=True
    )
    analyst_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    outcome: Mapped[FeedbackOutcome] = mapped_column(
        enum_column(FeedbackOutcome), index=True, nullable=False
    )
    reason_code: Mapped[FeedbackReason] = mapped_column(
        enum_column(FeedbackReason, length=48), index=True, nullable=False
    )
    #: The analyst's own words. Stored verbatim and never aggregated - free text
    #: cannot be counted, which is exactly why ``reason_code`` exists.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="analyst_feedback")

    def __repr__(self) -> str:
        return f"<AnalystFeedback public_id={self.public_id!r} outcome={self.outcome}>"
