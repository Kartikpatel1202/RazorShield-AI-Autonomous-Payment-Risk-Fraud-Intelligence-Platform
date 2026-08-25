"""Point-in-time history built with SQL, for scoring a single transaction.

The streaming accumulator is the fast path for building a training set. This is
the path a live prediction takes: it asks the database directly for what had
happened before one specific transaction.

Both paths must agree exactly, or a model would be served features unlike those
it learned from. ``tests/test_provider_parity.py`` asserts they do.

**The boundary.** "Before" is the strict total order ``(timestamp, id)``:

    timestamp < T  OR  (timestamp = T AND id < T_id)

which excludes the transaction itself and is unambiguous when two payments
share a timestamp.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.models.enums import TransactionStatus
from ml.features.history import (
    CUSTOMER_WINDOWS,
    ENTITY_WINDOWS,
    CustomerHistory,
    EntityHistory,
    HistoryWindow,
    TransactionView,
)
from ml.features.loader import (
    get_customer_profile,
    get_device_profile,
    get_ip_profile,
)


class UnknownCustomerError(LookupError):
    """A transaction points at a customer that does not exist."""


def before_predicate(transaction: TransactionView) -> Any:
    """SQL predicate selecting rows strictly earlier than ``transaction``.

    Public so other layers (the Phase 5 investigation tools) enforce the same
    boundary rather than reimplementing it.
    """
    return or_(
        Transaction.transaction_timestamp < transaction.timestamp,
        and_(
            Transaction.transaction_timestamp == transaction.timestamp,
            Transaction.id < transaction.id,
        ),
    )


def _window_start(transaction: TransactionView, window: timedelta) -> datetime:
    return transaction.timestamp - window


def _count_within(transaction: TransactionView, window: timedelta) -> Any:
    """1 when a row falls inside the window ending at the transaction, else 0."""
    return case(
        (Transaction.transaction_timestamp >= _window_start(transaction, window), 1), else_=0
    )


def _failed_within(transaction: TransactionView, window: timedelta) -> Any:
    return case(
        (
            and_(
                Transaction.transaction_timestamp >= _window_start(transaction, window),
                Transaction.status == TransactionStatus.FAILED,
            ),
            1,
        ),
        else_=0,
    )


def _amount_within(transaction: TransactionView, window: timedelta) -> Any:
    return case(
        (
            Transaction.transaction_timestamp >= _window_start(transaction, window),
            Transaction.amount,
        ),
        else_=0,
    )


def _window_columns(
    transaction: TransactionView, windows: Mapping[str, timedelta], *, with_amounts: bool
) -> tuple[list[Any], list[str]]:
    """Conditional aggregates for every window, computed in a single pass."""
    columns: list[Any] = []
    labels: list[str] = []

    for name, window in windows.items():
        columns.append(func.sum(_count_within(transaction, window)))
        labels.append(f"count_{name}")
    for name, window in windows.items():
        columns.append(func.sum(_failed_within(transaction, window)))
        labels.append(f"failed_{name}")
    if with_amounts:
        for name, window in windows.items():
            columns.append(func.sum(_amount_within(transaction, window)))
            labels.append(f"amount_{name}")

    return columns, labels


def _scalar(value: Any, default: float = 0.0) -> float:
    return default if value is None else float(value)


def customer_history(session: Session, transaction: TransactionView) -> CustomerHistory:
    """Everything the customer had done before this transaction, in three queries."""
    window_columns, window_labels = _window_columns(
        transaction, CUSTOMER_WINDOWS, with_amounts=True
    )

    aggregates = session.execute(
        select(
            func.count(Transaction.id),
            func.sum(case((Transaction.status == TransactionStatus.SUCCESSFUL, 1), else_=0)),
            func.sum(case((Transaction.status == TransactionStatus.FAILED, 1), else_=0)),
            func.sum(Transaction.amount),
            func.sum(Transaction.amount * Transaction.amount),
            func.max(Transaction.amount),
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
            *window_columns,
        ).where(Transaction.customer_id == transaction.customer_id, before_predicate(transaction))
    ).one()

    head, tail = aggregates[:8], aggregates[8:]
    windows = dict(zip(window_labels, tail, strict=True))

    locations = session.execute(
        select(Transaction.country, Transaction.city, func.count(Transaction.id))
        .where(Transaction.customer_id == transaction.customer_id, before_predicate(transaction))
        .group_by(Transaction.country, Transaction.city)
    ).all()

    country_counts: dict[str, int] = {}
    city_counts: dict[str, int] = {}
    for country, city, count in locations:
        country_counts[country] = country_counts.get(country, 0) + int(count)
        city_counts[city] = city_counts.get(city, 0) + int(count)

    latest = session.execute(
        select(Transaction.country, Transaction.city)
        .where(Transaction.customer_id == transaction.customer_id, before_predicate(transaction))
        .order_by(Transaction.transaction_timestamp.desc(), Transaction.id.desc())
        .limit(1)
    ).first()

    return CustomerHistory(
        transaction_count=int(head[0] or 0),
        success_count=int(head[1] or 0),
        failure_count=int(head[2] or 0),
        amount_sum=_scalar(head[3]),
        amount_square_sum=_scalar(head[4]),
        amount_max=_scalar(head[5]),
        first_transaction_at=head[6],
        last_transaction_at=head[7],
        counts={name: int(windows[f"count_{name}"] or 0) for name in CUSTOMER_WINDOWS},
        failed_counts={name: int(windows[f"failed_{name}"] or 0) for name in CUSTOMER_WINDOWS},
        amounts={name: _scalar(windows[f"amount_{name}"]) for name in CUSTOMER_WINDOWS},
        country_counts=country_counts,
        city_counts=city_counts,
        last_country=latest[0] if latest else None,
        last_city=latest[1] if latest else None,
    )


def _entity_history(
    session: Session, transaction: TransactionView, entity_filter: Any
) -> EntityHistory:
    """Prior activity of one device or IP address, in a single query."""
    window_columns, window_labels = _window_columns(transaction, ENTITY_WINDOWS, with_amounts=False)

    row = session.execute(
        select(
            func.count(Transaction.id),
            func.count(func.distinct(Transaction.customer_id)),
            func.min(Transaction.transaction_timestamp),
            func.sum(case((Transaction.customer_id == transaction.customer_id, 1), else_=0)),
            *window_columns,
        ).where(entity_filter, before_predicate(transaction))
    ).one()

    head, tail = row[:4], row[4:]
    windows = dict(zip(window_labels, tail, strict=True))

    return EntityHistory(
        transaction_count=int(head[0] or 0),
        distinct_customers=int(head[1] or 0),
        first_seen_at=head[2],
        counts={name: int(windows[f"count_{name}"] or 0) for name in ENTITY_WINDOWS},
        failed_counts={name: int(windows[f"failed_{name}"] or 0) for name in ENTITY_WINDOWS},
        customer_used_before=int(head[3] or 0) > 0,
    )


def device_history(session: Session, transaction: TransactionView) -> EntityHistory | None:
    if transaction.device_id is None:
        return None
    return _entity_history(session, transaction, Transaction.device_id == transaction.device_id)


def ip_history(session: Session, transaction: TransactionView) -> EntityHistory | None:
    if transaction.ip_address_id is None:
        return None
    return _entity_history(
        session, transaction, Transaction.ip_address_id == transaction.ip_address_id
    )


def build_history_window(session: Session, transaction: TransactionView) -> HistoryWindow:
    """Assemble the full point-in-time context for one transaction from SQL."""
    profile = get_customer_profile(session, transaction.customer_id)
    if profile is None:
        raise UnknownCustomerError(
            f"transaction {transaction.transaction_id} references a missing customer"
        )

    return HistoryWindow(
        customer_profile=profile,
        customer=customer_history(session, transaction),
        device_profile=get_device_profile(session, transaction.device_id),
        device=device_history(session, transaction),
        ip_profile=get_ip_profile(session, transaction.ip_address_id),
        ip=ip_history(session, transaction),
    )


__all__ = [
    "UnknownCustomerError",
    "before_predicate",
    "build_history_window",
    "customer_history",
    "device_history",
    "ip_history",
]
