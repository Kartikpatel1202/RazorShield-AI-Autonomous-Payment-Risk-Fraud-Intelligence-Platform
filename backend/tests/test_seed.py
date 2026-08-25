"""Seed generation: sizing, determinism, sharing patterns and demo scenarios."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, Customer, Device, IpAddress, Merchant, Transaction
from app.models.enums import TransactionStatus
from app.seed import SeedConfig, seed_database
from app.seed import scenarios as scn
from app.seed.validation import SeedValidationError, validate

# Deliberately tiny: this config is seeded from scratch inside a test.
TINY_CONFIG = SeedConfig(
    random_seed=99,
    merchants=2,
    customers=40,
    ip_addresses=60,
    transactions=500,
    history_days=21,
    reference_time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
)


def _seed_into_memory(config: SeedConfig) -> list[tuple]:
    """Seed a throwaway database and return a fingerprint of every transaction."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            seed_database(session, config)
            session.commit()
            return list(
                session.execute(
                    select(
                        Transaction.transaction_id,
                        Transaction.amount,
                        Transaction.status,
                        Transaction.transaction_timestamp,
                        Transaction.city,
                        Transaction.is_fraud,
                    ).order_by(Transaction.transaction_id)
                ).all()
            )
    finally:
        engine.dispose()


# --- sizing -----------------------------------------------------------------


def test_transaction_count_matches_the_requested_size(
    db_session: Session, seed_config: SeedConfig
) -> None:
    total = db_session.scalar(select(func.count(Transaction.id)))
    assert total == seed_config.transactions


def test_population_counts_match_the_configuration(
    db_session: Session, seed_config: SeedConfig
) -> None:
    assert db_session.scalar(select(func.count(Merchant.id))) == seed_config.merchants
    # The demo scenarios add their own customers on top of the generated ones.
    customers = db_session.scalar(select(func.count(Customer.id)))
    assert customers == seed_config.customers + len(scn.CUSTOMERS_FRAUD) + 2


def test_every_customer_has_at_least_one_transaction(db_session: Session) -> None:
    customers_without_transactions = db_session.scalar(
        select(func.count(Customer.id)).where(
            ~Customer.id.in_(select(Transaction.customer_id).distinct())
        )
    )
    assert customers_without_transactions == 0


def test_every_device_has_observed_activity(db_session: Session) -> None:
    """Unused fingerprints are pruned, so no device carries invented timestamps."""
    idle = db_session.scalar(
        select(func.count(Device.id)).where(
            ~Device.id.in_(select(Transaction.device_id).where(Transaction.device_id.is_not(None)))
        )
    )
    assert idle == 0


# --- distribution -----------------------------------------------------------


def test_fraud_is_a_small_minority(db_session: Session) -> None:
    total = db_session.scalar(select(func.count(Transaction.id))) or 0
    fraud = (
        db_session.scalar(select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True)))
        or 0
    )
    assert 0 < fraud / total < 0.10, "fraud must be rare, not a 50/50 split"


def test_transactions_span_the_whole_history_window(
    db_session: Session, seed_config: SeedConfig
) -> None:
    earliest, latest = db_session.execute(
        select(
            func.min(Transaction.transaction_timestamp), func.max(Transaction.transaction_timestamp)
        )
    ).one()

    assert earliest >= seed_config.history_start - timedelta(days=1)
    assert latest <= seed_config.reference_time
    # Data must be spread out, not stacked at one instant.
    assert (latest - earliest) > timedelta(days=seed_config.history_days / 2)


def test_recent_activity_exists_for_velocity_windows(
    db_session: Session, seed_config: SeedConfig
) -> None:
    recent = db_session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.transaction_timestamp >= seed_config.reference_time - timedelta(hours=1)
        )
    )
    assert recent > 0


def test_all_four_transaction_statuses_occur(db_session: Session) -> None:
    statuses = {str(value) for value in db_session.scalars(select(Transaction.status).distinct())}
    assert statuses == {status.value for status in TransactionStatus}


# --- determinism ------------------------------------------------------------


def test_the_same_seed_reproduces_the_same_dataset() -> None:
    first = _seed_into_memory(TINY_CONFIG)
    second = _seed_into_memory(TINY_CONFIG)
    assert first == second
    assert first, "the fingerprint must not be empty"


def test_a_different_seed_produces_a_different_dataset() -> None:
    from dataclasses import replace

    baseline = _seed_into_memory(TINY_CONFIG)
    altered = _seed_into_memory(replace(TINY_CONFIG, random_seed=TINY_CONFIG.random_seed + 1))
    assert baseline != altered


# --- sharing ----------------------------------------------------------------


def _entities_shared_by_multiple_customers(db_session: Session, column) -> int:  # noqa: ANN001
    rows = db_session.execute(
        select(column)
        .where(column.is_not(None))
        .group_by(column)
        .having(func.count(func.distinct(Transaction.customer_id)) > 1)
    ).all()
    return len(rows)


def test_some_devices_are_shared_between_customers(db_session: Session) -> None:
    assert _entities_shared_by_multiple_customers(db_session, Transaction.device_id) >= 5


def test_most_devices_belong_to_a_single_customer(db_session: Session) -> None:
    """Sharing is only a signal if it is the exception."""
    total = db_session.scalar(select(func.count(Device.id))) or 0
    shared = _entities_shared_by_multiple_customers(db_session, Transaction.device_id)
    assert shared / total < 0.25


def test_some_ip_addresses_are_shared_between_customers(db_session: Session) -> None:
    assert _entities_shared_by_multiple_customers(db_session, Transaction.ip_address_id) >= 20


# --- demo scenarios ---------------------------------------------------------


def _customer(db_session: Session, external_id: str) -> Customer:
    return db_session.scalars(
        select(Customer).where(Customer.external_customer_id == external_id)
    ).one()


def _transaction(db_session: Session, reference: str) -> Transaction:
    return db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == reference)
    ).one()


def test_scenario_a_is_an_ordinary_payment(db_session: Session) -> None:
    customer = _customer(db_session, scn.CUSTOMER_NORMAL)
    current = _transaction(db_session, scn.TXN_SCENARIO_A_CURRENT)

    assert current.customer_id == customer.id
    assert current.is_fraud is False
    assert current.city == customer.city
    assert current.country == customer.country
    # Known device, known IP, settled history.
    assert customer.successful_transaction_count >= scn.NORMAL_HISTORY_COUNT
    devices = db_session.scalar(
        select(func.count(func.distinct(Transaction.device_id))).where(
            Transaction.customer_id == customer.id
        )
    )
    assert devices == 1


def test_scenario_b_evidence_is_derivable_from_the_database(
    db_session: Session, seed_config: SeedConfig
) -> None:
    customer = _customer(db_session, scn.CUSTOMER_SUSPICIOUS)
    current = _transaction(db_session, scn.TXN_SCENARIO_B_CURRENT)

    # 1. The amount is far above the customer's own baseline.
    baseline = db_session.scalar(
        select(func.avg(Transaction.amount)).where(
            Transaction.customer_id == customer.id,
            Transaction.status == TransactionStatus.SUCCESSFUL,
        )
    )
    assert Decimal(str(baseline)) < Decimal("3500")
    assert current.amount == scn.SUSPICIOUS_CURRENT_AMOUNT

    # 2. The device is brand new.
    device = db_session.get(Device, current.device_id)
    assert device is not None
    assert device.device_id == scn.DEVICE_SUSPICIOUS_NEW
    assert device.first_seen_at >= seed_config.reference_time - timedelta(hours=1)

    # 3. The IP is unfamiliar, foreign and poorly reputed.
    ip = db_session.get(IpAddress, current.ip_address_id)
    assert ip is not None
    assert ip.country != customer.country
    assert ip.is_proxy is True

    # 4. Recent failed attempts precede it.
    recent_failures = db_session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.customer_id == customer.id,
            Transaction.status == TransactionStatus.FAILED,
            Transaction.transaction_timestamp >= seed_config.reference_time - timedelta(hours=1),
        )
    )
    assert recent_failures == scn.SUSPICIOUS_FAILED_ATTEMPTS

    # 5. No risk verdict has been stored anywhere.
    assert current.risk_prediction is None
    assert current.risk_signals == []


def test_scenario_c_links_three_customers_through_one_device_and_ip(
    db_session: Session, seed_config: SeedConfig
) -> None:
    device = db_session.scalars(
        select(Device).where(Device.device_id == scn.DEVICE_FRAUD_SHARED)
    ).one()
    ip = db_session.scalars(
        select(IpAddress).where(IpAddress.ip_address == scn.IP_FRAUD_SHARED)
    ).one()

    device_customers = set(
        db_session.scalars(
            select(Transaction.customer_id).where(Transaction.device_id == device.id).distinct()
        )
    )
    ip_customers = set(
        db_session.scalars(
            select(Transaction.customer_id).where(Transaction.ip_address_id == ip.id).distinct()
        )
    )
    expected = {_customer(db_session, name).id for name in scn.CUSTOMERS_FRAUD}

    assert device_customers == expected
    assert ip_customers == expected


def test_scenario_c_burst_is_compressed_in_time(
    db_session: Session, seed_config: SeedConfig
) -> None:
    device = db_session.scalars(
        select(Device).where(Device.device_id == scn.DEVICE_FRAUD_SHARED)
    ).one()
    earliest, latest = db_session.execute(
        select(
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
        ).where(Transaction.device_id == device.id)
    ).one()

    assert (latest - earliest) <= timedelta(hours=1)
    fraud_rows = db_session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.device_id == device.id, Transaction.is_fraud.is_(True)
        )
    )
    assert fraud_rows >= len(scn.CUSTOMERS_FRAUD)


# --- validation -------------------------------------------------------------


def test_validation_passes_on_the_generated_dataset(
    db_session: Session, seed_config: SeedConfig
) -> None:
    validate(db_session, seed_config)


def test_validation_rejects_an_unrecognised_currency(
    db_session: Session, seed_config: SeedConfig
) -> None:
    """Amounts are guarded by a CHECK constraint; currency is not, so the
    validator is the only thing standing between a typo and a bad dataset."""
    transaction = db_session.scalars(select(Transaction).limit(1)).one()
    transaction.currency = "XYZ"
    db_session.flush()

    with pytest.raises(SeedValidationError) as error:
        validate(db_session, seed_config)
    assert any("unrecognised currency" in failure for failure in error.value.failures)


def test_validation_rejects_a_timestamp_outside_the_window(
    db_session: Session, seed_config: SeedConfig
) -> None:
    transaction = db_session.scalars(select(Transaction).limit(1)).one()
    transaction.transaction_timestamp = seed_config.history_start - timedelta(days=400)
    db_session.flush()

    with pytest.raises(SeedValidationError) as error:
        validate(db_session, seed_config)
    assert any("outside the dataset time window" in failure for failure in error.value.failures)


def test_validation_rejects_a_missing_demo_scenario(
    db_session: Session, seed_config: SeedConfig
) -> None:
    db_session.delete(_transaction(db_session, scn.TXN_SCENARIO_B_CURRENT))
    db_session.flush()

    with pytest.raises(SeedValidationError) as error:
        validate(db_session, seed_config)
    assert any("scenario B" in failure for failure in error.value.failures)
