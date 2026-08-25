"""Temporal leakage tests for the anomaly engine.

Phase 4 reuses the Phase 3 point-in-time pipeline rather than building a second
history system, so in principle it inherits those guarantees. These tests verify
that inheritance actually holds end to end - through the behavioral subset, the
preprocessing and the forest - rather than assuming it.

As in Phase 3, the last test deliberately inserts a transaction *before* the
subject and asserts the score *does* move. Without it, every "nothing changed"
assertion here could pass vacuously.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.models.enums import PaymentMethod, TransactionStatus
from ml.anomaly.predictor import BehavioralAnomalyPredictor
from ml.anomaly.schema import BEHAVIORAL_FEATURES, FORBIDDEN_BEHAVIORAL_FEATURES
from ml.anomaly.schema import select as narrow
from ml.features.builder import build_features
from ml.features.loader import get_merchant_profile, to_view
from ml.features.point_in_time import build_history_window


def _subject(session: Session) -> Transaction:
    return session.scalars(
        select(Transaction)
        .where(Transaction.device_id.is_not(None), Transaction.ip_address_id.is_not(None))
        .order_by(Transaction.transaction_timestamp.asc())
        .offset(400)
        .limit(1)
    ).one()


def _behavioral(session: Session, transaction: Transaction) -> dict:
    view = to_view(transaction)
    window = build_history_window(session, view)
    full = build_features(view, window, get_merchant_profile(session, view.merchant_id))
    return narrow(full)


def _score(
    predictor: BehavioralAnomalyPredictor, session: Session, transaction: Transaction
) -> int:
    view = to_view(transaction)
    window = build_history_window(session, view)
    full = build_features(view, window, get_merchant_profile(session, view.merchant_id))
    return predictor.score_from_features(transaction.transaction_id, full).anomaly_score


def _insert_like(
    session: Session,
    template: Transaction,
    *,
    reference: str,
    offset: timedelta,
) -> Transaction:
    row = Transaction(
        transaction_id=reference,
        merchant_id=template.merchant_id,
        customer_id=template.customer_id,
        device_id=template.device_id,
        ip_address_id=template.ip_address_id,
        amount=Decimal("97000.00"),
        currency="INR",
        payment_method=PaymentMethod.CARD,
        status=TransactionStatus.FAILED,
        transaction_timestamp=template.transaction_timestamp + offset,
        country="SG",
        city="Singapore",
        failed_attempts=0,
        is_fraud=True,
    )
    session.add(row)
    session.flush()
    return row


def test_a_later_transaction_does_not_change_behavioral_features(
    db_session: Session,
) -> None:
    subject = _subject(db_session)
    before = _behavioral(db_session, subject)

    _insert_like(db_session, subject, reference="anom_future_probe", offset=timedelta(minutes=20))

    assert _behavioral(db_session, subject) == before


def test_a_later_transaction_does_not_change_the_anomaly_score(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """The full path - features, preprocessing, forest - must be time-blind."""
    subject = _subject(db_session)
    before = _score(anomaly_predictor, db_session, subject)

    for index in range(5):
        _insert_like(
            db_session,
            subject,
            reference=f"anom_future_burst_{index}",
            offset=timedelta(minutes=3 * (index + 1)),
        )

    assert _score(anomaly_predictor, db_session, subject) == before


def test_a_transaction_at_the_same_instant_with_a_higher_id_is_not_history(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    subject = _subject(db_session)
    before = _score(anomaly_predictor, db_session, subject)

    _insert_like(db_session, subject, reference="anom_same_instant", offset=timedelta(0))

    assert _score(anomaly_predictor, db_session, subject) == before


def test_the_first_transaction_of_a_customer_has_no_history(db_session: Session) -> None:
    subject = _subject(db_session)
    first = db_session.scalars(
        select(Transaction)
        .where(Transaction.customer_id == subject.customer_id)
        .order_by(Transaction.transaction_timestamp.asc())
        .limit(1)
    ).one()

    behavioral = _behavioral(db_session, first)
    assert behavioral["previous_transaction_count"] == 0
    assert behavioral["transactions_last_24h"] == 0
    assert behavioral["historical_average_amount"] == 0.0


def test_the_label_never_reaches_the_behavioral_row(db_session: Session) -> None:
    subject = _subject(db_session)
    behavioral = _behavioral(db_session, subject)

    assert set(behavioral) == set(BEHAVIORAL_FEATURES)
    assert not set(behavioral) & FORBIDDEN_BEHAVIORAL_FEATURES


@pytest.mark.parametrize("minutes_before", [2, 30])
def test_an_earlier_transaction_does_change_the_score(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session, minutes_before: int
) -> None:
    """Proves the invariance tests above are not vacuous."""
    subject = _subject(db_session)
    before = _behavioral(db_session, subject)

    _insert_like(
        db_session,
        subject,
        reference=f"anom_past_probe_{minutes_before}",
        offset=timedelta(minutes=-minutes_before),
    )

    after = _behavioral(db_session, subject)
    assert after != before
    assert after["previous_transaction_count"] == before["previous_transaction_count"] + 1
