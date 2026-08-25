"""Tunable parameters for the seed generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Fixed seed: the same value must always rebuild the same dataset.
DEFAULT_RANDOM_SEED = 20260101

# Fraud prevalence. Real card-not-present fraud sits well under 1% of volume;
# 1.5% keeps the dataset realistic while leaving enough positive examples for a
# model to learn from. Documented in docs/dataset.md.
DEFAULT_FRAUD_RATE = 0.015


@dataclass(frozen=True)
class SeedConfig:
    """Size and shape of the generated dataset."""

    random_seed: int = DEFAULT_RANDOM_SEED
    merchants: int = 4
    customers: int = 1_500
    ip_addresses: int = 900
    transactions: int = 20_000
    history_days: int = 90
    fraud_rate: float = DEFAULT_FRAUD_RATE

    # Devices deliberately shared between unrelated customers. The total device
    # count is derived, not configured: every customer owns one or more private
    # device fingerprints and these are added on top. A small fixed device pool
    # would force every device to be shared by several customers, which would
    # destroy device sharing as a fraud signal. See docs/dataset.md.
    shared_devices: int = 60

    # Share of the IP pool that behaves like public or carrier-grade NAT space,
    # legitimately seen from many customers.
    public_ip_share: float = 0.2

    # Anchor for every generated timestamp. Defaults to the moment the generator
    # runs so that "last 5 minutes" velocity queries return rows; pin it to make
    # timestamps byte-for-byte reproducible too.
    reference_time: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(second=0, microsecond=0)
    )

    def __post_init__(self) -> None:
        if self.transactions < self.customers:
            raise ValueError("transactions must be at least one per customer")
        if not 0 < self.fraud_rate < 0.5:
            raise ValueError("fraud_rate must be a small positive share, well below 0.5")
        if self.history_days < 7:
            raise ValueError("history_days must cover at least one week")

    @property
    def history_start(self) -> datetime:
        """Earliest transaction timestamp in the dataset."""
        return self.reference_time - timedelta(days=self.history_days)
