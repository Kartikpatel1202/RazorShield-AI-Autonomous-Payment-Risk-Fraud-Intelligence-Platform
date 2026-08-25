"""Assembles the investigation context for a single transaction.

Purely descriptive: counts, lookups and comparisons against what the database
already records. Turning this evidence into a risk verdict is the job of later
phases, and nothing in this module attempts it.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, Device, IpAddress, Merchant, Transaction
from app.models.enums import TransactionStatus
from app.schemas.context import (
    DeviceUsage,
    IpUsage,
    LocationContext,
    TransactionContext,
    VelocityWindows,
)
from app.schemas.entities import (
    CustomerRead,
    DeviceRead,
    IpAddressRead,
    MerchantRead,
    TransactionRead,
)
from ml.features.loader import to_view
from ml.features.point_in_time import before_predicate

# Windows the later feature engineering and agent tooling will need.
VELOCITY_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("last_5_minutes", timedelta(minutes=5)),
    ("last_1_hour", timedelta(hours=1)),
    ("last_24_hours", timedelta(hours=24)),
    ("last_7_days", timedelta(days=7)),
)

RECENT_TRANSACTION_LIMIT = 10


def _customer_velocity(session: Session, transaction: Transaction) -> VelocityWindows:
    """Count the customer's preceding transactions over several time windows.

    Windows are anchored on the transaction's own timestamp, not on wall-clock
    now, so the answer is stable however long after the event it is asked.
    """
    anchor = transaction.transaction_timestamp
    # One boundary rule for the whole codebase: strictly earlier in the total
    # order (timestamp, id). A plain `id != current` would let a transaction
    # sharing this timestamp but recorded afterwards count as history.
    earlier = before_predicate(to_view(transaction))
    counts: dict[str, int] = {}

    for name, window in VELOCITY_WINDOWS:
        counts[name] = int(
            session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.customer_id == transaction.customer_id,
                    earlier,
                    Transaction.transaction_timestamp >= anchor - window,
                )
            )
            or 0
        )

    failed_last_hour = int(
        session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.customer_id == transaction.customer_id,
                earlier,
                Transaction.status == TransactionStatus.FAILED,
                Transaction.transaction_timestamp >= anchor - timedelta(hours=1),
            )
        )
        or 0
    )

    return VelocityWindows(**counts, failed_last_1_hour=failed_last_hour)


def _device_usage(session: Session, device: Device) -> DeviceUsage:
    distinct_customers, transaction_count = session.execute(
        select(
            func.count(func.distinct(Transaction.customer_id)),
            func.count(Transaction.id),
        ).where(Transaction.device_id == device.id)
    ).one()

    return DeviceUsage(
        distinct_customers=int(distinct_customers or 0),
        transaction_count=int(transaction_count or 0),
        first_seen_at=device.first_seen_at,
        last_seen_at=device.last_seen_at,
        shared_with_other_customers=int(distinct_customers or 0) > 1,
    )


def _ip_usage(session: Session, ip_record: IpAddress) -> IpUsage:
    distinct_customers, transaction_count = session.execute(
        select(
            func.count(func.distinct(Transaction.customer_id)),
            func.count(Transaction.id),
        ).where(Transaction.ip_address_id == ip_record.id)
    ).one()

    return IpUsage(
        distinct_customers=int(distinct_customers or 0),
        transaction_count=int(transaction_count or 0),
        first_seen_at=ip_record.first_seen_at,
        last_seen_at=ip_record.last_seen_at,
        shared_with_other_customers=int(distinct_customers or 0) > 1,
    )


def _recent_transactions(session: Session, transaction: Transaction) -> list[Transaction]:
    """The customer's transactions immediately preceding this one, newest first."""
    return list(
        session.scalars(
            select(Transaction)
            .where(
                Transaction.customer_id == transaction.customer_id,
                before_predicate(to_view(transaction)),
            )
            .order_by(Transaction.transaction_timestamp.desc(), Transaction.id.desc())
            .limit(RECENT_TRANSACTION_LIMIT)
        )
    )


def build_transaction_context(session: Session, transaction: Transaction) -> TransactionContext:
    """Gather the transaction plus the surrounding facts an analyst would pull up."""
    customer = session.get(Customer, transaction.customer_id)
    merchant = session.get(Merchant, transaction.merchant_id)
    if customer is None or merchant is None:  # pragma: no cover - guarded by FKs
        raise RuntimeError(
            f"Transaction {transaction.transaction_id} has a dangling customer or merchant"
        )

    device = session.get(Device, transaction.device_id) if transaction.device_id else None
    ip_record = (
        session.get(IpAddress, transaction.ip_address_id) if transaction.ip_address_id else None
    )

    location = LocationContext(
        country=transaction.country,
        city=transaction.city,
        customer_home_country=customer.country,
        customer_home_city=customer.city,
        matches_customer_home_country=transaction.country == customer.country,
        matches_customer_home_city=transaction.city == customer.city,
        ip_country=ip_record.country if ip_record else None,
        ip_city=ip_record.city if ip_record else None,
    )

    return TransactionContext(
        transaction=TransactionRead.model_validate(transaction),
        merchant=MerchantRead.model_validate(merchant),
        customer=CustomerRead.model_validate(customer),
        device=DeviceRead.model_validate(device) if device else None,
        ip_address=IpAddressRead.model_validate(ip_record) if ip_record else None,
        location=location,
        customer_velocity=_customer_velocity(session, transaction),
        device_usage=_device_usage(session, device) if device else None,
        ip_usage=_ip_usage(session, ip_record) if ip_record else None,
        recent_customer_transactions=[
            TransactionRead.model_validate(row)
            for row in _recent_transactions(session, transaction)
        ],
    )
