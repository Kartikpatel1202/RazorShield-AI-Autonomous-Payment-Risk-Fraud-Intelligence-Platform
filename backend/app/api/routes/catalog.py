"""Read-only data-access endpoints for merchants, customers, devices and IPs.

These endpoints expose recorded facts. They perform no risk scoring, run no
model and make no decision.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.db.session import get_db
from app.schemas.common import DEFAULT_PAGE_SIZE, Page, PageNumber, PageSize
from app.schemas.entities import (
    CustomerRead,
    DeviceRead,
    IpAddressRead,
    MerchantRead,
    TransactionRead,
)
from app.services import catalog

router = APIRouter(
    # Merchants, customers, devices and IPs are the entities a transaction
    # points at; reading them is reading transaction data.
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))]
)

CustomerRef = Path(
    description="External customer id (e.g. CUSTOMER_NORMAL_001) or numeric primary key"
)
DeviceRef = Path(description="Device fingerprint (e.g. dev_000123) or numeric primary key")
IpRef = Path(description="IP address (e.g. 198.18.100.31) or numeric primary key")


@router.get("/merchants", response_model=list[MerchantRead], tags=["merchants"])
def list_merchants(session: Session = Depends(get_db)) -> list[MerchantRead]:
    """Every merchant on the platform."""
    return catalog.list_merchants(session)  # type: ignore[return-value]


@router.get("/customers/{customer_id}", response_model=CustomerRead, tags=["customers"])
def get_customer(
    customer_id: str = CustomerRef, session: Session = Depends(get_db)
) -> CustomerRead:
    """One customer with their historical behaviour counters."""
    return catalog.get_customer(session, customer_id)  # type: ignore[return-value]


@router.get(
    "/customers/{customer_id}/transactions",
    response_model=Page[TransactionRead],
    tags=["customers"],
)
def list_customer_transactions(
    customer_id: str = CustomerRef,
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    session: Session = Depends(get_db),
) -> Page[TransactionRead]:
    """A customer's transaction history, newest first."""
    customer = catalog.get_customer(session, customer_id)
    return catalog.list_customer_transactions(session, customer, page, page_size)  # type: ignore[return-value]


@router.get("/devices/{device_id}", response_model=DeviceRead, tags=["devices"])
def get_device(device_id: str = DeviceRef, session: Session = Depends(get_db)) -> DeviceRead:
    """One device fingerprint."""
    return catalog.get_device(session, device_id)  # type: ignore[return-value]


@router.get(
    "/devices/{device_id}/transactions",
    response_model=Page[TransactionRead],
    tags=["devices"],
)
def list_device_transactions(
    device_id: str = DeviceRef,
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    session: Session = Depends(get_db),
) -> Page[TransactionRead]:
    """Every payment made from a device, newest first.

    Rows from more than one customer are the raw material for the
    coordinated-fraud analysis in a later phase.
    """
    device = catalog.get_device(session, device_id)
    return catalog.list_device_transactions(session, device, page, page_size)  # type: ignore[return-value]


@router.get("/ip-addresses/{ip_id}", response_model=IpAddressRead, tags=["ip-addresses"])
def get_ip_address(ip_id: str = IpRef, session: Session = Depends(get_db)) -> IpAddressRead:
    """One IP address with its simulated reputation."""
    return catalog.get_ip_address(session, ip_id)  # type: ignore[return-value]


@router.get(
    "/ip-addresses/{ip_id}/transactions",
    response_model=Page[TransactionRead],
    tags=["ip-addresses"],
)
def list_ip_transactions(
    ip_id: str = IpRef,
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    session: Session = Depends(get_db),
) -> Page[TransactionRead]:
    """Every payment originating from an IP address, newest first."""
    ip_record = catalog.get_ip_address(session, ip_id)
    return catalog.list_ip_transactions(session, ip_record, page, page_size)  # type: ignore[return-value]
