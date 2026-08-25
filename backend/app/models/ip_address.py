"""Observed IP addresses.

``reputation_score`` is a simulated value produced by the seed generator. No
external IP-reputation service is called anywhere in this project.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, UtcDateTime

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class IpAddress(PkMixin, CreatedAtMixin, Base):
    """An IP address seen originating payments."""

    __tablename__ = "ip_addresses"
    __table_args__ = (
        CheckConstraint(
            "reputation_score >= 0 AND reputation_score <= 100",
            name="reputation_score_range",
        ),
    )

    ip_address: Mapped[str] = mapped_column(String(45), unique=True, index=True, nullable=False)
    country: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)
    # 0 = worst observed reputation, 100 = best. Simulated for the dataset.
    reputation_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("80.00"), index=True, nullable=False
    )
    is_proxy: Mapped[bool] = mapped_column(default=False, nullable=False)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="ip_address_record")

    def __repr__(self) -> str:
        return f"<IpAddress id={self.id} ip={self.ip_address!r}>"
