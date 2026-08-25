"""The aggregated view returned by ``/api/transactions/{id}/context``.

Everything in this payload is counted or looked up from the database. It is
deliberately evidence, not judgement: no field scores, ranks or classifies the
transaction's risk.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.entities import (
    CustomerRead,
    DeviceRead,
    IpAddressRead,
    MerchantRead,
    TransactionRead,
)


class VelocityWindows(BaseModel):
    """How many transactions the customer made in the run-up to this one."""

    last_5_minutes: int
    last_1_hour: int
    last_24_hours: int
    last_7_days: int
    failed_last_1_hour: int


class DeviceUsage(BaseModel):
    """How widely the paying device has been seen."""

    distinct_customers: int
    transaction_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    shared_with_other_customers: bool


class IpUsage(BaseModel):
    """How widely the originating IP address has been seen."""

    distinct_customers: int
    transaction_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    shared_with_other_customers: bool


class LocationContext(BaseModel):
    """Where the payment came from, relative to what is known about the payer."""

    country: str
    city: str
    customer_home_country: str
    customer_home_city: str
    matches_customer_home_country: bool
    matches_customer_home_city: bool
    ip_country: str | None
    ip_city: str | None


class TransactionContext(BaseModel):
    """A transaction together with everything an investigator would pull up."""

    transaction: TransactionRead
    merchant: MerchantRead
    customer: CustomerRead
    device: DeviceRead | None
    ip_address: IpAddressRead | None
    location: LocationContext
    customer_velocity: VelocityWindows
    device_usage: DeviceUsage | None
    ip_usage: IpUsage | None
    recent_customer_transactions: list[TransactionRead] = Field(
        description="The customer's most recent transactions before this one, newest first."
    )
