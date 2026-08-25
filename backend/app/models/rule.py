"""Merchant-configurable risk rules.

Structure only. The rule engine that evaluates ``condition`` and applies
``action``/``risk_adjustment`` belongs to a later phase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import JsonDocument, PkMixin, TimestampMixin, enum_column
from app.models.enums import RuleAction

if TYPE_CHECKING:
    from app.models.merchant import Merchant


class RiskRule(PkMixin, TimestampMixin, Base):
    """A declarative rule owned by a merchant.

    ``condition`` is a structured document rather than an expression string so
    the future engine can evaluate it without parsing or ``eval``.
    """

    __tablename__ = "risk_rules"
    __table_args__ = (
        CheckConstraint(
            "risk_adjustment >= -100 AND risk_adjustment <= 100", name="risk_adjustment_range"
        ),
        Index("ix_risk_rules_merchant_active", "merchant_id", "is_active"),
    )

    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)
    action: Mapped[RuleAction] = mapped_column(enum_column(RuleAction), nullable=False)
    risk_adjustment: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)

    merchant: Mapped[Merchant] = relationship(back_populates="risk_rules")

    def __repr__(self) -> str:
        return f"<RiskRule id={self.id} name={self.name!r}>"
