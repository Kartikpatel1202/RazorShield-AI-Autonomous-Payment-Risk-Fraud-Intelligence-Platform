"""Transactions - the central entity of the RazorShield data universe.

Deliberately carries **no risk score**. A transaction records what happened;
Phase 3 derives risk from it. ``is_fraud`` is the ground-truth label of the
simulated dataset, used later for model training and evaluation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, UtcDateTime, enum_column
from app.models.enums import PaymentMethod, TransactionStatus

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.customer import Customer
    from app.models.decision import RiskDecision
    from app.models.device import Device
    from app.models.event import RiskEvent
    from app.models.feedback import AnalystFeedback, ModelFeedback
    from app.models.investigation import Investigation
    from app.models.ip_address import IpAddress
    from app.models.merchant import Merchant
    from app.models.review import ReviewCase
    from app.models.risk import RiskPrediction, RiskSignal


class Transaction(PkMixin, CreatedAtMixin, Base):
    """A single payment attempt."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("failed_attempts >= 0", name="failed_attempts_non_negative"),
        # Velocity and history lookups are always scoped to an entity and
        # ordered by time, so the composite indexes carry the timestamp.
        Index("ix_transactions_customer_time", "customer_id", "transaction_timestamp"),
        Index("ix_transactions_device_time", "device_id", "transaction_timestamp"),
        Index("ix_transactions_ip_time", "ip_address_id", "transaction_timestamp"),
        Index("ix_transactions_merchant_time", "merchant_id", "transaction_timestamp"),
        Index("ix_transactions_fraud_time", "is_fraud", "transaction_timestamp"),
        # Leading columns above also serve plain merchant_id / customer_id /
        # device_id / ip_address_id / is_fraud lookups, so no standalone
        # single-column index is defined for them.
    )

    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    ip_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("ip_addresses.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_column(PaymentMethod), index=True, nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        enum_column(TransactionStatus), index=True, nullable=False
    )
    transaction_timestamp: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)

    country: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)

    # Consecutive failed attempts by this customer immediately before this one.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_fraud: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    merchant: Mapped[Merchant] = relationship(back_populates="transactions")
    customer: Mapped[Customer] = relationship(back_populates="transactions")
    device: Mapped[Device | None] = relationship(back_populates="transactions")
    ip_address_record: Mapped[IpAddress | None] = relationship(back_populates="transactions")

    risk_prediction: Mapped[RiskPrediction | None] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False
    )
    risk_signals: Mapped[list[RiskSignal]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    investigation: Mapped[Investigation | None] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False
    )
    #: A list, not a scalar: a transaction may be decided more than once and
    #: every evaluation is retained. Ordered oldest-first.
    risk_decisions: Mapped[list[RiskDecision]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="RiskDecision.decided_at",
    )
    review_case: Mapped[ReviewCase | None] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False
    )
    #: The live pipeline's observations of this transaction, oldest first.
    risk_events: Mapped[list[RiskEvent]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="RiskEvent.sequence",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    model_feedback: Mapped[list[ModelFeedback]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    analyst_feedback: Mapped[list[AnalystFeedback]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="AnalystFeedback.created_at",
    )

    def __repr__(self) -> str:
        return f"<Transaction id={self.id} transaction_id={self.transaction_id!r}>"
