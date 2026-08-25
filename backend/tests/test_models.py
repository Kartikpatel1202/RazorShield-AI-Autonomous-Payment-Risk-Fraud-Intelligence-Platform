"""ORM schema, relationship and constraint tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AnalystDecision,
    AuditLog,
    Customer,
    CustomerDevice,
    Device,
    Investigation,
    IpAddress,
    Merchant,
    ModelFeedback,
    ReviewCase,
    RiskPrediction,
    RiskRule,
    RiskSignal,
    Transaction,
    User,
)
from app.models.enums import PaymentMethod, TransactionStatus

EXPECTED_TABLES = {
    "users",
    "merchants",
    "customers",
    "devices",
    "customer_devices",
    "ip_addresses",
    "transactions",
    "risk_predictions",
    "risk_signals",
    "risk_decisions",
    "risk_events",
    "investigations",
    "review_cases",
    "analyst_decisions",
    "risk_rules",
    "audit_logs",
    "model_feedback",
    "analyst_feedback",
}

# Populated only by later phases; Phase 2 must leave them empty.
DEFERRED_MODELS = (
    RiskPrediction,
    RiskSignal,
    Investigation,
    ReviewCase,
    AnalystDecision,
    RiskRule,
    AuditLog,
    ModelFeedback,
)


def test_every_expected_table_is_mapped() -> None:
    from app.models import Base

    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_transaction_has_no_risk_score_column() -> None:
    """Risk scoring belongs to Phase 3; the transaction table must stay neutral."""
    columns = set(Transaction.__table__.columns.keys())
    assert not columns & {"risk_score", "fraud_probability", "risk_level"}


@pytest.mark.parametrize("model", DEFERRED_MODELS)
def test_later_phase_tables_are_empty_after_seeding(db_session: Session, model: type) -> None:
    assert db_session.scalar(select(func.count()).select_from(model)) == 0


def test_transaction_resolves_all_four_relationships(db_session: Session) -> None:
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.device_id.is_not(None)).limit(1)
    ).one()

    assert isinstance(transaction.merchant, Merchant)
    assert isinstance(transaction.customer, Customer)
    assert isinstance(transaction.device, Device)
    assert isinstance(transaction.ip_address_record, IpAddress)
    assert transaction.customer.merchant_id == transaction.merchant_id


def test_merchant_owns_customers_which_own_transactions(db_session: Session) -> None:
    merchant = db_session.scalars(select(Merchant).limit(1)).one()
    assert merchant.customers

    customer = merchant.customers[0]
    assert customer.merchant is merchant
    for transaction in customer.transactions:
        assert transaction.customer_id == customer.id
        assert transaction.merchant_id == merchant.id


def test_device_customer_association_is_many_to_many(db_session: Session) -> None:
    link = db_session.scalars(select(CustomerDevice).limit(1)).one()
    device = db_session.get(Device, link.device_id)
    customer = db_session.get(Customer, link.customer_id)
    assert device is not None and customer is not None

    assert customer in device.customers
    assert device in customer.devices


def test_users_cover_every_required_role(db_session: Session) -> None:
    roles = set(db_session.scalars(select(User.role)))
    assert {"merchant", "risk_analyst", "admin"} <= {str(role) for role in roles}


def test_seed_never_stores_a_password_hash(db_session: Session) -> None:
    hashes = list(db_session.scalars(select(User.password_hash)))
    assert hashes and all(value is None for value in hashes)


def _new_transaction(db_session: Session, **overrides: object) -> Transaction:
    template = db_session.scalars(select(Transaction).limit(1)).one()
    values: dict[str, object] = {
        "transaction_id": "txn_constraint_probe",
        "merchant_id": template.merchant_id,
        "customer_id": template.customer_id,
        "amount": Decimal("100.00"),
        "currency": "INR",
        "payment_method": PaymentMethod.CARD,
        "status": TransactionStatus.SUCCESSFUL,
        "transaction_timestamp": datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        "country": "IN",
        "city": "Pune",
    }
    values.update(overrides)
    return Transaction(**values)


def test_non_positive_amount_is_rejected(db_session: Session) -> None:
    db_session.add(_new_transaction(db_session, amount=Decimal("0.00")))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_transaction_reference_is_rejected(db_session: Session) -> None:
    existing = db_session.scalars(select(Transaction).limit(1)).one()
    db_session.add(_new_transaction(db_session, transaction_id=existing.transaction_id))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_timestamps_round_trip_as_utc_aware(db_session: Session) -> None:
    """SQLite drops tzinfo; the column type must put it back."""
    transaction = db_session.scalars(select(Transaction).limit(1)).one()
    assert transaction.transaction_timestamp.tzinfo is not None
    assert transaction.created_at.tzinfo is not None

    customer = db_session.scalars(select(Customer).limit(1)).one()
    assert customer.account_created_at.tzinfo is not None
