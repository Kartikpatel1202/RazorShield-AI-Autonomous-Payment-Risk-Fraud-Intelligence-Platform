"""The transaction explorer: one joined, filtered, sorted, paginated query.

The explorer table shows columns from five tables at once - the transaction, its
merchant and customer, the Phase 3 prediction, the Phase 4 anomaly signal and
the Phase 6 decision. Fetching those per row would be a six-fold N+1 over a page
of fifty; this module builds a single SELECT with joins and lets the database do
the work.

Filtering and sorting are server-side by construction. Sort keys come from a
closed allow-list mapped to real columns, so a sort parameter can never become
arbitrary SQL.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Customer, Merchant, RiskPrediction, RiskSignal, Transaction
from app.models.enums import DecisionAction, TransactionStatus
from app.schemas.common import Page, PageMeta
from app.services.analytics import latest_decisions
from app.services.anomaly import ANOMALY_SIGNAL

logger = logging.getLogger(__name__)

#: Sortable columns, by the name the API accepts. A closed map: an unknown sort
#: key is rejected by FastAPI's enum validation and can never reach SQL.
SORT_COLUMNS = (
    "timestamp",
    "amount",
    "fraud_probability",
    "anomaly_score",
    "transaction_id",
)

#: Anomaly severities the filter accepts, matching Phase 4's bands.
ANOMALY_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _base_statement(session: Session) -> tuple[Select[Any], Any, Any]:
    """The joined explorer query, before filters.

    Both risk joins are LEFT joins: a transaction without a prediction, an
    anomaly signal or a decision must still appear in the explorer, showing the
    gap rather than vanishing from the table.
    """
    latest = latest_decisions(session)
    anomaly = (
        select(
            RiskSignal.transaction_id.label("transaction_id"),
            RiskSignal.signal_value.label("anomaly_score"),
            RiskSignal.severity.label("anomaly_severity"),
            RiskSignal.source.label("anomaly_model_version"),
        )
        .where(RiskSignal.signal_name == ANOMALY_SIGNAL)
        .subquery()
    )

    statement = (
        select(
            Transaction.transaction_id,
            Transaction.transaction_timestamp,
            Transaction.amount,
            Transaction.currency,
            Transaction.status,
            Transaction.is_fraud,
            Customer.external_customer_id.label("customer_id"),
            Merchant.external_merchant_id.label("merchant_id"),
            Merchant.name.label("merchant_name"),
            RiskPrediction.fraud_probability,
            RiskPrediction.risk_score,
            anomaly.c.anomaly_score,
            anomaly.c.anomaly_severity,
            latest.c.action.label("decision"),
            latest.c.policy_version,
            latest.c.requires_human_review,
        )
        .select_from(Transaction)
        .join(Customer, Customer.id == Transaction.customer_id)
        .join(Merchant, Merchant.id == Transaction.merchant_id)
        .join(RiskPrediction, RiskPrediction.transaction_id == Transaction.id, isouter=True)
        .join(anomaly, anomaly.c.transaction_id == Transaction.id, isouter=True)
        .join(latest, latest.c.transaction_id == Transaction.id, isouter=True)
    )
    return statement, anomaly, latest


def build_statement(
    session: Session,
    *,
    search: str | None = None,
    decision: DecisionAction | None = None,
    risk_level: str | None = None,
    anomaly_severity: str | None = None,
    merchant_id: str | None = None,
    customer_id: str | None = None,
    status: TransactionStatus | None = None,
    is_fraud: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_probability: float | None = None,
    max_probability: float | None = None,
    sort_by: str = "timestamp",
    descending: bool = True,
    thresholds: Any = None,
) -> Select[Any]:
    """Apply every filter server-side and return the ready-to-paginate query."""
    statement, anomaly, latest = _base_statement(session)

    if search:
        # Anchored to the business key. Bound parameter, never interpolated.
        statement = statement.where(Transaction.transaction_id.ilike(f"%{search}%"))
    if decision is not None:
        statement = statement.where(latest.c.action == decision)
    if anomaly_severity is not None:
        statement = statement.where(anomaly.c.anomaly_severity == anomaly_severity.lower())
    if merchant_id is not None:
        statement = statement.where(Merchant.external_merchant_id == merchant_id)
    if customer_id is not None:
        statement = statement.where(Customer.external_customer_id == customer_id)
    if status is not None:
        statement = statement.where(Transaction.status == status)
    if is_fraud is not None:
        statement = statement.where(Transaction.is_fraud.is_(is_fraud))
    if date_from is not None:
        statement = statement.where(Transaction.transaction_timestamp >= date_from)
    if date_to is not None:
        statement = statement.where(Transaction.transaction_timestamp <= date_to)
    if min_probability is not None:
        statement = statement.where(
            RiskPrediction.fraud_probability >= Decimal(str(min_probability))
        )
    if max_probability is not None:
        statement = statement.where(
            RiskPrediction.fraud_probability <= Decimal(str(max_probability))
        )
    if risk_level is not None and thresholds is not None:
        statement = _apply_risk_level(statement, risk_level, thresholds)

    return statement.order_by(*_ordering(sort_by, descending, anomaly, latest))


def _apply_risk_level(statement: Select[Any], level: str, thresholds: Any) -> Select[Any]:
    """Band by the policy's own supervised thresholds.

    Reading the bounds from the active policy keeps the explorer's idea of
    "HIGH" identical to the decision engine's.
    """
    block = Decimal(str(thresholds.fraud_block))
    high = Decimal(str(thresholds.fraud_high))
    medium = Decimal(str(thresholds.fraud_medium))
    column = RiskPrediction.fraud_probability

    if level == "CRITICAL":
        return statement.where(column >= block)
    if level == "HIGH":
        return statement.where(column >= high, column < block)
    if level == "MEDIUM":
        return statement.where(column >= medium, column < high)
    return statement.where(column < medium)


def _ordering(sort_by: str, descending: bool, anomaly: Any, latest: Any) -> tuple[Any, ...]:
    """Map an allow-listed sort key onto real columns.

    The transaction id is always appended as a tiebreak so paging is stable when
    the primary key ties - without it, two pages can show the same row.
    """
    columns = {
        "timestamp": Transaction.transaction_timestamp,
        "amount": Transaction.amount,
        "fraud_probability": RiskPrediction.fraud_probability,
        "anomaly_score": anomaly.c.anomaly_score,
        "transaction_id": Transaction.transaction_id,
    }
    column = columns.get(sort_by, Transaction.transaction_timestamp)
    primary = column.desc().nullslast() if descending else column.asc().nullsfirst()
    return (primary, Transaction.id.desc())


def explore(
    session: Session, page: int, page_size: int, *, thresholds: Any, **filters: Any
) -> Page[dict[str, Any]]:
    """One page of the explorer, as plain dictionaries ready for serialisation.

    ``app.services.pagination.paginate`` returns ORM entities; the explorer
    selects individual columns, so it needs the same COUNT-plus-LIMIT shape over
    rows instead. The count reuses the filtered statement, so the total always
    matches the filters actually applied.
    """
    statement = build_statement(session, thresholds=thresholds, **filters)

    total = int(
        session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    )
    rows = (
        session.execute(statement.offset((page - 1) * page_size).limit(page_size)).mappings().all()
    )
    total_pages = ceil(total / page_size) if total else 0

    return Page[dict[str, Any]](
        items=[_row_to_item(row, thresholds) for row in rows],
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        ),
    )


def risk_level_for(probability: float | None, thresholds: Any) -> str | None:
    """The policy band a probability falls in.

    Derived from the active policy's own thresholds rather than a second table
    of cut-offs, so the explorer and the decision engine cannot disagree about
    what "HIGH" means. Shared with the transaction detail endpoint.
    """
    if probability is None:
        return None
    if probability >= thresholds.fraud_block:
        return "CRITICAL"
    if probability >= thresholds.fraud_high:
        return "HIGH"
    if probability >= thresholds.fraud_medium:
        return "MEDIUM"
    return "LOW"


def _row_to_item(row: Any, thresholds: Any) -> dict[str, Any]:
    probability = float(row["fraud_probability"]) if row["fraud_probability"] is not None else None
    return {
        "transaction_id": row["transaction_id"],
        "timestamp": row["transaction_timestamp"],
        "amount": float(row["amount"]),
        "currency": row["currency"],
        "status": str(row["status"]),
        "is_fraud": bool(row["is_fraud"]),
        "customer_id": row["customer_id"],
        "merchant_id": row["merchant_id"],
        "merchant_name": row["merchant_name"],
        "fraud_probability": probability,
        "risk_score": row["risk_score"],
        "risk_level": risk_level_for(probability, thresholds),
        "anomaly_score": int(row["anomaly_score"]) if row["anomaly_score"] is not None else None,
        "anomaly_severity": (
            str(row["anomaly_severity"]).upper() if row["anomaly_severity"] is not None else None
        ),
        "decision": str(row["decision"]).upper() if row["decision"] is not None else None,
        "policy_version": row["policy_version"],
        "requires_human_review": row["requires_human_review"],
    }
