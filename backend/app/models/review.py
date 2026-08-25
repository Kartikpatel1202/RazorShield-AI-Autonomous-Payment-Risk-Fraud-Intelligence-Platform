"""Human-in-the-loop review queue and analyst decisions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, UtcDateTime, enum_column
from app.models.enums import AnalystDecisionType, ReviewCaseStatus, ReviewResolution

if TYPE_CHECKING:
    from app.models.decision import RiskDecision
    from app.models.transaction import Transaction
    from app.models.user import User


class ReviewCase(PkMixin, CreatedAtMixin, Base):
    """A transaction routed to a human analyst."""

    __tablename__ = "review_cases"
    __table_args__ = (Index("ix_review_cases_status_created", "status", "created_at"),)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    status: Mapped[ReviewCaseStatus] = mapped_column(
        enum_column(ReviewCaseStatus), default=ReviewCaseStatus.OPEN, index=True, nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: The machine decision that opened this case. The decision row itself is
    #: immutable; resolving the case never writes back to it.
    risk_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_decisions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    #: How an analyst settled the case. Recorded *alongside* the machine
    #: decision, never in place of it, so the pair "engine said X, human said Y"
    #: stays visible - which is the only way disagreement can be measured.
    resolution: Mapped[ReviewResolution | None] = mapped_column(
        enum_column(ReviewResolution), index=True, nullable=True
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="review_case")
    risk_decision: Mapped[RiskDecision | None] = relationship(back_populates="review_cases")
    assignee: Mapped[User | None] = relationship(
        back_populates="assigned_cases", foreign_keys=[assigned_to]
    )
    decisions: Mapped[list[AnalystDecision]] = relationship(
        back_populates="review_case", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ReviewCase id={self.id} status={self.status}>"


class AnalystDecision(PkMixin, CreatedAtMixin, Base):
    """The outcome an analyst recorded against a review case."""

    __tablename__ = "analyst_decisions"

    review_case_id: Mapped[int] = mapped_column(
        ForeignKey("review_cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    analyst_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    decision: Mapped[AnalystDecisionType] = mapped_column(
        enum_column(AnalystDecisionType), index=True, nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    review_case: Mapped[ReviewCase] = relationship(back_populates="decisions")
    analyst: Mapped[User | None] = relationship(back_populates="decisions")

    def __repr__(self) -> str:
        return f"<AnalystDecision id={self.id} decision={self.decision}>"
