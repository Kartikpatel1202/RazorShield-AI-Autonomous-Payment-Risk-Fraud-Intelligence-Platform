"""Recomputes derived columns from the transactions that were actually written.

Nothing here invents a value. Customer counters, device and IP first/last-seen
timestamps and the ``is_trusted`` flag are all measured from the generated
transaction stream, so the dataset is internally consistent and later phases can
re-derive every one of them from raw rows.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import Customer, CustomerDevice, Device, IpAddress, Transaction
from app.models.enums import RiskLevel, TransactionStatus

logger = logging.getLogger(__name__)

# A device counts as trusted once it has a real, single-owner track record.
TRUSTED_MIN_TRANSACTIONS = 5
TRUSTED_MIN_AGE_DAYS = 14

# Thresholds for the *historical* risk band. This describes what already
# happened to the account - it is not a prediction and never feeds a risk score.
HIGH_RISK_FAILURE_RATIO = 0.30
MEDIUM_RISK_FAILURE_RATIO = 0.15


def _classify_history(failure_ratio: float, chargebacks: int) -> RiskLevel:
    if chargebacks >= 2 or failure_ratio >= HIGH_RISK_FAILURE_RATIO:
        return RiskLevel.HIGH
    if chargebacks == 1 or failure_ratio >= MEDIUM_RISK_FAILURE_RATIO:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def update_customer_counters(session: Session, chargebacks: dict[int, int]) -> None:
    """Fill in each customer's success/failure counts, average and risk band."""
    totals: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"successful": 0, "failed": 0, "other": 0, "amount_sum": Decimal("0")}
    )

    rows = session.execute(
        select(
            Transaction.customer_id,
            Transaction.status,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0),
        ).group_by(Transaction.customer_id, Transaction.status)
    ).all()

    for customer_id, status, count, amount_sum in rows:
        bucket = totals[customer_id]
        if status == TransactionStatus.SUCCESSFUL:
            bucket["successful"] = count
            bucket["amount_sum"] = Decimal(str(amount_sum))
        elif status == TransactionStatus.FAILED:
            bucket["failed"] = count
        else:
            bucket["other"] += count

    payload: list[dict[str, Any]] = []
    for customer_id, bucket in totals.items():
        successful = int(bucket["successful"])
        failed = int(bucket["failed"])
        attempted = successful + failed + int(bucket["other"])
        failure_ratio = failed / attempted if attempted else 0.0
        chargeback_count = chargebacks.get(customer_id, 0)
        average = (
            (bucket["amount_sum"] / successful).quantize(Decimal("0.01"))
            if successful
            else Decimal("0.00")
        )
        payload.append(
            {
                "id": customer_id,
                "successful_transaction_count": successful,
                "failed_transaction_count": failed,
                "chargeback_count": chargeback_count,
                "average_transaction_amount": average,
                "historical_risk_level": _classify_history(failure_ratio, chargeback_count),
            }
        )

    if payload:
        session.execute(update(Customer), payload)
    logger.info("Recomputed counters for %d customers", len(payload))


def update_device_activity(session: Session) -> None:
    """Derive device first/last seen, owner count and the trusted flag."""
    rows = session.execute(
        select(
            Transaction.device_id,
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
            func.count(Transaction.id),
            func.count(func.distinct(Transaction.customer_id)),
        )
        .where(Transaction.device_id.is_not(None))
        .group_by(Transaction.device_id)
    ).all()

    payload = []
    for device_id, first_seen, last_seen, transaction_count, owner_count in rows:
        age_days = (last_seen - first_seen).days
        payload.append(
            {
                "id": device_id,
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "is_trusted": (
                    owner_count == 1
                    and transaction_count >= TRUSTED_MIN_TRANSACTIONS
                    and age_days >= TRUSTED_MIN_AGE_DAYS
                ),
            }
        )

    if payload:
        session.execute(update(Device), payload)
    logger.info("Recomputed activity for %d devices", len(payload))


def update_ip_activity(session: Session) -> None:
    """Derive IP first/last seen from observed transactions."""
    rows = session.execute(
        select(
            Transaction.ip_address_id,
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
        )
        .where(Transaction.ip_address_id.is_not(None))
        .group_by(Transaction.ip_address_id)
    ).all()

    payload = [
        {"id": ip_id, "first_seen_at": first_seen, "last_seen_at": last_seen}
        for ip_id, first_seen, last_seen in rows
    ]
    if payload:
        session.execute(update(IpAddress), payload)
    logger.info("Recomputed activity for %d IP addresses", len(payload))


def prune_unused_devices(session: Session) -> int:
    """Drop device rows that never carried a transaction.

    A registered-but-unused fingerprint would have no observed first/last seen,
    so rather than persist an invented timestamp the row is removed. Returns the
    number of devices deleted.
    """
    used = select(Transaction.device_id).where(Transaction.device_id.is_not(None)).distinct()
    session.execute(delete(CustomerDevice).where(CustomerDevice.device_id.not_in(used)))
    deleted = session.execute(delete(Device).where(Device.id.not_in(used))).rowcount
    session.flush()
    logger.info("Pruned %d devices with no observed transactions", deleted)
    return int(deleted or 0)


def update_customer_device_links(session: Session) -> None:
    """Fill in per-link usage counts and first/last use."""
    observed = {
        (customer_id, device_id): (first_used, last_used, count)
        for customer_id, device_id, first_used, last_used, count in session.execute(
            select(
                Transaction.customer_id,
                Transaction.device_id,
                func.min(Transaction.transaction_timestamp),
                func.max(Transaction.transaction_timestamp),
                func.count(Transaction.id),
            )
            .where(Transaction.device_id.is_not(None))
            .group_by(Transaction.customer_id, Transaction.device_id)
        ).all()
    }

    updated = 0
    for link in session.scalars(select(CustomerDevice)):
        measured = observed.get((link.customer_id, link.device_id))
        if measured is None:
            continue
        link.first_used_at, link.last_used_at, link.transaction_count = measured
        updated += 1

    session.flush()
    logger.info("Recomputed %d customer-device links", updated)


def recompute_all(session: Session, chargebacks: dict[int, int]) -> None:
    """Run every derivation pass, in dependency order."""
    update_customer_counters(session, chargebacks)
    prune_unused_devices(session)
    update_device_activity(session)
    update_ip_activity(session)
    update_customer_device_links(session)
    session.flush()
