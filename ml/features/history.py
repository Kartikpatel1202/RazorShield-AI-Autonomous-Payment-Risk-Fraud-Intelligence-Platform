"""The point-in-time data contract.

Every feature in RazorShield is computed from two things and nothing else:

1. the transaction being scored, and
2. a :class:`HistoryWindow` summarising what had already happened *strictly
   before* it.

Feature functions never touch the ORM or the database, so they cannot reach
future rows even by accident. Two providers build the same ``HistoryWindow`` -
a streaming accumulator for bulk dataset construction and a SQL provider for
single-transaction inference - and a test asserts the two agree exactly.

**Ordering.** "Before" means strictly earlier in the total order
``(transaction_timestamp, id)``. Using the surrogate id as a tie-breaker makes
the boundary unambiguous when two payments share a timestamp, and guarantees a
transaction never counts itself as part of its own history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt

# Velocity windows tracked for a customer.
CUSTOMER_WINDOWS: Mapping[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}

# Velocity windows tracked for a device or an IP address.
ENTITY_WINDOWS: Mapping[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
}

#: Longest window any accumulator has to retain, used to prune its buffers.
MAX_WINDOW = max(CUSTOMER_WINDOWS.values())


@dataclass(frozen=True)
class TransactionView:
    """A transaction as the feature layer sees it - plain data, no ORM."""

    id: int
    transaction_id: str
    merchant_id: int
    customer_id: int
    device_id: int | None
    ip_address_id: int | None
    amount: float
    currency: str
    payment_method: str
    status: str
    timestamp: datetime
    country: str
    city: str
    failed_attempts: int
    #: Ground-truth label. Carried as metadata for training; never a feature.
    is_fraud: bool

    @property
    def sort_key(self) -> tuple[datetime, int]:
        return (self.timestamp, self.id)

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @property
    def is_successful(self) -> bool:
        return self.status == "successful"


@dataclass(frozen=True)
class CustomerProfile:
    """Static customer attributes that are safe to use at any point in time.

    Deliberately excludes ``average_transaction_amount``,
    ``successful_transaction_count``, ``failed_transaction_count``,
    ``chargeback_count`` and ``historical_risk_level``: those columns are
    recomputed over the *whole* dataset by the seed generator, so at any given
    T they already encode the future. The equivalent quantities are rebuilt
    point-in-time in :class:`CustomerHistory` instead.
    """

    account_created_at: datetime
    home_country: str
    home_city: str


@dataclass(frozen=True)
class DeviceProfile:
    """Static device attributes.

    ``first_seen_at``, ``last_seen_at`` and ``is_trusted`` are excluded for the
    same reason as the customer counters - the seed generator derives them from
    the complete transaction stream. Device age is measured point-in-time.
    """

    device_type: str


@dataclass(frozen=True)
class IpProfile:
    """Static IP attributes.

    ``reputation_score`` and ``is_proxy`` are assigned when the address is
    created and are not derived from transaction outcomes, so they are safe to
    read directly. ``first_seen_at``/``last_seen_at`` are not, and are rebuilt
    point-in-time.
    """

    reputation_score: float
    is_proxy: bool
    country: str
    city: str


@dataclass(frozen=True)
class CustomerHistory:
    """Everything the customer had done before the transaction being scored."""

    transaction_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    amount_sum: float = 0.0
    amount_square_sum: float = 0.0
    amount_max: float = 0.0
    first_transaction_at: datetime | None = None
    last_transaction_at: datetime | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    failed_counts: Mapping[str, int] = field(default_factory=dict)
    amounts: Mapping[str, float] = field(default_factory=dict)
    country_counts: Mapping[str, int] = field(default_factory=dict)
    city_counts: Mapping[str, int] = field(default_factory=dict)
    last_country: str | None = None
    last_city: str | None = None

    @property
    def has_history(self) -> bool:
        return self.transaction_count > 0

    @property
    def mean_amount(self) -> float:
        return self.amount_sum / self.transaction_count if self.transaction_count else 0.0

    @property
    def amount_std(self) -> float:
        """Population standard deviation of prior amounts.

        Zero for fewer than two prior transactions - there is no spread to
        measure, and inventing one would be a fabricated feature.
        """
        if self.transaction_count < 2:
            return 0.0
        mean = self.mean_amount
        # E[x^2] - mean^2 rather than a two-pass or Welford formulation: it is
        # the only form a single SQL aggregate and a streaming accumulator can
        # both compute identically, and provider parity matters more here than
        # the last few significant figures. The cancellation error is ~1e-8
        # relative, far below anything a tree split can resolve.
        variance = self.amount_square_sum / self.transaction_count - mean * mean
        return sqrt(max(0.0, variance))


@dataclass(frozen=True)
class EntityHistory:
    """Prior activity of a device or an IP address.

    ``customer_used_before`` answers "has *this* customer paid from this entity
    before?", which is a different and sharper question than whether the entity
    has been seen at all.
    """

    transaction_count: int = 0
    distinct_customers: int = 0
    first_seen_at: datetime | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    failed_counts: Mapping[str, int] = field(default_factory=dict)
    customer_used_before: bool = False

    @property
    def has_history(self) -> bool:
        return self.transaction_count > 0


@dataclass(frozen=True)
class HistoryWindow:
    """The complete point-in-time context for one transaction."""

    customer_profile: CustomerProfile
    customer: CustomerHistory
    device_profile: DeviceProfile | None
    device: EntityHistory | None
    ip_profile: IpProfile | None
    ip: EntityHistory | None


def age_in_hours(reference: datetime, since: datetime | None) -> float:
    """Hours between ``since`` and ``reference``; 0.0 when ``since`` is unknown."""
    if since is None:
        return 0.0
    return max(0.0, (reference - since).total_seconds() / 3600.0)


def age_in_days(reference: datetime, since: datetime | None) -> float:
    """Days between ``since`` and ``reference``; 0.0 when ``since`` is unknown."""
    if since is None:
        return 0.0
    return max(0.0, (reference - since).total_seconds() / 86400.0)


def safe_ratio(numerator: float, denominator: float, *, default: float = 0.0) -> float:
    """``numerator / denominator``, or ``default`` when there is nothing to divide by.

    Used wherever a ratio is undefined for a customer, device or IP with no
    history - the alternative would be silently substituting a population
    statistic, which leaks information the transaction did not have.
    """
    if denominator <= 0:
        return default
    return numerator / denominator
