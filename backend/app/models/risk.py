"""Risk prediction and signal tables.

Structure only. Phase 2 writes no rows here: predictions are produced by the ML
engine in Phase 3 and signals by the feature/rule layers after that. Nothing in
this phase may populate a fraud probability or a risk score.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, enum_column
from app.models.enums import SignalSeverity

if TYPE_CHECKING:
    from app.models.feedback import ModelFeedback
    from app.models.transaction import Transaction


class RiskPrediction(PkMixin, CreatedAtMixin, Base):
    """Model output for one transaction, written by the Phase 3 risk engine."""

    __tablename__ = "risk_predictions"
    __table_args__ = (
        CheckConstraint(
            "fraud_probability >= 0 AND fraud_probability <= 1", name="fraud_probability_range"
        ),
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="risk_score_range"),
    )

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    fraud_probability: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates="risk_prediction")
    feedback: Mapped[list[ModelFeedback]] = relationship(back_populates="prediction")

    def __repr__(self) -> str:
        return f"<RiskPrediction id={self.id} transaction_id={self.transaction_id}>"


class RiskSignal(PkMixin, CreatedAtMixin, Base):
    """One named, numeric piece of evidence about a transaction.

    Values are numeric so signals can be compared and aggregated: counts
    (``velocity_1h``), ratios (``amount_vs_customer_average``) and booleans
    encoded as 0/1 (``is_new_device``).
    """

    __tablename__ = "risk_signals"
    __table_args__ = (Index("ix_risk_signals_transaction_name", "transaction_id", "signal_name"),)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    signal_name: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    signal_value: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)
    severity: Mapped[SignalSeverity] = mapped_column(
        enum_column(SignalSeverity), index=True, nullable=False
    )
    # Which subsystem emitted the signal, e.g. "rules", "anomaly", "model".
    source: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    transaction: Mapped[Transaction] = relationship(back_populates="risk_signals")

    def __repr__(self) -> str:
        return f"<RiskSignal id={self.id} name={self.signal_name!r}>"
