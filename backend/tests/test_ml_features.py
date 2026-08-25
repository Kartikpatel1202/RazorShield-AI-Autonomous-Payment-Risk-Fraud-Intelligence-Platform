"""Unit tests for the feature calculations.

These build history objects by hand rather than through a database, so each
assertion pins down one arithmetic rule exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ml.features import (
    customer_features,
    device_features,
    ip_features,
    location_features,
    velocity_features,
)
from ml.features.accumulator import HistoryAccumulator
from ml.features.builder import build_features
from ml.features.history import (
    CustomerHistory,
    CustomerProfile,
    DeviceProfile,
    EntityHistory,
    HistoryWindow,
    IpProfile,
    TransactionView,
)
from ml.features.schema import FEATURE_COLUMNS, validate_row
from ml.features.transaction_features import MerchantProfile

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def make_transaction(**overrides: object) -> TransactionView:
    values: dict[str, object] = {
        "id": 100,
        "transaction_id": "txn_test",
        "merchant_id": 1,
        "customer_id": 10,
        "device_id": 20,
        "ip_address_id": 30,
        "amount": 5_000.0,
        "currency": "INR",
        "payment_method": "card",
        "status": "pending",
        "timestamp": NOW,
        "country": "IN",
        "city": "Pune",
        "failed_attempts": 0,
        "is_fraud": False,
    }
    values.update(overrides)
    return TransactionView(**values)  # type: ignore[arg-type]


PROFILE = CustomerProfile(
    account_created_at=NOW - timedelta(days=200), home_country="IN", home_city="Pune"
)


# --- schema contract --------------------------------------------------------


def test_builder_output_matches_the_declared_schema() -> None:
    """The builder and the schema must never drift apart."""
    window = HistoryWindow(
        customer_profile=PROFILE,
        customer=CustomerHistory(),
        device_profile=DeviceProfile("android"),
        device=EntityHistory(),
        ip_profile=IpProfile(80.0, False, "IN", "Pune"),
        ip=EntityHistory(),
    )
    row = build_features(make_transaction(), window, MerchantProfile("retail"))

    validate_row(row)
    assert set(row) == set(FEATURE_COLUMNS)


# --- customer features ------------------------------------------------------


def test_customer_history_statistics() -> None:
    history = CustomerHistory(
        transaction_count=4,
        success_count=3,
        failure_count=1,
        amount_sum=8_000.0,
        amount_square_sum=20_000_000.0,
        amount_max=4_000.0,
        first_transaction_at=NOW - timedelta(days=30),
        last_transaction_at=NOW - timedelta(hours=2),
    )
    row = customer_features.build(make_transaction(amount=4_000.0), PROFILE, history)

    assert row["previous_transaction_count"] == 4
    assert row["historical_average_amount"] == pytest.approx(2_000.0)
    assert row["historical_failure_rate"] == pytest.approx(0.25)
    assert row["historical_success_rate"] == pytest.approx(0.75)
    assert row["amount_vs_historical_average"] == pytest.approx(2.0)
    assert row["amount_vs_historical_max"] == pytest.approx(1.0)
    assert row["customer_account_age_days"] == pytest.approx(200.0)
    assert row["seconds_since_previous_transaction"] == pytest.approx(7_200.0)
    assert row["customer_has_history"] == 1


def test_amount_standard_deviation_needs_two_observations() -> None:
    """One prior payment has no spread; inventing one would be fabrication."""
    single = CustomerHistory(transaction_count=1, amount_sum=100.0, amount_square_sum=10_000.0)
    assert single.amount_std == 0.0

    pair = CustomerHistory(transaction_count=2, amount_sum=300.0, amount_square_sum=50_000.0)
    # mean 150, E[x^2] 25000 -> variance 2500 -> std 50
    assert pair.amount_std == pytest.approx(50.0)


def test_missing_customer_history_uses_explicit_zeros_and_a_flag() -> None:
    row = customer_features.build(make_transaction(), PROFILE, CustomerHistory())

    assert row["customer_is_first_transaction"] == 1
    assert row["customer_has_history"] == 0
    assert row["previous_transaction_count"] == 0
    # Ratios are undefined with no baseline; 0.0 plus the flag beats a guess.
    assert row["amount_vs_historical_average"] == 0.0
    assert row["amount_zscore_vs_history"] == 0.0
    assert row["seconds_since_previous_transaction"] == 0.0
    assert row["amount_above_historical_max"] == 0


def test_amount_above_historical_max_requires_history() -> None:
    with_history = CustomerHistory(transaction_count=3, amount_max=1_000.0)
    row = customer_features.build(make_transaction(amount=5_000.0), PROFILE, with_history)
    assert row["amount_above_historical_max"] == 1


# --- velocity ---------------------------------------------------------------


def test_velocity_features_read_the_window_counts() -> None:
    history = CustomerHistory(
        transaction_count=9,
        counts={"5m": 2, "1h": 4, "24h": 7, "7d": 9},
        failed_counts={"5m": 1, "1h": 3, "24h": 4, "7d": 4},
        amounts={"5m": 500.0, "1h": 2_000.0, "24h": 10_000.0, "7d": 15_000.0},
    )
    row = velocity_features.build(make_transaction(amount=5_000.0), history)

    assert row["transactions_last_5m"] == 2
    assert row["transactions_last_1h"] == 4
    assert row["transactions_last_24h"] == 7
    assert row["transactions_last_7d"] == 9
    assert row["failed_transactions_last_1h"] == 3
    assert row["amount_last_24h"] == pytest.approx(10_000.0)
    assert row["failure_rate_last_1h"] == pytest.approx(0.75)
    assert row["amount_vs_amount_last_24h"] == pytest.approx(0.5)


def test_velocity_defaults_to_zero_without_history() -> None:
    row = velocity_features.build(make_transaction(), CustomerHistory())
    for window in ("5m", "1h", "24h", "7d"):
        assert row[f"transactions_last_{window}"] == 0
    assert row["failure_rate_last_1h"] == 0.0


# --- device -----------------------------------------------------------------


def test_device_features_expose_sharing_and_age() -> None:
    history = EntityHistory(
        transaction_count=12,
        distinct_customers=3,
        first_seen_at=NOW - timedelta(hours=6),
        counts={"1h": 5, "24h": 12},
        failed_counts={"1h": 2, "24h": 3},
        customer_used_before=True,
    )
    row = device_features.build(make_transaction(), DeviceProfile("web_desktop"), history)

    assert row["has_device"] == 1
    assert row["device_type"] == "web_desktop"
    assert row["is_new_device"] == 0
    assert row["is_new_device_for_customer"] == 0
    assert row["device_age_hours"] == pytest.approx(6.0)
    assert row["device_customer_count"] == 3
    assert row["device_is_shared"] == 1
    assert row["device_failure_rate_last_1h"] == pytest.approx(0.4)


def test_unseen_device_is_flagged_new() -> None:
    row = device_features.build(make_transaction(), DeviceProfile("ios"), EntityHistory())
    assert row["is_new_device"] == 1
    assert row["is_new_device_for_customer"] == 1
    assert row["device_age_hours"] == 0.0


def test_absent_device_falls_back_explicitly() -> None:
    row = device_features.build(make_transaction(device_id=None), None, None)
    assert row["has_device"] == 0
    assert row["device_type"] == "unknown"
    assert row["device_customer_count"] == 0


# --- IP ---------------------------------------------------------------------


def test_ip_features_use_the_stored_reputation() -> None:
    history = EntityHistory(
        transaction_count=8,
        distinct_customers=4,
        first_seen_at=NOW - timedelta(hours=2),
        counts={"1h": 6, "24h": 8},
        failed_counts={"1h": 3, "24h": 3},
        customer_used_before=False,
    )
    row = ip_features.build(make_transaction(), IpProfile(11.5, True, "SG", "Singapore"), history)

    assert row["ip_reputation_score"] == pytest.approx(11.5)
    assert row["ip_is_proxy"] == 1
    assert row["ip_is_shared"] == 1
    assert row["ip_customer_count"] == 4
    assert row["is_new_ip_for_customer"] == 1
    assert row["ip_age_hours"] == pytest.approx(2.0)


def test_absent_ip_uses_a_neutral_reputation() -> None:
    row = ip_features.build(make_transaction(ip_address_id=None), None, None)
    assert row["has_ip"] == 0
    assert row["ip_reputation_score"] == ip_features.UNKNOWN_REPUTATION


# --- location ---------------------------------------------------------------


def test_location_change_is_measured_against_the_previous_transaction() -> None:
    history = CustomerHistory(
        transaction_count=10,
        country_counts={"IN": 9, "SG": 1},
        city_counts={"Pune": 8, "Mumbai": 1, "Singapore": 1},
        last_country="IN",
        last_city="Pune",
    )
    row = location_features.build(
        make_transaction(country="SG", city="Singapore"),
        PROFILE,
        history,
        IpProfile(20.0, True, "SG", "Singapore"),
    )

    assert row["country_changed"] == 1
    assert row["city_changed"] == 1
    assert row["location_changed"] == 1
    assert row["is_home_country"] == 0
    assert row["previous_country_count"] == 2
    assert row["country_frequency"] == pytest.approx(0.1)
    assert row["is_new_country_for_customer"] == 0
    assert row["ip_country_matches_transaction"] == 1


def test_first_transaction_has_not_changed_location() -> None:
    row = location_features.build(make_transaction(), PROFILE, CustomerHistory(), None)
    assert row["country_changed"] == 0
    assert row["city_changed"] == 0
    assert row["location_changed"] == 0
    assert row["is_new_country_for_customer"] == 0


# --- accumulator ------------------------------------------------------------


def test_accumulator_snapshot_precedes_observation() -> None:
    accumulator = HistoryAccumulator()
    first = make_transaction(id=1, timestamp=NOW - timedelta(hours=1), amount=1_000.0)
    second = make_transaction(id=2, timestamp=NOW, amount=3_000.0)

    assert accumulator.customer_snapshot(first).transaction_count == 0
    accumulator.observe(first)

    snapshot = accumulator.customer_snapshot(second)
    assert snapshot.transaction_count == 1
    assert snapshot.mean_amount == pytest.approx(1_000.0)
    assert snapshot.counts["24h"] == 1


def test_accumulator_rejects_out_of_order_transactions() -> None:
    """Out-of-order input would silently break the point-in-time guarantee."""
    accumulator = HistoryAccumulator()
    accumulator.observe(make_transaction(id=2, timestamp=NOW))

    with pytest.raises(ValueError, match="ascending"):
        accumulator.observe(make_transaction(id=1, timestamp=NOW - timedelta(hours=1)))


def test_accumulator_windows_expire() -> None:
    accumulator = HistoryAccumulator()
    accumulator.observe(make_transaction(id=1, timestamp=NOW - timedelta(days=3)))
    accumulator.observe(make_transaction(id=2, timestamp=NOW - timedelta(minutes=2)))

    snapshot = accumulator.customer_snapshot(make_transaction(id=3, timestamp=NOW))
    assert snapshot.transaction_count == 2
    assert snapshot.counts["5m"] == 1
    assert snapshot.counts["24h"] == 1
    assert snapshot.counts["7d"] == 2


def test_accumulator_tracks_distinct_customers_per_device() -> None:
    accumulator = HistoryAccumulator()
    for index, customer_id in enumerate((10, 11, 12), start=1):
        accumulator.observe(
            make_transaction(
                id=index, customer_id=customer_id, timestamp=NOW - timedelta(minutes=10 - index)
            )
        )

    snapshot = accumulator.device_snapshot(make_transaction(id=99, customer_id=13, timestamp=NOW))
    assert snapshot is not None
    assert snapshot.distinct_customers == 3
    assert snapshot.customer_used_before is False
