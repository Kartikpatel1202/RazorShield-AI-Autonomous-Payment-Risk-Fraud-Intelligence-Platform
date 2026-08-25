"""The immutable record of what the policy engine decided.

Every row here is written once and never changed. A decision is the thing an
audit asks about months later - "what did the system do, on what inputs, under
which policy?" - and a row that can be edited cannot answer that question. If
the policy changes or the signals are recomputed, the correct action is a *new*
row, not an amended one.

Immutability is enforced, not merely documented: see
``app.db.immutability``, which rejects any UPDATE or DELETE of a
``RiskDecision`` at flush time on every database backend.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, JsonDocument, PkMixin, UtcDateTime, enum_column
from app.models.enums import DecisionAction

if TYPE_CHECKING:
    from app.models.review import ReviewCase
    from app.models.transaction import Transaction


class RiskDecision(PkMixin, CreatedAtMixin, Base):
    """One policy evaluation of one transaction, frozen at the moment it ran.

    Note the absence of a unique constraint on ``transaction_id``: a transaction
    may be decided more than once - after a model refresh, a policy change, or a
    replay - and each evaluation keeps its own row. ``decided_at`` plus the
    ascending primary key give the history a total order.
    """

    __tablename__ = "risk_decisions"
    __table_args__ = (
        CheckConstraint(
            "fraud_probability IS NULL OR (fraud_probability >= 0 AND fraud_probability <= 1)",
            name="decision_fraud_probability_range",
        ),
        CheckConstraint(
            "anomaly_score IS NULL OR (anomaly_score >= 0 AND anomaly_score <= 100)",
            name="decision_anomaly_score_range",
        ),
        Index("ix_risk_decisions_transaction_decided", "transaction_id", "decided_at"),
        Index("ix_risk_decisions_policy_action", "policy_version", "action"),
    )

    #: Stable public identifier returned by the API, so the surrogate key stays
    #: internal.
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action: Mapped[DecisionAction] = mapped_column(
        enum_column(DecisionAction), index=True, nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)

    #: Rules that fired, and the subset whose action became the outcome.
    matched_rules: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    #: The deterministic explanation, assembled from measured values. Never
    #: model-generated.
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False
    )

    # --- the inputs, copied in so the decision stays interpretable even if the
    # --- source rows are later recomputed -----------------------------------
    fraud_probability: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fraud_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anomaly_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anomaly_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    anomaly_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    investigation_public_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    investigation_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    #: How long the pure policy evaluation took, in milliseconds. Observability
    #: only - it records how the decision was reached, never what was decided,
    #: and no rule reads it. Present so the operations dashboard can report a
    #: measured latency instead of an absent one.
    evaluation_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: SHA-256 over the signals and the policy snapshot. Two decisions with the
    #: same digest saw identical inputs, which is what makes "reproducible" a
    #: checkable claim rather than an assertion.
    input_digest: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    #: Full per-rule detail: each rule's action, reason codes and the conditions
    #: with their measured values, plus the policy snapshot in force.
    detail: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates="risk_decisions")
    review_cases: Mapped[list[ReviewCase]] = relationship(back_populates="risk_decision")

    def __repr__(self) -> str:
        return f"<RiskDecision public_id={self.public_id!r} action={self.action}>"
