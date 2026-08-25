"""Customers: the payers transacting with a merchant."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import PkMixin, TimestampMixin, UtcDateTime, enum_column
from app.models.enums import RiskLevel

if TYPE_CHECKING:
    from app.models.device import CustomerDevice, Device
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction


class Customer(PkMixin, TimestampMixin, Base):
    """A merchant's customer, with rolling behavioural counters.

    The counters are historical facts derived from past transactions. They are
    inputs to later risk modelling, never a risk score themselves.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "external_customer_id", name="uq_customers_merchant_external_id"
        ),
        Index("ix_customers_merchant_risk", "merchant_id", "historical_risk_level"),
    )

    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    external_customer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)

    average_transaction_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00"), nullable=False
    )
    successful_transaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_transaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chargeback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    historical_risk_level: Mapped[RiskLevel] = mapped_column(
        enum_column(RiskLevel), default=RiskLevel.LOW, nullable=False
    )

    merchant: Mapped[Merchant] = relationship(back_populates="customers")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    device_links: Mapped[list[CustomerDevice]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    devices: Mapped[list[Device]] = relationship(
        secondary="customer_devices", back_populates="customers", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} external_id={self.external_customer_id!r}>"
