"""Streaming point-in-time history for bulk dataset construction.

The whole transaction stream is walked once in chronological order. For each
transaction the accumulator is asked for the history *as it stands*, and only
afterwards is the transaction folded into that state. Leakage is therefore
prevented structurally rather than by a filter that could be got wrong: at the
moment a transaction's features are computed, no later row has been observed at
all.

Nothing here records ``is_fraud``. The label never enters the state, so no
feature can be derived from it - not for the transaction being scored, and not
for any earlier one.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ml.features.history import (
    CUSTOMER_WINDOWS,
    ENTITY_WINDOWS,
    MAX_WINDOW,
    CustomerHistory,
    EntityHistory,
    TransactionView,
)


def _window_counts(
    events: deque[tuple[datetime, float, bool]],
    now: datetime,
    windows: Mapping[str, timedelta],
) -> tuple[dict[str, int], dict[str, int], dict[str, float]]:
    """Count events, failures and amounts inside each window ending at ``now``.

    The buffer is ordered oldest-first, so each window is a suffix scan. Buffers
    are pruned to the longest window, keeping this cheap.
    """
    counts = {name: 0 for name in windows}
    failures = {name: 0 for name in windows}
    amounts = {name: 0.0 for name in windows}

    for timestamp, amount, failed in reversed(events):
        age = now - timestamp
        matched = False
        for name, window in windows.items():
            if age <= window:
                counts[name] += 1
                amounts[name] += amount
                if failed:
                    failures[name] += 1
                matched = True
        if not matched:
            # Older than every window; everything before it is older still.
            break

    return counts, failures, amounts


@dataclass
class _CustomerState:
    """Running totals for one customer."""

    transaction_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    amount_sum: float = 0.0
    amount_square_sum: float = 0.0
    amount_max: float = 0.0
    first_transaction_at: datetime | None = None
    last_transaction_at: datetime | None = None
    events: deque[tuple[datetime, float, bool]] = field(default_factory=deque)
    country_counts: Counter[str] = field(default_factory=Counter)
    city_counts: Counter[str] = field(default_factory=Counter)
    last_country: str | None = None
    last_city: str | None = None

    def snapshot(self, now: datetime) -> CustomerHistory:
        _prune(self.events, now, MAX_WINDOW)
        counts, failures, amounts = _window_counts(self.events, now, CUSTOMER_WINDOWS)
        return CustomerHistory(
            transaction_count=self.transaction_count,
            success_count=self.success_count,
            failure_count=self.failure_count,
            amount_sum=self.amount_sum,
            amount_square_sum=self.amount_square_sum,
            amount_max=self.amount_max,
            first_transaction_at=self.first_transaction_at,
            last_transaction_at=self.last_transaction_at,
            counts=counts,
            failed_counts=failures,
            amounts=amounts,
            country_counts=dict(self.country_counts),
            city_counts=dict(self.city_counts),
            last_country=self.last_country,
            last_city=self.last_city,
        )

    def observe(self, transaction: TransactionView) -> None:
        amount = transaction.amount
        self.transaction_count += 1
        self.amount_sum += amount
        self.amount_square_sum += amount * amount
        self.amount_max = max(self.amount_max, amount)
        if transaction.is_successful:
            self.success_count += 1
        elif transaction.is_failed:
            self.failure_count += 1

        if self.first_transaction_at is None:
            self.first_transaction_at = transaction.timestamp
        self.last_transaction_at = transaction.timestamp

        self.events.append((transaction.timestamp, amount, transaction.is_failed))
        self.country_counts[transaction.country] += 1
        self.city_counts[transaction.city] += 1
        self.last_country = transaction.country
        self.last_city = transaction.city


@dataclass
class _EntityState:
    """Running totals for one device or IP address."""

    transaction_count: int = 0
    first_seen_at: datetime | None = None
    customers: set[int] = field(default_factory=set)
    events: deque[tuple[datetime, float, bool]] = field(default_factory=deque)

    def snapshot(self, now: datetime, customer_id: int) -> EntityHistory:
        _prune(self.events, now, max(ENTITY_WINDOWS.values()))
        counts, failures, _ = _window_counts(self.events, now, ENTITY_WINDOWS)
        return EntityHistory(
            transaction_count=self.transaction_count,
            distinct_customers=len(self.customers),
            first_seen_at=self.first_seen_at,
            counts=counts,
            failed_counts=failures,
            customer_used_before=customer_id in self.customers,
        )

    def observe(self, transaction: TransactionView) -> None:
        self.transaction_count += 1
        if self.first_seen_at is None:
            self.first_seen_at = transaction.timestamp
        self.customers.add(transaction.customer_id)
        self.events.append((transaction.timestamp, transaction.amount, transaction.is_failed))


def _prune(events: deque[tuple[datetime, float, bool]], now: datetime, window: timedelta) -> None:
    """Drop buffered events that can no longer fall inside any window."""
    cutoff = now - window
    while events and events[0][0] < cutoff:
        events.popleft()


class HistoryAccumulator:
    """Chronological, single-pass point-in-time history.

    Usage is strictly ``snapshot`` then ``observe``, in that order, over
    transactions fed in ascending ``(timestamp, id)`` order.
    """

    def __init__(self) -> None:
        self._customers: dict[int, _CustomerState] = {}
        self._devices: dict[int, _EntityState] = {}
        self._ips: dict[int, _EntityState] = {}
        self._last_key: tuple[datetime, int] | None = None

    def customer_snapshot(self, transaction: TransactionView) -> CustomerHistory:
        state = self._customers.get(transaction.customer_id)
        return state.snapshot(transaction.timestamp) if state else CustomerHistory()

    def device_snapshot(self, transaction: TransactionView) -> EntityHistory | None:
        if transaction.device_id is None:
            return None
        state = self._devices.get(transaction.device_id)
        if state is None:
            return EntityHistory()
        return state.snapshot(transaction.timestamp, transaction.customer_id)

    def ip_snapshot(self, transaction: TransactionView) -> EntityHistory | None:
        if transaction.ip_address_id is None:
            return None
        state = self._ips.get(transaction.ip_address_id)
        if state is None:
            return EntityHistory()
        return state.snapshot(transaction.timestamp, transaction.customer_id)

    def observe(self, transaction: TransactionView) -> None:
        """Fold a transaction into the state, after its features were taken."""
        if self._last_key is not None and transaction.sort_key < self._last_key:
            raise ValueError(
                "transactions must be observed in ascending (timestamp, id) order; "
                f"{transaction.transaction_id} arrived out of sequence"
            )
        self._last_key = transaction.sort_key

        self._customers.setdefault(transaction.customer_id, _CustomerState()).observe(transaction)
        if transaction.device_id is not None:
            self._devices.setdefault(transaction.device_id, _EntityState()).observe(transaction)
        if transaction.ip_address_id is not None:
            self._ips.setdefault(transaction.ip_address_id, _EntityState()).observe(transaction)
