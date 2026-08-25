"""Model artifact, predictor and persistence tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import joblib
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RiskPrediction as RiskPredictionRow
from app.models import Transaction
from app.services.risk import store_prediction
from ml.features.builder import build_features
from ml.features.loader import get_merchant_profile, to_view
from ml.features.point_in_time import build_history_window
from ml.features.schema import FEATURE_COLUMNS, FEATURE_VERSION, FeatureSchemaError
from ml.inference.predictor import (
    FraudRiskPredictor,
    ModelContractError,
    ModelNotAvailableError,
    get_predictor,
    reset_predictor,
    to_risk_score,
)

SECRET_MARKERS = ("password", "secret", "DATABASE_URL", "token", "credential")


# --- risk score mapping -----------------------------------------------------


@pytest.mark.parametrize(
    ("probability", "expected"),
    [(0.0, 0), (0.004, 0), (0.0061, 1), (0.5, 50), (0.9349, 93), (0.999, 100), (1.0, 100)],
)
def test_risk_score_is_a_linear_restatement(probability: float, expected: int) -> None:
    assert to_risk_score(probability) == expected


@pytest.mark.parametrize("probability", [-0.5, 1.5])
def test_risk_score_is_clamped(probability: float) -> None:
    assert 0 <= to_risk_score(probability) <= 100


def test_risk_score_uses_bankers_rounding_at_exact_halves() -> None:
    """``round()`` breaks .5 ties towards even, which is what the mapping specifies.

    Pinned rather than worked around: the score is defined as
    ``round(probability * 100)`` and the tie behaviour should be documented, not
    accidental.
    """
    assert to_risk_score(0.005) == 0
    assert to_risk_score(0.015) == 2


# --- artifact ---------------------------------------------------------------


def test_artifact_loads_and_exposes_its_metadata(predictor: FraudRiskPredictor) -> None:
    metadata = predictor.metadata

    assert metadata["feature_version"] == FEATURE_VERSION
    assert metadata["feature_columns"] == list(FEATURE_COLUMNS)
    assert metadata["model_version"] == "xgboost-test"
    assert 0.0 <= metadata["threshold"] <= 1.0
    assert metadata["training_rows"] > 0
    datetime.fromisoformat(metadata["trained_at"])


def test_artifact_contains_no_secrets_or_paths(trained_model_path: Path) -> None:
    """The artifact travels between machines; it must carry nothing sensitive."""
    artifact = joblib.load(trained_model_path)
    serialised = repr(artifact["metadata"]).lower()

    for marker in SECRET_MARKERS:
        assert marker.lower() not in serialised
    assert "c:\\" not in serialised
    assert "/srv/" not in serialised


def test_missing_artifact_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ModelNotAvailableError):
        FraudRiskPredictor.load(tmp_path / "nope.joblib")


def test_a_model_trained_on_another_feature_version_is_refused(
    trained_model_path: Path, tmp_path: Path
) -> None:
    """A stale model served against changed features is worse than none."""
    artifact = joblib.load(trained_model_path)
    artifact["metadata"]["feature_version"] = "v0-ancient"
    stale = tmp_path / "stale.joblib"
    joblib.dump(artifact, stale)

    with pytest.raises(ModelContractError, match="feature version"):
        FraudRiskPredictor.load(stale)


def test_a_model_with_different_feature_columns_is_refused(
    trained_model_path: Path, tmp_path: Path
) -> None:
    artifact = joblib.load(trained_model_path)
    artifact["metadata"]["feature_columns"] = list(FEATURE_COLUMNS)[:-1]
    stale = tmp_path / "columns.joblib"
    joblib.dump(artifact, stale)

    with pytest.raises(ModelContractError, match="feature columns"):
        FraudRiskPredictor.load(stale)


def test_predictor_is_cached_per_process(trained_model_path: Path) -> None:
    reset_predictor()
    try:
        first = get_predictor(trained_model_path)
        assert get_predictor() is first
    finally:
        reset_predictor()


# --- prediction -------------------------------------------------------------


def _features_for(session: Session, reference: str) -> tuple[Transaction, dict]:
    transaction = session.scalars(
        select(Transaction).where(Transaction.transaction_id == reference)
    ).one()
    view = to_view(transaction)
    window = build_history_window(session, view)
    return transaction, build_features(
        view, window, get_merchant_profile(session, view.merchant_id)
    )


def test_prediction_output_is_well_formed(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")
    prediction = predictor.predict_from_features(transaction.transaction_id, features)

    assert 0.0 <= prediction.fraud_probability <= 1.0
    assert prediction.risk_score == to_risk_score(prediction.fraud_probability)
    assert prediction.model_version == "xgboost-test"
    assert prediction.exceeds_threshold == (prediction.fraud_probability >= prediction.threshold)


def test_prediction_is_deterministic(predictor: FraudRiskPredictor, db_session: Session) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    first = predictor.predict_from_features(transaction.transaction_id, features)
    second = predictor.predict_from_features(transaction.transaction_id, features)
    assert first == second


def test_prediction_rejects_a_malformed_feature_row(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    _, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    features.pop(FEATURE_COLUMNS[0])

    with pytest.raises(FeatureSchemaError, match="missing feature"):
        predictor.predict_from_features("txn_broken", features)


def test_prediction_rejects_a_row_carrying_the_label(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    _, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    features["is_fraud"] = 1

    with pytest.raises(FeatureSchemaError):
        predictor.predict_from_features("txn_leaky", features)


def test_predict_builds_features_from_the_database(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_B_CURRENT")
    ).one()
    prediction = predictor.predict(db_session, to_view(transaction))

    assert prediction.transaction_id == "TXN_SCENARIO_B_CURRENT"
    assert 0.0 <= prediction.fraud_probability <= 1.0


def test_the_suspicious_scenario_scores_above_the_normal_one(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    """The model must separate the two, though the values are not prescribed."""
    normal_txn, normal_features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    suspicious_txn, suspicious_features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")

    normal = predictor.predict_from_features(normal_txn.transaction_id, normal_features)
    suspicious = predictor.predict_from_features(suspicious_txn.transaction_id, suspicious_features)

    assert suspicious.fraud_probability > normal.fraud_probability


# --- persistence ------------------------------------------------------------


def test_prediction_is_persisted(predictor: FraudRiskPredictor, db_session: Session) -> None:
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_B_CURRENT")
    ).one()

    prediction = predictor.predict(db_session, to_view(transaction))
    row = store_prediction(db_session, transaction, prediction)

    assert row.transaction_id == transaction.id
    assert row.risk_score == prediction.risk_score
    assert row.model_version == prediction.model_version
    assert float(row.fraud_probability) == pytest.approx(prediction.fraud_probability, abs=1e-5)


def test_rescoring_updates_rather_than_duplicates(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    """``risk_predictions.transaction_id`` is unique: one current score per row."""
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_C_CURRENT_1")
    ).one()

    prediction = predictor.predict(db_session, to_view(transaction))
    first = store_prediction(db_session, transaction, prediction)
    first_id, first_created = first.id, first.created_at

    second = store_prediction(db_session, transaction, prediction)

    assert second.id == first_id
    assert second.created_at >= first_created
    count = db_session.scalar(
        select(func.count(RiskPredictionRow.id)).where(
            RiskPredictionRow.transaction_id == transaction.id
        )
    )
    assert count == 1


def test_stored_probability_respects_the_column_precision(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    """``NUMERIC(6,5)`` cannot hold full float precision; the service quantises."""
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_A_CURRENT")
    ).one()
    prediction = predictor.predict(db_session, to_view(transaction))
    row = store_prediction(db_session, transaction, prediction)

    assert 0 <= float(row.fraud_probability) <= 1
    assert 0 <= row.risk_score <= 100


def test_scoring_leaves_no_rows_when_the_transaction_is_untouched(
    db_session: Session,
) -> None:
    """Phase 2 left risk_predictions empty; only explicit scoring writes to it."""
    untouched = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "txn_00000001")
    ).one()
    existing = db_session.scalar(
        select(func.count(RiskPredictionRow.id)).where(
            RiskPredictionRow.transaction_id == untouched.id
        )
    )
    assert existing == 0


# --- batch scoring ----------------------------------------------------------


def test_batch_scoring_matches_single_scoring(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    """The fast path and the request path must produce identical probabilities."""
    references = [
        "TXN_SCENARIO_A_CURRENT",
        "TXN_SCENARIO_B_CURRENT",
        "TXN_SCENARIO_C_CURRENT_1",
    ]
    rows = []
    singles = []
    for reference in references:
        transaction, features = _features_for(db_session, reference)
        rows.append((reference, features))
        singles.append(predictor.predict_from_features(transaction.transaction_id, features))

    batched = predictor.predict_many(rows)

    assert len(batched) == len(singles)
    for one, many in zip(singles, batched, strict=True):
        assert many.transaction_id == one.transaction_id
        assert many.fraud_probability == pytest.approx(one.fraud_probability, abs=1e-9)
        assert many.risk_score == one.risk_score


def test_batch_scoring_of_nothing_returns_nothing(predictor: FraudRiskPredictor) -> None:
    assert predictor.predict_many([]) == []


def test_batch_scoring_validates_every_row(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    _, good = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    bad = dict(good)
    bad.pop(FEATURE_COLUMNS[0])

    with pytest.raises(FeatureSchemaError):
        predictor.predict_many([("good", good), ("bad", bad)])


def test_bulk_replace_keeps_one_row_per_transaction(
    predictor: FraudRiskPredictor, db_session: Session
) -> None:
    from app.services.risk import bulk_replace_predictions

    transactions = [
        db_session.scalars(select(Transaction).where(Transaction.transaction_id == reference)).one()
        for reference in ("TXN_SCENARIO_A_CURRENT", "TXN_SCENARIO_B_CURRENT")
    ]
    predictions = [predictor.predict(db_session, to_view(t)) for t in transactions]
    payload = list(zip([t.id for t in transactions], predictions, strict=True))

    assert bulk_replace_predictions(db_session, payload) == 2
    # Re-running must replace, not accumulate.
    assert bulk_replace_predictions(db_session, payload) == 2

    for transaction, prediction in zip(transactions, predictions, strict=True):
        count = db_session.scalar(
            select(func.count(RiskPredictionRow.id)).where(
                RiskPredictionRow.transaction_id == transaction.id
            )
        )
        assert count == 1
        row = db_session.scalars(
            select(RiskPredictionRow).where(RiskPredictionRow.transaction_id == transaction.id)
        ).one()
        assert row.risk_score == prediction.risk_score


def test_bulk_replace_of_nothing_is_a_noop(db_session: Session) -> None:
    from app.services.risk import bulk_replace_predictions

    assert bulk_replace_predictions(db_session, []) == 0
