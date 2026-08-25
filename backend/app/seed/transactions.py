"""Generates the transaction stream.

Volume is split across customers in proportion to how often their behaviour
profile says they pay. Timestamps follow a realistic hour-of-day curve across the
whole history window so later phases can compute velocity over any time window.

Fraudulent rows are labelled with ``is_fraud`` and given genuinely different
characteristics - larger amounts, unfamiliar devices, unusual hours - so the
evidence for a later model is really present in the data. No risk score is
computed or stored here.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import Transaction
from app.models.enums import TransactionStatus
from app.seed.config import SeedConfig
from app.seed.locations import DATASET_CURRENCY, DOMESTIC_LOCATIONS, INTERNATIONAL_LOCATIONS
from app.seed.population import CustomerPlan

logger = logging.getLogger(__name__)

# Relative payment volume by hour of day (index 0 = midnight). Evening peak,
# overnight trough - the shape that makes a 3am payment notable.
HOUR_WEIGHTS: tuple[float, ...] = (
    0.4,
    0.25,
    0.2,
    0.2,
    0.25,
    0.5,
    0.9,
    1.6,
    2.4,
    3.2,
    3.6,
    3.8,
    3.5,
    3.4,
    3.3,
    3.4,
    3.8,
    4.4,
    5.0,
    5.2,
    4.6,
    3.4,
    2.0,
    0.9,
)

# Probability splits for the outcome of a payment that did not fail outright.
PENDING_RATE = 0.012
REVERSED_RATE = 0.008

# How much larger a fraudulent payment tends to be than the customer's norm.
FRAUD_AMOUNT_MULTIPLIER = (2.5, 8.0)
FRAUD_UNUSUAL_HOUR_RATE = 0.45
FRAUD_UNFAMILIAR_DEVICE_RATE = 0.6
FRAUD_FOREIGN_RATE = 0.4

# How often a payment comes from something other than the customer's main
# device/IP. Shared devices get their own, higher rate so the sharing signal is
# actually present in the transaction stream and not just in the link table.
SECONDARY_DEVICE_RATE = 0.18
SECONDARY_IP_RATE = 0.22
SHARED_DEVICE_USAGE_RATE = 0.25

_BATCH_SIZE = 2_000


class TransactionRow(TypedDict):
    """A row ready for bulk insertion into ``transactions``."""

    transaction_id: str
    merchant_id: int
    customer_id: int
    device_id: int
    ip_address_id: int
    amount: Decimal
    currency: str
    payment_method: str
    status: str
    transaction_timestamp: datetime
    country: str
    city: str
    failed_attempts: int
    is_fraud: bool
    created_at: datetime


def _allocate_volume(
    config: SeedConfig, plans: list[CustomerPlan], rng: random.Random, budget: int
) -> list[int]:
    """Split ``budget`` transactions across customers by expected activity."""
    months = config.history_days / 30.0
    weights = [rng.uniform(*plan.profile.monthly_rate) * months for plan in plans]
    total_weight = sum(weights)

    counts = [max(1, round(budget * weight / total_weight)) for weight in weights]

    # Correct the rounding drift so the dataset lands on the requested size.
    drift = budget - sum(counts)
    order = sorted(range(len(counts)), key=lambda i: weights[i], reverse=True)
    cursor = 0
    while drift != 0 and order:
        index = order[cursor % len(order)]
        if drift > 0:
            counts[index] += 1
            drift -= 1
        elif counts[index] > 1:
            counts[index] -= 1
            drift += 1
        cursor += 1
    return counts


def _fraud_probabilities(plans: list[CustomerPlan], counts: list[int], rate: float) -> list[float]:
    """Per-customer fraud probability whose volume-weighted mean is ``rate``."""
    total = sum(counts)
    mean_multiplier = (
        sum(
            count * plan.profile.fraud_multiplier for plan, count in zip(plans, counts, strict=True)
        )
        / total
    )
    return [min(0.95, rate * plan.profile.fraud_multiplier / mean_multiplier) for plan in plans]


def _timestamp(
    rng: random.Random, window_start: datetime, window_end: datetime, unusual_hour: bool
) -> datetime:
    """A timestamp in the window, biased towards realistic payment hours."""
    span_seconds = max(1, int((window_end - window_start).total_seconds()))
    base = window_start + timedelta(seconds=rng.randrange(span_seconds))
    hour = (
        rng.choice((1, 2, 3, 4))
        if unusual_hour
        else rng.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
    )
    stamp = base.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60))
    # Shifting the hour can step outside the window at either edge; nudge it
    # back by a whole day so the hour-of-day distribution is preserved.
    if stamp > window_end:
        stamp -= timedelta(days=1)
    elif stamp < window_start:
        stamp += timedelta(days=1)
    return min(max(stamp, window_start), window_end)


def _amount(rng: random.Random, plan: CustomerPlan, is_fraud: bool) -> Decimal:
    low, high = plan.profile.amount_range
    # Log-normal-ish: most payments near the low end, a long thin tail.
    value = low + (high - low) * rng.random() ** 1.7
    if is_fraud:
        value *= rng.uniform(*FRAUD_AMOUNT_MULTIPLIER)
    return Decimal(str(round(max(1.0, value), 2)))


def _location(rng: random.Random, plan: CustomerPlan, is_fraud: bool) -> tuple[str, str]:
    foreign_rate = FRAUD_FOREIGN_RATE if is_fraud else plan.profile.foreign_rate
    travel_rate = 0.5 if is_fraud else plan.profile.travel_rate
    roll = rng.random()
    if roll < foreign_rate:
        location = rng.choice(INTERNATIONAL_LOCATIONS)
        return location.country, location.city
    if roll < foreign_rate + travel_rate:
        location = rng.choice(DOMESTIC_LOCATIONS)
        return location.country, location.city
    return plan.home.country, plan.home.city


def _generate_for_customer(
    config: SeedConfig,
    rng: random.Random,
    plan: CustomerPlan,
    count: int,
    fraud_probability: float,
) -> list[TransactionRow]:
    """Generate one customer's chronological history."""
    window_start = max(config.history_start, plan.customer.account_created_at)
    if window_start >= config.reference_time:
        window_start = config.history_start

    fraud_flags = [rng.random() < fraud_probability for _ in range(count)]
    stamps = sorted(
        _timestamp(
            rng,
            window_start,
            config.reference_time,
            is_fraud and rng.random() < FRAUD_UNUSUAL_HOUR_RATE,
        )
        for is_fraud in fraud_flags
    )

    primary_device = plan.device_ids[0]
    primary_ip = plan.ip_ids[0]
    rows: list[TransactionRow] = []
    consecutive_failures = 0

    for stamp, is_fraud in zip(stamps, fraud_flags, strict=True):
        unfamiliar = is_fraud and rng.random() < FRAUD_UNFAMILIAR_DEVICE_RATE
        secondary_devices = plan.device_ids[1:]
        if plan.shared_device_id is not None and rng.random() < SHARED_DEVICE_USAGE_RATE:
            device_id = plan.shared_device_id
        elif secondary_devices and (unfamiliar or rng.random() < SECONDARY_DEVICE_RATE):
            device_id = rng.choice(secondary_devices)
        else:
            device_id = primary_device

        secondary_ips = plan.ip_ids[1:]
        ip_id = (
            rng.choice(secondary_ips)
            if secondary_ips and (unfamiliar or rng.random() < SECONDARY_IP_RATE)
            else primary_ip
        )

        failure_rate = plan.profile.failure_rate * (2.0 if is_fraud else 1.0)
        roll = rng.random()
        if roll < failure_rate:
            status = TransactionStatus.FAILED
        elif roll < failure_rate + PENDING_RATE:
            status = TransactionStatus.PENDING
        elif roll < failure_rate + PENDING_RATE + REVERSED_RATE:
            status = TransactionStatus.REVERSED
        else:
            status = TransactionStatus.SUCCESSFUL

        country, city = _location(rng, plan, is_fraud)

        rows.append(
            TransactionRow(
                transaction_id="",  # assigned once the whole stream is ordered
                merchant_id=plan.customer.merchant_id,
                customer_id=plan.customer.id,
                device_id=device_id,
                ip_address_id=ip_id,
                amount=_amount(rng, plan, is_fraud),
                currency=DATASET_CURRENCY,
                payment_method=rng.choice(plan.profile.payment_methods).value,
                status=status.value,
                transaction_timestamp=stamp,
                country=country,
                city=city,
                failed_attempts=consecutive_failures,
                is_fraud=is_fraud,
                created_at=stamp,
            )
        )
        consecutive_failures = consecutive_failures + 1 if status is TransactionStatus.FAILED else 0

    return rows


def build_transactions(
    session: Session, config: SeedConfig, rng: random.Random, plans: list[CustomerPlan], budget: int
) -> int:
    """Generate and bulk-insert the background transaction stream.

    Returns the number of rows written. Demo scenarios are added separately and
    receive their own identifiers.
    """
    counts = _allocate_volume(config, plans, rng, budget)
    probabilities = _fraud_probabilities(plans, counts, config.fraud_rate)

    rows: list[TransactionRow] = []
    for plan, count, probability in zip(plans, counts, probabilities, strict=True):
        rows.extend(_generate_for_customer(config, rng, plan, count, probability))

    # Chronological identifiers make the dataset far easier to reason about.
    rows.sort(key=lambda row: row["transaction_timestamp"])
    for sequence, row in enumerate(rows, start=1):
        row["transaction_id"] = f"txn_{sequence:08d}"

    for start in range(0, len(rows), _BATCH_SIZE):
        session.execute(insert(Transaction), rows[start : start + _BATCH_SIZE])
    session.flush()

    fraud_count = sum(1 for row in rows if row["is_fraud"])
    logger.info(
        "Generated %d background transactions (%d labelled fraud, %.2f%%)",
        len(rows),
        fraud_count,
        100.0 * fraud_count / max(1, len(rows)),
    )
    return len(rows)
