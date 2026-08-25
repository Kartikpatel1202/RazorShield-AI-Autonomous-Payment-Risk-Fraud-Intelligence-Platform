"""The behavioral feature contract for the anomaly engine.

Phase 4 does **not** build a second feature pipeline. It selects an explicit
subset of the Phase 3 point-in-time features (``ml.features``), so every
temporal-leakage guarantee established there applies here unchanged: the
features are still computed strictly from history before the transaction, by the
same two providers, validated by the same parity test.

**Why a subset rather than all 74.** Isolation Forest measures how easily a
point can be isolated by random axis-aligned splits. Every column it is given
contributes to that geometry, so features that describe *what kind* of payment
this is - payment method, merchant category, country, hour of day - would make
an unusual-but-legitimate combination (a card payment from Berlin at 3am) look
structurally identical to genuinely anomalous behaviour. The subset here is
restricted to features that describe *behaviour relative to what came before*:
volume, velocity, spend deviation, entity reach and movement.

Excluded on purpose:

* ``is_fraud`` - the label. Never a feature anywhere in this project.
* identifiers (``transaction_id``, ``customer_id``, ``device_id``,
  ``ip_address_id``, ``merchant_id``) - would let the forest isolate individual
  entities instead of behaviour.
* categoricals (``payment_method``, ``merchant_category``, ``device_type``,
  ``transaction_country``) - one-hot columns are almost all zero, and a random
  split on a sparse indicator isolates rare *categories* rather than rare
  *behaviour*. That is a different question from the one this engine answers.
* calendar features (``hour_of_day``, ``day_of_week``, ``is_weekend``,
  ``is_night``) - a 3am payment is unusual for the population but perfectly
  normal for a night-shift customer; the customer-relative features capture the
  part that matters.
* static attributes (``ip_reputation_score``, ``ip_is_proxy``,
  ``customer_account_age_days``) - properties of an entity, not of the
  behaviour being scored. Reputation in particular is an input the decision
  engine can weigh separately in a later phase.
"""

from __future__ import annotations

from typing import Any

BEHAVIORAL_FEATURE_VERSION = "b1"

#: Spend for this payment, and how far it sits from the customer's own baseline.
TRANSACTION_BEHAVIOR: tuple[str, ...] = (
    "amount",
    "log_amount",
    "amount_vs_historical_average",
    "amount_vs_historical_max",
    "amount_zscore_vs_history",
)

#: What the customer had done before this payment.
CUSTOMER_BEHAVIOR: tuple[str, ...] = (
    "previous_transaction_count",
    "previous_success_count",
    "previous_failure_count",
    "historical_failure_rate",
    "historical_average_amount",
    "historical_amount_std",
    "historical_max_amount",
    "seconds_since_previous_transaction",
)

#: How fast the customer was moving in the run-up to this payment.
VELOCITY_BEHAVIOR: tuple[str, ...] = (
    "transactions_last_5m",
    "transactions_last_1h",
    "transactions_last_24h",
    "transactions_last_7d",
    "failed_transactions_last_1h",
    "failed_transactions_last_24h",
    "amount_last_1h",
    "amount_last_24h",
)

#: How far the paying device reaches, and how hard it has been working.
DEVICE_BEHAVIOR: tuple[str, ...] = (
    "device_transaction_count",
    "device_customer_count",
    "device_transactions_last_1h",
    "device_transactions_last_24h",
    "device_failed_last_1h",
    "device_age_hours",
    "is_new_device",
    "is_new_device_for_customer",
)

#: The same questions for the originating IP address.
IP_BEHAVIOR: tuple[str, ...] = (
    "ip_transaction_count",
    "ip_customer_count",
    "ip_transactions_last_1h",
    "ip_transactions_last_24h",
    "ip_failed_last_1h",
    "ip_age_hours",
    "is_new_ip",
    "is_new_ip_for_customer",
)

#: Movement relative to where this customer normally pays from.
LOCATION_BEHAVIOR: tuple[str, ...] = (
    "location_changed",
    "country_changed",
    "city_changed",
    "country_frequency",
    "city_frequency",
    "is_home_country",
    "is_home_city",
    "previous_country_count",
    "previous_city_count",
    "is_new_country_for_customer",
    "is_new_city_for_customer",
)

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "transaction": TRANSACTION_BEHAVIOR,
    "customer": CUSTOMER_BEHAVIOR,
    "velocity": VELOCITY_BEHAVIOR,
    "device": DEVICE_BEHAVIOR,
    "ip": IP_BEHAVIOR,
    "location": LOCATION_BEHAVIOR,
}

#: The model input, in order. Nothing outside this tuple reaches the forest.
BEHAVIORAL_FEATURES: tuple[str, ...] = tuple(
    name for group in FEATURE_GROUPS.values() for name in group
)

#: Heavy right tails: money amounts and unbounded counts. ``log1p`` compresses
#: them so a single huge payment does not dominate every split, while preserving
#: order and mapping 0 to 0. Ratios, rates, ages and indicators are left alone -
#: they are already bounded or roughly linear, and transforming them would only
#: obscure the scale a human reads them on.
LOG1P_FEATURES: frozenset[str] = frozenset(
    {
        "amount",
        "amount_vs_historical_average",
        "amount_vs_historical_max",
        "historical_average_amount",
        "historical_amount_std",
        "historical_max_amount",
        "amount_last_1h",
        "amount_last_24h",
        "seconds_since_previous_transaction",
        "previous_transaction_count",
        "previous_success_count",
        "previous_failure_count",
        "transactions_last_5m",
        "transactions_last_1h",
        "transactions_last_24h",
        "transactions_last_7d",
        "failed_transactions_last_1h",
        "failed_transactions_last_24h",
        "device_transaction_count",
        "device_customer_count",
        "device_transactions_last_1h",
        "device_transactions_last_24h",
        "device_failed_last_1h",
        "device_age_hours",
        "ip_transaction_count",
        "ip_customer_count",
        "ip_transactions_last_1h",
        "ip_transactions_last_24h",
        "ip_failed_last_1h",
        "ip_age_hours",
        "previous_country_count",
        "previous_city_count",
    }
)

#: ``log_amount`` is already a log transform; applying another would be a bug.
assert "log_amount" not in LOG1P_FEATURES

#: Customer-relative deviation is reported alongside the global anomaly score
#: (see ``ml.anomaly.scoring``). These are the features that answer "unusual for
#: *this* customer" rather than "unusual for the population".
CUSTOMER_RELATIVE_FEATURES: tuple[str, ...] = (
    "amount_vs_historical_average",
    "amount_zscore_vs_history",
    "transactions_last_5m",
    "transactions_last_1h",
    "failed_transactions_last_1h",
)

#: Columns that must never appear in ``BEHAVIORAL_FEATURES``.
FORBIDDEN_BEHAVIORAL_FEATURES: frozenset[str] = frozenset(
    {
        "is_fraud",
        "status",
        "transaction_id",
        "transaction_db_id",
        "customer_id",
        "device_id",
        "ip_address_id",
        "merchant_id",
    }
)


class BehavioralSchemaError(ValueError):
    """A feature row does not match the behavioral contract."""


def select(row: dict[str, Any]) -> dict[str, Any]:
    """Narrow a full Phase 3 feature row to the behavioral subset.

    Raises :class:`BehavioralSchemaError` if the row is missing anything the
    contract requires, so a pipeline change surfaces immediately instead of
    silently degrading a score.
    """
    missing = [name for name in BEHAVIORAL_FEATURES if name not in row]
    if missing:
        raise BehavioralSchemaError(f"missing behavioral feature(s): {missing}")
    return {name: row[name] for name in BEHAVIORAL_FEATURES}


def validate_row(row: dict[str, Any]) -> None:
    """Check a behavioral row: nothing missing, nothing extra, nothing forbidden."""
    produced = set(row)
    expected = set(BEHAVIORAL_FEATURES)

    missing = expected - produced
    if missing:
        raise BehavioralSchemaError(f"missing behavioral feature(s): {sorted(missing)}")

    unexpected = produced - expected
    if unexpected:
        raise BehavioralSchemaError(f"unexpected behavioral feature(s): {sorted(unexpected)}")

    leaked = produced & FORBIDDEN_BEHAVIORAL_FEATURES
    if leaked:
        raise BehavioralSchemaError(f"forbidden column(s) present: {sorted(leaked)}")
