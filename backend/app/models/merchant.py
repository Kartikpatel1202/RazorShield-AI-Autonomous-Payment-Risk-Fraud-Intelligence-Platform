"""Merchants: the businesses whose payments RazorShield protects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.rule import RiskRule
    from app.models.transaction import Transaction


class Merchant(PkMixin, CreatedAtMixin, Base):
    """A merchant account. Owns customers, transactions and risk rules."""

    __tablename__ = "merchants"

    external_merchant_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    customers: Mapped[list[Customer]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )
    transactions: Mapped[list[Transaction]] = relationship(back_populates="merchant")
    risk_rules: Mapped[list[RiskRule]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Merchant id={self.id} name={self.name!r}>"
