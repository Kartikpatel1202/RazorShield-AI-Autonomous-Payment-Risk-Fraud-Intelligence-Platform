"""Device features, measured strictly before the transaction.

Device sharing is the strongest coordinated-fraud signal in the data universe,
so both the raw customer count and the derived "is shared" flag are exposed.

**Missing device.** Some payments carry no fingerprint. ``has_device`` marks
those rows and every other device feature falls back to ``0``/``unknown``
rather than to a population statistic.
"""

from __future__ import annotations

from typing import Any

from ml.features.history import (
    DeviceProfile,
    EntityHistory,
    TransactionView,
    age_in_hours,
    safe_ratio,
)


def build(
    transaction: TransactionView,
    profile: DeviceProfile | None,
    history: EntityHistory | None,
) -> dict[str, Any]:
    """Device age, reach across customers and recent activity."""
    if history is None:
        return {
            "has_device": 0,
            "device_type": "unknown",
            "is_new_device": 1,
            "is_new_device_for_customer": 1,
            "device_age_hours": 0.0,
            "device_transaction_count": 0,
            "device_customer_count": 0,
            "device_is_shared": 0,
            "device_transactions_last_1h": 0,
            "device_transactions_last_24h": 0,
            "device_failed_last_1h": 0,
            "device_failure_rate_last_1h": 0.0,
        }

    counts = history.counts
    failures = history.failed_counts

    return {
        "has_device": 1,
        "device_type": profile.device_type if profile else "unknown",
        "is_new_device": int(not history.has_history),
        "is_new_device_for_customer": int(not history.customer_used_before),
        "device_age_hours": age_in_hours(transaction.timestamp, history.first_seen_at),
        "device_transaction_count": history.transaction_count,
        "device_customer_count": history.distinct_customers,
        "device_is_shared": int(history.distinct_customers > 1),
        "device_transactions_last_1h": counts.get("1h", 0),
        "device_transactions_last_24h": counts.get("24h", 0),
        "device_failed_last_1h": failures.get("1h", 0),
        "device_failure_rate_last_1h": safe_ratio(failures.get("1h", 0), counts.get("1h", 0)),
    }
