"""Reads the payment universe out of PostgreSQL into plain feature-layer types.

This is the only module in ``ml.features`` that knows about SQLAlchemy. Every
other module works on the dataclasses in :mod:`ml.features.history`, which keeps
the feature logic pure and unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Device, IpAddress, Merchant, Transaction
from ml.features.history import (
    CustomerProfile,
    DeviceProfile,
    IpProfile,
    TransactionView,
)
from ml.features.transaction_features import MerchantProfile


def to_view(transaction: Transaction) -> TransactionView:
    """Convert an ORM transaction into the immutable feature-layer view."""
    return TransactionView(
        id=transaction.id,
        transaction_id=transaction.transaction_id,
        merchant_id=transaction.merchant_id,
        customer_id=transaction.customer_id,
        device_id=transaction.device_id,
        ip_address_id=transaction.ip_address_id,
        amount=float(transaction.amount),
        currency=transaction.currency,
        payment_method=str(transaction.payment_method),
        status=str(transaction.status),
        timestamp=transaction.transaction_timestamp,
        country=transaction.country,
        city=transaction.city,
        failed_attempts=transaction.failed_attempts,
        is_fraud=transaction.is_fraud,
    )


def iter_transactions(session: Session, batch_size: int = 5_000) -> Iterator[TransactionView]:
    """Stream every transaction in ascending ``(timestamp, id)`` order.

    The ordering is the point-in-time contract: the accumulator relies on it to
    guarantee that no later row has been observed when a row is scored.
    """
    statement = (
        select(Transaction)
        .order_by(Transaction.transaction_timestamp.asc(), Transaction.id.asc())
        .execution_options(yield_per=batch_size)
    )
    for transaction in session.scalars(statement):
        yield to_view(transaction)


def load_customer_profiles(session: Session) -> dict[int, CustomerProfile]:
    """Static, leak-safe customer attributes keyed by primary key."""
    rows = session.execute(
        select(Customer.id, Customer.account_created_at, Customer.country, Customer.city)
    ).all()
    return {
        customer_id: CustomerProfile(
            account_created_at=created_at, home_country=country, home_city=city
        )
        for customer_id, created_at, country, city in rows
    }


def load_device_profiles(session: Session) -> dict[int, DeviceProfile]:
    """Device type only - age and trust are rebuilt point-in-time."""
    rows = session.execute(select(Device.id, Device.device_type)).all()
    return {
        device_id: DeviceProfile(device_type=str(device_type)) for device_id, device_type in rows
    }


def load_ip_profiles(session: Session) -> dict[int, IpProfile]:
    """Stored simulated reputation and geography. No external service is called."""
    rows = session.execute(
        select(
            IpAddress.id,
            IpAddress.reputation_score,
            IpAddress.is_proxy,
            IpAddress.country,
            IpAddress.city,
        )
    ).all()
    return {
        ip_id: IpProfile(
            reputation_score=float(reputation),
            is_proxy=bool(is_proxy),
            country=country,
            city=city,
        )
        for ip_id, reputation, is_proxy, country, city in rows
    }


def load_merchant_profiles(session: Session) -> dict[int, MerchantProfile]:
    rows = session.execute(select(Merchant.id, Merchant.category)).all()
    return {
        merchant_id: MerchantProfile(category=category or "unknown")
        for merchant_id, category in rows
    }


def get_customer_profile(session: Session, customer_id: int) -> CustomerProfile | None:
    row = session.execute(
        select(Customer.account_created_at, Customer.country, Customer.city).where(
            Customer.id == customer_id
        )
    ).first()
    if row is None:
        return None
    return CustomerProfile(account_created_at=row[0], home_country=row[1], home_city=row[2])


def get_device_profile(session: Session, device_id: int | None) -> DeviceProfile | None:
    if device_id is None:
        return None
    device_type = session.scalar(select(Device.device_type).where(Device.id == device_id))
    return DeviceProfile(device_type=str(device_type)) if device_type is not None else None


def get_ip_profile(session: Session, ip_id: int | None) -> IpProfile | None:
    if ip_id is None:
        return None
    row = session.execute(
        select(
            IpAddress.reputation_score, IpAddress.is_proxy, IpAddress.country, IpAddress.city
        ).where(IpAddress.id == ip_id)
    ).first()
    if row is None:
        return None
    return IpProfile(
        reputation_score=float(row[0]), is_proxy=bool(row[1]), country=row[2], city=row[3]
    )


def get_merchant_profile(session: Session, merchant_id: int) -> MerchantProfile | None:
    category = session.scalar(select(Merchant.category).where(Merchant.id == merchant_id))
    return MerchantProfile(category=category or "unknown")
