"""Temporal leakage tests - the most important tests in the ML layer.

A fraud model that can see the future is worthless in production and looks
excellent in evaluation, which is exactly the failure mode these tests exist to
make impossible.

The central assertion: **inserting a transaction that happens after T must not
change any feature of T.** The final test in this file deliberately inserts a
transaction *before* T and asserts the features do change - without it, the
others could pass simply because the feature builder ignores history entirely.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.models.enums import PaymentMethod, TransactionStatus
from ml.features.builder import build_features
from ml.features.loader import get_merchant_profile, to_view
from ml.features.point_in_time import build_history_window
from ml.features.schema import FEATURE_COLUMNS, FORBIDDEN_FEATURES, TARGET_COLUMN


def _subject(session: Session) -> Transaction:
    """A transaction with a real history behind it and entities to share."""
    return session.scalars(
        select(Transaction)
        .where(Transaction.device_id.is_not(None), Transaction.ip_address_id.is_not(None))
        .order_by(Transaction.transaction_timestamp.asc())
        .offset(400)
        .limit(1)
    ).one()


def _features(session: Session, transaction: Transaction) -> dict:
    view = to_view(transaction)
    window = build_history_window(session, view)
    return build_features(view, window, get_merchant_profile(session, view.merchant_id))


def _insert_like(
    session: Session,
    template: Transaction,
    *,
    reference: str,
    offset: timedelta,
    amount: str = "99000.00",
    status: TransactionStatus = TransactionStatus.FAILED,
) -> Transaction:
    """Insert a transaction sharing the subject's customer, device and IP."""
    row = Transaction(
        transaction_id=reference,
        merchant_id=template.merchant_id,
        customer_id=template.customer_id,
        device_id=template.device_id,
        ip_address_id=template.ip_address_id,
        amount=Decimal(amount),
        currency="INR",
        payment_method=PaymentMethod.CARD,
        status=status,
        transaction_timestamp=template.transaction_timestamp + offset,
        country="SG",
        city="Singapore",
        failed_attempts=0,
        is_fraud=True,
    )
    session.add(row)
    session.flush()
    return row


def test_a_later_transaction_does_not_change_earlier_features(db_session: Session) -> None:
    """The mandatory anti-cheating test."""
    subject = _subject(db_session)
    before = _features(db_session, subject)

    _insert_like(db_session, subject, reference="txn_future_probe", offset=timedelta(minutes=30))

    after = _features(db_session, subject)
    assert after == before, "a future transaction leaked into an earlier transaction's features"


def test_many_later_transactions_do_not_change_earlier_features(db_session: Session) -> None:
    """A whole burst of later activity must still be invisible."""
    subject = _subject(db_session)
    before = _features(db_session, subject)

    for index in range(10):
        _insert_like(
            db_session,
            subject,
            reference=f"txn_future_burst_{index}",
            offset=timedelta(minutes=2 * (index + 1)),
        )

    assert _features(db_session, subject) == before


def test_a_transaction_at_the_same_instant_with_a_higher_id_is_not_history(
    db_session: Session,
) -> None:
    """Ties are broken by id, so a same-timestamp row inserted later is 'after'."""
    subject = _subject(db_session)
    before = _features(db_session, subject)

    _insert_like(db_session, subject, reference="txn_same_instant", offset=timedelta(0))

    assert _features(db_session, subject) == before


def test_a_transaction_never_counts_itself(db_session: Session) -> None:
    """Velocity and history exclude the transaction being scored."""
    subject = _subject(db_session)
    features = _features(db_session, subject)

    solo = db_session.scalars(
        select(Transaction)
        .where(Transaction.customer_id == subject.customer_id)
        .order_by(Transaction.transaction_timestamp.asc())
        .limit(1)
    ).one()
    first_features = _features(db_session, solo)

    assert first_features["previous_transaction_count"] == 0
    assert first_features["transactions_last_24h"] == 0
    assert first_features["customer_is_first_transaction"] == 1
    # The subject sits deep in its customer's history, so it must see some.
    assert features["previous_transaction_count"] > 0


def test_the_label_is_never_a_feature() -> None:
    assert TARGET_COLUMN not in FEATURE_COLUMNS
    assert not set(FEATURE_COLUMNS) & FORBIDDEN_FEATURES


def test_no_identifier_is_a_feature() -> None:
    """Identifiers would let the model memorise entities instead of behaviour."""
    for identifier in (
        "transaction_id",
        "transaction_db_id",
        "customer_id",
        "device_id",
        "ip_address_id",
        "merchant_id",
    ):
        assert identifier not in FEATURE_COLUMNS


def test_full_dataset_aggregates_are_not_features() -> None:
    """Phase 2 recomputes these across the whole dataset, so they encode the future."""
    for contaminated in (
        "average_transaction_amount",
        "successful_transaction_count",
        "failed_transaction_count",
        "chargeback_count",
        "historical_risk_level",
        "is_trusted",
    ):
        assert contaminated in FORBIDDEN_FEATURES
        assert contaminated not in FEATURE_COLUMNS


def test_the_transactions_own_outcome_is_not_a_feature() -> None:
    """``status`` is known only after processing; using it would score the answer."""
    assert "status" not in FEATURE_COLUMNS
    assert "status" in FORBIDDEN_FEATURES


@pytest.mark.parametrize("minutes_before", [1, 45])
def test_an_earlier_transaction_does_change_features(
    db_session: Session, minutes_before: int
) -> None:
    """Proves the leakage tests above are actually sensitive.

    If the feature builder ignored history, every test in this file would pass
    vacuously. Inserting a transaction *before* the subject must move its
    features.
    """
    subject = _subject(db_session)
    before = _features(db_session, subject)

    _insert_like(
        db_session,
        subject,
        reference=f"txn_past_probe_{minutes_before}",
        offset=timedelta(minutes=-minutes_before),
    )

    after = _features(db_session, subject)
    assert after != before, "inserting earlier history had no effect; the test is vacuous"
    assert after["previous_transaction_count"] == before["previous_transaction_count"] + 1
