"""Reading the audit trail.

The audit log answers one question: *why did RazorShield do what it did?* These
queries expose it without letting a caller ask for the whole table - every list
is paginated and every filter is a bound parameter.

Read-only. Nothing here writes an audit entry; the services that cause events
write their own, which is what keeps the trail trustworthy.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Transaction
from app.models.enums import ActorType

logger = logging.getLogger(__name__)

#: Event types the trail currently contains, newest phase first. Exposed so the
#: UI can offer a filter without hardcoding a list that would silently rot.
KNOWN_EVENT_TYPES = (
    "risk.decision",
    "review.case_opened",
    "review.resolved",
    "investigation.completed",
)


def statement(
    *,
    event_type: str | None = None,
    actor_type: ActorType | None = None,
    transaction_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> Select[Any]:
    """The filtered audit query, newest first.

    Joined to ``transactions`` so each entry carries its transaction reference -
    the alternative is a lookup per row, which is the N+1 this avoids.
    """
    query = (
        select(
            AuditLog.id,
            AuditLog.event_type,
            AuditLog.actor_type,
            AuditLog.actor_id,
            AuditLog.event_data,
            AuditLog.created_at,
            Transaction.transaction_id,
        )
        .select_from(AuditLog)
        .join(Transaction, Transaction.id == AuditLog.transaction_id, isouter=True)
    )

    if event_type is not None:
        query = query.where(AuditLog.event_type == event_type)
    if actor_type is not None:
        query = query.where(AuditLog.actor_type == actor_type)
    if transaction_id is not None:
        query = query.where(Transaction.transaction_id == transaction_id)
    if created_after is not None:
        query = query.where(AuditLog.created_at >= created_after)
    if created_before is not None:
        query = query.where(AuditLog.created_at <= created_before)

    return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


def page(
    session: Session,
    query: Select[Any],
    page_number: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of audit entries plus the total matching count."""
    total = int(
        session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    )
    rows = (
        session.execute(query.offset((page_number - 1) * page_size).limit(page_size))
        .mappings()
        .all()
    )
    return [_to_entry(row) for row in rows], total


def _to_entry(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = row["event_data"] or {}
    return {
        "audit_id": row["id"],
        "event_type": row["event_type"],
        "actor_type": str(row["actor_type"]),
        "actor_id": row["actor_id"],
        "transaction_id": row["transaction_id"],
        "created_at": row["created_at"],
        # Surfaced as first-class fields so the table is scannable without
        # expanding every row; the full document stays available below.
        "decision": data.get("decision"),
        "decision_id": data.get("decision_id"),
        "policy_version": data.get("policy_version"),
        "investigation_id": data.get("investigation_id"),
        "resolution": data.get("resolution"),
        "event_data": data,
    }


def event_type_counts(session: Session) -> dict[str, int]:
    """How many entries of each type exist. One grouped query."""
    rows = session.execute(
        select(AuditLog.event_type, func.count()).group_by(AuditLog.event_type)
    ).all()
    return {str(event_type): count for event_type, count in rows}
