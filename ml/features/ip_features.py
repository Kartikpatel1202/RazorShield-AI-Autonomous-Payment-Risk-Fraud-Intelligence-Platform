"""IP address features, measured strictly before the transaction.

``ip_reputation_score`` and ``ip_is_proxy`` come from the stored simulated
values. They are assigned when an address is created and are not derived from
transaction outcomes, so reading them at any point in time is safe. No external
reputation service is called anywhere in this project.
"""

from __future__ import annotations

from typing import Any

from ml.features.history import (
    EntityHistory,
    IpProfile,
    TransactionView,
    age_in_hours,
    safe_ratio,
)

#: Neutral reputation used when the transaction carries no IP address. Chosen as
#: the midpoint of the 0-100 scale so a missing address is neither exonerating
#: nor incriminating; `has_ip` lets the model tell the two cases apart.
UNKNOWN_REPUTATION = 50.0


def build(
    transaction: TransactionView,
    profile: IpProfile | None,
    history: EntityHistory | None,
) -> dict[str, Any]:
    """IP age, reach across customers, recent activity and stored reputation."""
    if history is None:
        return {
            "has_ip": 0,
            "is_new_ip": 1,
            "is_new_ip_for_customer": 1,
            "ip_age_hours": 0.0,
            "ip_transaction_count": 0,
            "ip_customer_count": 0,
            "ip_is_shared": 0,
            "ip_transactions_last_1h": 0,
            "ip_transactions_last_24h": 0,
            "ip_failed_last_1h": 0,
            "ip_failure_rate_last_1h": 0.0,
            "ip_reputation_score": UNKNOWN_REPUTATION,
            "ip_is_proxy": 0,
        }

    counts = history.counts
    failures = history.failed_counts

    return {
        "has_ip": 1,
        "is_new_ip": int(not history.has_history),
        "is_new_ip_for_customer": int(not history.customer_used_before),
        "ip_age_hours": age_in_hours(transaction.timestamp, history.first_seen_at),
        "ip_transaction_count": history.transaction_count,
        "ip_customer_count": history.distinct_customers,
        "ip_is_shared": int(history.distinct_customers > 1),
        "ip_transactions_last_1h": counts.get("1h", 0),
        "ip_transactions_last_24h": counts.get("24h", 0),
        "ip_failed_last_1h": failures.get("1h", 0),
        "ip_failure_rate_last_1h": safe_ratio(failures.get("1h", 0), counts.get("1h", 0)),
        "ip_reputation_score": profile.reputation_score if profile else UNKNOWN_REPUTATION,
        "ip_is_proxy": int(profile.is_proxy) if profile else 0,
    }
