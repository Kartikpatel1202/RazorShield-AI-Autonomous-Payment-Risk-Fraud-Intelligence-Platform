"""Customer behaviour features, all measured strictly before the transaction.

**Missing history.** A customer's very first payment has no baseline. Rather
than substituting a population average - which would leak information the
transaction never had - every undefined quantity is set to ``0.0`` and paired
with an explicit indicator (``customer_has_history``,
``customer_is_first_transaction``). The model can then learn that "no history"
is its own state instead of confusing it with "history that happens to be zero".
"""

from __future__ import annotations

from typing import Any

from ml.features.history import (
    CustomerHistory,
    CustomerProfile,
    TransactionView,
    age_in_days,
    safe_ratio,
)


def build(
    transaction: TransactionView, profile: CustomerProfile, history: CustomerHistory
) -> dict[str, Any]:
    """Account age, prior volume, prior outcomes and deviation from baseline."""
    amount = float(transaction.amount)
    mean_amount = history.mean_amount
    std_amount = history.amount_std

    seconds_since_previous = (
        (transaction.timestamp - history.last_transaction_at).total_seconds()
        if history.last_transaction_at is not None
        else 0.0
    )

    return {
        "customer_account_age_days": age_in_days(transaction.timestamp, profile.account_created_at),
        "customer_has_history": int(history.has_history),
        "customer_is_first_transaction": int(not history.has_history),
        "previous_transaction_count": history.transaction_count,
        "previous_success_count": history.success_count,
        "previous_failure_count": history.failure_count,
        "historical_failure_rate": safe_ratio(history.failure_count, history.transaction_count),
        "historical_success_rate": safe_ratio(history.success_count, history.transaction_count),
        "historical_average_amount": mean_amount,
        "historical_amount_std": std_amount,
        "historical_max_amount": history.amount_max,
        # Deviation from the customer's own baseline: the core "this is not how
        # they normally pay" signal.
        "amount_vs_historical_average": safe_ratio(amount, mean_amount),
        "amount_vs_historical_max": safe_ratio(amount, history.amount_max),
        "amount_zscore_vs_history": safe_ratio(amount - mean_amount, std_amount),
        "amount_above_historical_max": int(history.has_history and amount > history.amount_max),
        "seconds_since_previous_transaction": max(0.0, seconds_since_previous),
        "customer_history_span_days": age_in_days(
            transaction.timestamp, history.first_transaction_at
        ),
    }
