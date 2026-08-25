"""Features intrinsic to the transaction being scored.

These read only the transaction row itself plus static merchant attributes, so
they carry no temporal risk at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log1p
from typing import Any

from ml.features.history import TransactionView

# Hours treated as the overnight trough, when legitimate volume is lowest.
NIGHT_HOURS = frozenset({0, 1, 2, 3, 4, 5})


@dataclass(frozen=True)
class MerchantProfile:
    """Static merchant attributes. Nothing here depends on transaction outcomes."""

    category: str


def build(transaction: TransactionView, merchant: MerchantProfile | None) -> dict[str, Any]:
    """Amount, timing and payment-instrument features.

    ``failed_attempts`` is the count of consecutive failures the customer had
    immediately before this attempt. It is recorded on the row by the payment
    flow itself and describes only what already happened, so it is safe to use.
    """
    timestamp = transaction.timestamp
    hour = timestamp.hour
    weekday = timestamp.weekday()

    return {
        "amount": float(transaction.amount),
        "log_amount": log1p(max(0.0, float(transaction.amount))),
        "payment_method": transaction.payment_method,
        "merchant_category": merchant.category if merchant else "unknown",
        "hour_of_day": hour,
        "day_of_week": weekday,
        "is_weekend": int(weekday >= 5),
        "is_night": int(hour in NIGHT_HOURS),
        "failed_attempts": int(transaction.failed_attempts),
    }
