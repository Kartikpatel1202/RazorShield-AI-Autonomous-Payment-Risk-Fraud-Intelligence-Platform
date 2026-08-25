"""The live risk event stream.

One row per stage a transaction passes through, in the order it passed through
them. The table exists for a reason the in-memory bus cannot serve: a browser
that loses its connection needs to catch up, and a stream that only lives in
process memory has nothing to replay from.

Append-only in practice - nothing updates an event once written - though the
Phase 6 immutability guard is deliberately *not* applied here. That guard
protects decisions, which are the record of what the system did. These are
observations of it happening, and conflating the two would dilute what the
guard means.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, JsonDocument, PkMixin, UtcDateTime, enum_column
from app.models.enums import RiskEventType

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class RiskEvent(PkMixin, CreatedAtMixin, Base):
    """One observable step in a transaction's journey through the pipeline."""

    __tablename__ = "risk_events"
    __table_args__ = (
        # The stream is read newest-first globally, and oldest-first per
        # transaction. Both orderings are indexed.
        Index("ix_risk_events_sequence", "sequence"),
        Index("ix_risk_events_transaction_sequence", "transaction_id", "sequence"),
    )

    #: Stable public identifier, and the SSE ``id:`` field. A reconnecting
    #: client sends it back as ``Last-Event-ID`` to resume without gaps.
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)

    #: Monotonic across the whole stream, assigned by the database sequence.
    #: Ordering by ``created_at`` would be ambiguous - several events for one
    #: transaction can share a millisecond - so the stream orders by this.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: The business reference, copied in so an event survives its transaction
    #: being deleted and can be rendered without a join.
    transaction_reference: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    event_type: Mapped[RiskEventType] = mapped_column(
        enum_column(RiskEventType, length=48), index=True, nullable=False
    )
    #: Position within this transaction's own sequence, starting at 1. Lets a
    #: consumer detect a missing step without consulting the global ordering.
    transaction_sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)

    #: A summary, never a dump. Whatever goes here is served to any connected
    #: browser, so it carries identifiers and measured values only - no prompts,
    #: no model text, no credentials, no internal paths.
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False)

    transaction: Mapped[Transaction | None] = relationship(back_populates="risk_events")

    def __repr__(self) -> str:
        return f"<RiskEvent seq={self.sequence} type={self.event_type}>"
