"""Read queries over the core payment entities.

Every function returns ORM rows or a :class:`Page` of them; none of them derives
a risk verdict.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import Customer, Device, IpAddress, Merchant, Transaction
from app.models.enums import TransactionStatus
from app.schemas.common import Page
from app.services.lookup import resolve_by_reference
from app.services.pagination import paginate


def list_merchants(session: Session) -> list[Merchant]:
    """All merchants, ordered by name. The set is small and not paginated."""
    return list(session.scalars(select(Merchant).order_by(Merchant.name)))


def get_customer(session: Session, reference: str) -> Customer:
    return resolve_by_reference(
        session, Customer, Customer.external_customer_id, reference, "Customer"
    )


def get_device(session: Session, reference: str) -> Device:
    return resolve_by_reference(session, Device, Device.device_id, reference, "Device")


def get_ip_address(session: Session, reference: str) -> IpAddress:
    return resolve_by_reference(session, IpAddress, IpAddress.ip_address, reference, "IP address")


def get_transaction(session: Session, reference: str) -> Transaction:
    return resolve_by_reference(
        session, Transaction, Transaction.transaction_id, reference, "Transaction"
    )


def _recent_transactions() -> Select[tuple[Transaction]]:
    """Base query: newest first, which is how every consumer wants them."""
    return select(Transaction).order_by(
        Transaction.transaction_timestamp.desc(), Transaction.id.desc()
    )


def list_transactions(
    session: Session,
    page: int,
    page_size: int,
    *,
    merchant_id: int | None = None,
    status: TransactionStatus | None = None,
    is_fraud: bool | None = None,
) -> Page[Transaction]:
    """Paginated transaction feed with optional filters."""
    statement = _recent_transactions()
    if merchant_id is not None:
        statement = statement.where(Transaction.merchant_id == merchant_id)
    if status is not None:
        statement = statement.where(Transaction.status == status)
    if is_fraud is not None:
        statement = statement.where(Transaction.is_fraud.is_(is_fraud))
    return paginate(session, statement, page, page_size)


def list_customer_transactions(
    session: Session, customer: Customer, page: int, page_size: int
) -> Page[Transaction]:
    return paginate(
        session,
        _recent_transactions().where(Transaction.customer_id == customer.id),
        page,
        page_size,
    )


def list_device_transactions(
    session: Session, device: Device, page: int, page_size: int
) -> Page[Transaction]:
    return paginate(
        session,
        _recent_transactions().where(Transaction.device_id == device.id),
        page,
        page_size,
    )


def list_ip_transactions(
    session: Session, ip_record: IpAddress, page: int, page_size: int
) -> Page[Transaction]:
    return paginate(
        session,
        _recent_transactions().where(Transaction.ip_address_id == ip_record.id),
        page,
        page_size,
    )
