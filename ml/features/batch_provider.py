"""Whole-dataset feature generation in a single chronological pass.

Loads every transaction in ``(timestamp, id)`` order and walks it once through
:class:`~ml.features.accumulator.HistoryAccumulator`. Each row's features are
taken from the state *before* the row is folded in, so the pass is O(n) in
transactions and cannot see the future.

This replaces the naive alternative of one point-in-time query per transaction,
which would be tens of thousands of round trips for a 20,000-row dataset.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from ml.features.accumulator import HistoryAccumulator
from ml.features.builder import build_features
from ml.features.history import HistoryWindow, TransactionView
from ml.features.loader import (
    iter_transactions,
    load_customer_profiles,
    load_device_profiles,
    load_ip_profiles,
    load_merchant_profiles,
)

logger = logging.getLogger(__name__)


class MissingProfileError(LookupError):
    """A transaction references an entity with no profile row."""


def iter_feature_rows(session: Session) -> Iterator[tuple[TransactionView, dict[str, Any]]]:
    """Yield ``(transaction, features)`` for every transaction, oldest first.

    Profiles are preloaded once; the per-transaction cost is then pure Python
    accumulator work with no further database access.
    """
    customer_profiles = load_customer_profiles(session)
    device_profiles = load_device_profiles(session)
    ip_profiles = load_ip_profiles(session)
    merchant_profiles = load_merchant_profiles(session)

    logger.info(
        "Loaded profiles: %d customers, %d devices, %d IPs, %d merchants",
        len(customer_profiles),
        len(device_profiles),
        len(ip_profiles),
        len(merchant_profiles),
    )

    accumulator = HistoryAccumulator()

    for transaction in iter_transactions(session):
        profile = customer_profiles.get(transaction.customer_id)
        if profile is None:
            raise MissingProfileError(
                f"transaction {transaction.transaction_id} references a missing customer"
            )

        window = HistoryWindow(
            customer_profile=profile,
            customer=accumulator.customer_snapshot(transaction),
            device_profile=device_profiles.get(transaction.device_id)
            if transaction.device_id
            else None,
            device=accumulator.device_snapshot(transaction),
            ip_profile=ip_profiles.get(transaction.ip_address_id)
            if transaction.ip_address_id
            else None,
            ip=accumulator.ip_snapshot(transaction),
        )

        features = build_features(
            transaction, window, merchant_profiles.get(transaction.merchant_id)
        )

        yield transaction, features

        # Only now does this transaction become part of anyone's history.
        accumulator.observe(transaction)
