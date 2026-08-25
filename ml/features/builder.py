"""Assembles the full feature row for one transaction.

This is the only place that knows the complete feature set. Both the training
dataset builder and the inference path go through it, so a model can never be
served features built differently from the ones it was trained on.
"""

from __future__ import annotations

from typing import Any

from ml.features import (
    customer_features,
    device_features,
    ip_features,
    location_features,
    transaction_features,
    velocity_features,
)
from ml.features.history import HistoryWindow, TransactionView
from ml.features.transaction_features import MerchantProfile


def build_features(
    transaction: TransactionView,
    window: HistoryWindow,
    merchant: MerchantProfile | None = None,
) -> dict[str, Any]:
    """Compute every feature for ``transaction`` from its point-in-time ``window``.

    Pure: given the same transaction and window it always returns the same row,
    and it cannot reach data that is not already inside those two arguments.
    """
    features: dict[str, Any] = {}
    features.update(transaction_features.build(transaction, merchant))
    features.update(customer_features.build(transaction, window.customer_profile, window.customer))
    features.update(velocity_features.build(transaction, window.customer))
    features.update(device_features.build(transaction, window.device_profile, window.device))
    features.update(ip_features.build(transaction, window.ip_profile, window.ip))
    features.update(
        location_features.build(
            transaction, window.customer_profile, window.customer, window.ip_profile
        )
    )
    return features
