"""Append-only audit trail.

Every automated or human action taken on a transaction lands here. Later phases
render these rows as the investigation timeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, JsonDocument, PkMixin, enum_column
from app.models.enums import ActorType

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class AuditLog(PkMixin, CreatedAtMixin, Base):
    """One immutable audit event."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_transaction_created", "transaction_id", "created_at"),
        Index("ix_audit_logs_event_created", "event_type", "created_at"),
    )

    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        enum_column(ActorType), index=True, nullable=False
    )
    # Free-form actor reference: a user id, an agent name or a service name.
    actor_id: Mapped[str | None] = mapped_column(String(96), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    event_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, nullable=True)

    transaction: Mapped[Transaction | None] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} event_type={self.event_type!r}>"
