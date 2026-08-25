"""Read schemas for the core payment entities.

Every field is a recorded fact. Nothing here is a risk score, a fraud
probability or a model output - those arrive in a later phase.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import (
    DeviceType,
    PaymentMethod,
    RiskLevel,
    TransactionStatus,
)
from app.schemas.common import ORMModel


class MerchantRead(ORMModel):
    id: int
    external_merchant_id: str
    name: str
    email: str
    category: str | None
    country: str
    is_active: bool
    created_at: datetime


class CustomerRead(ORMModel):
    id: int
    merchant_id: int
    external_customer_id: str
    email: str | None
    account_created_at: datetime
    country: str
    city: str
    average_transaction_amount: Decimal
    successful_transaction_count: int
    failed_transaction_count: int
    chargeback_count: int
    historical_risk_level: RiskLevel = Field(
        description="Observed band derived from past behaviour. Not a prediction."
    )
    created_at: datetime
    updated_at: datetime


class DeviceRead(ORMModel):
    id: int
    device_id: str
    device_type: DeviceType
    device_label: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_trusted: bool
    created_at: datetime


class IpAddressRead(ORMModel):
    id: int
    ip_address: str
    country: str
    city: str
    first_seen_at: datetime
    last_seen_at: datetime
    reputation_score: Decimal = Field(
        description="Simulated 0-100 reputation. No external service is consulted."
    )
    is_proxy: bool
    created_at: datetime


class TransactionRead(ORMModel):
    id: int
    transaction_id: str
    merchant_id: int
    customer_id: int
    device_id: int | None
    ip_address_id: int | None
    amount: Decimal
    currency: str
    payment_method: PaymentMethod
    status: TransactionStatus
    transaction_timestamp: datetime
    country: str
    city: str
    failed_attempts: int
    is_fraud: bool = Field(
        description="Ground-truth label of the simulated dataset, for training and evaluation."
    )
    created_at: datetime
