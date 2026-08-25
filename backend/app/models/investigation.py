"""AI investigation records.

Structure only. The investigating agent arrives in a later phase; nothing in
Phase 2 writes a summary, confidence or recommended action.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, JsonDocument, PkMixin, UtcDateTime, enum_column
from app.models.enums import InvestigationStatus, RecommendedAction

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class Investigation(PkMixin, CreatedAtMixin, Base):
    """One agent investigation of a transaction."""

    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    #: Stable public identifier returned by the API. The surrogate primary key
    #: stays internal so investigation references can be shared safely.
    public_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    status: Mapped[InvestigationStatus] = mapped_column(
        enum_column(InvestigationStatus),
        default=InvestigationStatus.PENDING,
        index=True,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    recommended_action: Mapped[RecommendedAction | None] = mapped_column(
        enum_column(RecommendedAction), nullable=True
    )
    #: How many tool-selection rounds the agent used.
    iteration_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: True when a deterministic test double produced this investigation, so a
    #: mock-backed result can never be mistaken for a real one - in SQL as well
    #: as in the API response.
    agent_is_mock: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    #: The structured investigation: findings, evidence, tool trace, confidence
    #: basis and model versions. Contains no prompts, no model text and no
    #: credentials.
    report: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="investigation")

    def __repr__(self) -> str:
        return f"<Investigation id={self.id} status={self.status}>"
