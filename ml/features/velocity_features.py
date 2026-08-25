"""Customer velocity: how much activity preceded this transaction, and how fast.

Every window ends at the transaction's own timestamp and excludes the
transaction itself, so a payment never contributes to its own velocity.
"""

from __future__ import annotations

from typing import Any

from ml.features.history import CustomerHistory, TransactionView, safe_ratio


def build(transaction: TransactionView, history: CustomerHistory) -> dict[str, Any]:
    """Rolling transaction counts, failure counts and spend for the customer."""
    counts = history.counts
    failures = history.failed_counts
    amounts = history.amounts
    amount = float(transaction.amount)

    return {
        "transactions_last_5m": counts.get("5m", 0),
        "transactions_last_1h": counts.get("1h", 0),
        "transactions_last_24h": counts.get("24h", 0),
        "transactions_last_7d": counts.get("7d", 0),
        "failed_transactions_last_1h": failures.get("1h", 0),
        "failed_transactions_last_24h": failures.get("24h", 0),
        "amount_last_1h": amounts.get("1h", 0.0),
        "amount_last_24h": amounts.get("24h", 0.0),
        # A burst of failures immediately before a payment is a classic
        # card-testing / takeover shape.
        "failure_rate_last_1h": safe_ratio(failures.get("1h", 0), counts.get("1h", 0)),
        "amount_vs_amount_last_24h": safe_ratio(amount, amounts.get("24h", 0.0)),
    }
