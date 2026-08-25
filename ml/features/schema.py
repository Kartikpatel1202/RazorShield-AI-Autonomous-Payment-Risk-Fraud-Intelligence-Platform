"""The feature contract: names, order, types and forbidden columns.

``FEATURE_VERSION`` is stamped into every dataset and every model artifact. A
model refuses to score features built by a different version, so a pipeline
change can never be silently served against an old model.

The lists here are the authoritative order. ``tests/test_feature_schema.py``
asserts that {builder output} == {schema}, so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Any

FEATURE_VERSION = "v1"

#: Continuous, ordinal and boolean-as-integer features, in model input order.
NUMERIC_FEATURES: tuple[str, ...] = (
    "amount",
    "log_amount",
    "hour_of_day",
    "day_of_week",
    "is_weekend",
    "is_night",
    "failed_attempts",
    "customer_account_age_days",
    "customer_has_history",
    "customer_is_first_transaction",
    "previous_transaction_count",
    "previous_success_count",
    "previous_failure_count",
    "historical_failure_rate",
    "historical_success_rate",
    "historical_average_amount",
    "historical_amount_std",
    "historical_max_amount",
    "amount_vs_historical_average",
    "amount_vs_historical_max",
    "amount_zscore_vs_history",
    "amount_above_historical_max",
    "seconds_since_previous_transaction",
    "customer_history_span_days",
    "transactions_last_5m",
    "transactions_last_1h",
    "transactions_last_24h",
    "transactions_last_7d",
    "failed_transactions_last_1h",
    "failed_transactions_last_24h",
    "amount_last_1h",
    "amount_last_24h",
    "failure_rate_last_1h",
    "amount_vs_amount_last_24h",
    "has_device",
    "is_new_device",
    "is_new_device_for_customer",
    "device_age_hours",
    "device_transaction_count",
    "device_customer_count",
    "device_is_shared",
    "device_transactions_last_1h",
    "device_transactions_last_24h",
    "device_failed_last_1h",
    "device_failure_rate_last_1h",
    "has_ip",
    "is_new_ip",
    "is_new_ip_for_customer",
    "ip_age_hours",
    "ip_transaction_count",
    "ip_customer_count",
    "ip_is_shared",
    "ip_transactions_last_1h",
    "ip_transactions_last_24h",
    "ip_failed_last_1h",
    "ip_failure_rate_last_1h",
    "ip_reputation_score",
    "ip_is_proxy",
    "country_changed",
    "city_changed",
    "location_changed",
    "is_home_country",
    "is_home_city",
    "previous_country_count",
    "previous_city_count",
    "country_frequency",
    "city_frequency",
    "is_new_country_for_customer",
    "is_new_city_for_customer",
    "ip_country_matches_transaction",
)

#: String features, one-hot encoded by the model pipelines.
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "payment_method",
    "merchant_category",
    "device_type",
    "transaction_country",
)

#: Full model input, in order. Anything outside this tuple never reaches a model.
FEATURE_COLUMNS: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

#: Kept beside the features for joins, auditing and chronological splitting -
#: never fed to a model.
METADATA_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "transaction_db_id",
    "merchant_id",
    "customer_id",
    "device_id",
    "ip_address_id",
    "transaction_timestamp",
    "status",
)

#: The prediction target.
TARGET_COLUMN = "is_fraud"

#: Columns that must never appear in ``FEATURE_COLUMNS``.
#:
#: The identifiers would let a model memorise individual entities instead of
#: learning behaviour. ``is_fraud`` is the label. ``status`` is the outcome of
#: the very transaction being scored - known only after it is processed, so
#: using it would be scoring the answer. The remaining columns are aggregates
#: the seed generator recomputes across the *entire* dataset, which means at any
#: time T they already encode the future; their point-in-time equivalents are
#: rebuilt from the transaction stream instead.
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "is_fraud",
        "status",
        "transaction_id",
        "transaction_db_id",
        "customer_id",
        "device_id",
        "ip_address_id",
        "merchant_id",
        "average_transaction_amount",
        "successful_transaction_count",
        "failed_transaction_count",
        "chargeback_count",
        "historical_risk_level",
        "is_trusted",
        "device_first_seen_at",
        "device_last_seen_at",
        "ip_first_seen_at",
        "ip_last_seen_at",
    }
)


class FeatureSchemaError(ValueError):
    """A feature row does not match the declared schema."""


def validate_row(row: dict[str, Any]) -> None:
    """Check one feature row against the contract.

    Raises :class:`FeatureSchemaError` on a missing, unexpected or forbidden
    column so a broken pipeline fails loudly instead of silently degrading a
    prediction.
    """
    produced = set(row)
    expected = set(FEATURE_COLUMNS)

    missing = expected - produced
    if missing:
        raise FeatureSchemaError(f"missing feature(s): {sorted(missing)}")

    unexpected = produced - expected
    if unexpected:
        raise FeatureSchemaError(f"unexpected feature(s): {sorted(unexpected)}")

    leaked = produced & FORBIDDEN_FEATURES
    if leaked:
        raise FeatureSchemaError(f"forbidden column(s) present as features: {sorted(leaked)}")


def ordered_values(row: dict[str, Any]) -> list[Any]:
    """Feature values in ``FEATURE_COLUMNS`` order."""
    return [row[name] for name in FEATURE_COLUMNS]
