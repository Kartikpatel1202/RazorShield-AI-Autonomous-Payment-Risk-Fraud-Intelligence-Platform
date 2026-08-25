"""Anomaly model fitting, persistence, inference and batch parity."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RiskPrediction as RiskPredictionRow
from app.models import RiskSignal, Transaction
from app.services.anomaly import (
    ANOMALY_SIGNAL,
    CUSTOMER_DEVIATION_SIGNAL,
    OWNED_SIGNALS,
    bulk_replace_signals,
    store_signals,
)
from ml.anomaly.predictor import (
    AnomalyContractError,
    AnomalyModelNotAvailableError,
    BehavioralAnomalyPredictor,
    get_anomaly_predictor,
    reset_anomaly_predictor,
)
from ml.anomaly.schema import BEHAVIORAL_FEATURE_VERSION, BEHAVIORAL_FEATURES
from ml.anomaly.scoring import AnomalySeverity
from ml.anomaly.settings import load_anomaly_config
from ml.anomaly.train import (
    anomaly_scores_for,
    build_fitting_population,
    fit_model,
)
from ml.features.builder import build_features
from ml.features.loader import get_merchant_profile, to_view
from ml.features.point_in_time import build_history_window
from ml.features.schema import FEATURE_COLUMNS, TARGET_COLUMN
from ml.training.split import split_chronologically

SECRET_MARKERS = ("password", "secret", "database_url", "token", "credential")


# --- fitting population -----------------------------------------------------


def test_fitting_population_excludes_known_fraud(ml_dataset: pd.DataFrame) -> None:
    """Disclosed use of the label: the forest must learn normal behaviour only."""
    split = split_chronologically(ml_dataset, load_anomaly_config().split)
    population = build_fitting_population(split.train)

    assert population[TARGET_COLUMN].sum() == 0
    assert len(population) == split.train.rows - split.train.positives


def test_fitting_population_is_from_the_training_period_only(
    ml_dataset: pd.DataFrame,
) -> None:
    split = split_chronologically(ml_dataset, load_anomaly_config().split)
    population = build_fitting_population(split.train)

    assert population["transaction_timestamp"].max() <= split.validation.start


def test_an_all_fraud_training_fold_is_rejected(ml_dataset: pd.DataFrame) -> None:
    split = split_chronologically(ml_dataset, load_anomaly_config().split)
    poisoned = split.train
    poisoned.frame[TARGET_COLUMN] = 1

    with pytest.raises(ValueError, match="empty after removing known fraud"):
        build_fitting_population(poisoned)


# --- determinism ------------------------------------------------------------


def test_fitting_is_reproducible(ml_dataset: pd.DataFrame) -> None:
    """Same data + same seed must give the same forest."""
    config = load_anomaly_config()
    split = split_chronologically(ml_dataset, config.split)
    population = build_fitting_population(split.train)

    first_model, first_norm, _, _, _ = fit_model(config, population)
    second_model, second_norm, _, _, _ = fit_model(config, population)

    np.testing.assert_allclose(
        anomaly_scores_for(first_model, first_norm, split.validation.frame),
        anomaly_scores_for(second_model, second_norm, split.validation.frame),
    )


def test_a_different_seed_gives_a_different_forest(ml_dataset: pd.DataFrame) -> None:
    from dataclasses import replace

    config = load_anomaly_config()
    split = split_chronologically(ml_dataset, config.split)
    population = build_fitting_population(split.train)

    baseline_model, baseline_norm, _, _, _ = fit_model(config, population)
    altered = replace(config, random_seed=config.random_seed + 1)
    altered_model, altered_norm, _, _, _ = fit_model(altered, population)

    assert not np.allclose(
        anomaly_scores_for(baseline_model, baseline_norm, split.validation.frame),
        anomaly_scores_for(altered_model, altered_norm, split.validation.frame),
    )


# --- artifact ---------------------------------------------------------------


def test_artifact_exposes_its_metadata(anomaly_predictor: BehavioralAnomalyPredictor) -> None:
    metadata = anomaly_predictor.metadata

    assert metadata["model_version"] == "isolation-forest-v1"
    assert metadata["behavioral_feature_version"] == BEHAVIORAL_FEATURE_VERSION
    assert metadata["feature_columns"] == list(BEHAVIORAL_FEATURES)
    assert metadata["normalization"] == "empirical_percentile_of_fitting_population"
    assert metadata["fitting_rows"] > 0
    assert 0.0 <= metadata["anomaly_threshold"] <= 100.0
    datetime.fromisoformat(metadata["trained_at"])


def test_artifact_records_the_forest_parameters(
    anomaly_predictor: BehavioralAnomalyPredictor,
) -> None:
    params = anomaly_predictor.metadata["params"]
    for name in ("n_estimators", "max_samples", "contamination", "max_features"):
        assert name in params


def test_artifact_contains_no_secrets_or_paths(anomaly_model_path: Path) -> None:
    artifact = joblib.load(anomaly_model_path)
    serialised = repr(artifact["metadata"]).lower()

    for marker in SECRET_MARKERS:
        assert marker not in serialised
    assert "c:\\" not in serialised
    assert "/srv/" not in serialised


def test_missing_artifact_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(AnomalyModelNotAvailableError):
        BehavioralAnomalyPredictor.load(tmp_path / "absent.joblib")


def test_a_model_from_another_behavioral_version_is_refused(
    anomaly_model_path: Path, tmp_path: Path
) -> None:
    artifact = joblib.load(anomaly_model_path)
    artifact["metadata"]["behavioral_feature_version"] = "b0-ancient"
    stale = tmp_path / "stale.joblib"
    joblib.dump(artifact, stale)

    with pytest.raises(AnomalyContractError, match="behavioral feature version"):
        BehavioralAnomalyPredictor.load(stale)


def test_a_model_with_different_columns_is_refused(
    anomaly_model_path: Path, tmp_path: Path
) -> None:
    artifact = joblib.load(anomaly_model_path)
    artifact["metadata"]["feature_columns"] = list(BEHAVIORAL_FEATURES)[:-1]
    stale = tmp_path / "columns.joblib"
    joblib.dump(artifact, stale)

    with pytest.raises(AnomalyContractError, match="behavioral columns"):
        BehavioralAnomalyPredictor.load(stale)


def test_predictor_is_cached_per_process(anomaly_model_path: Path) -> None:
    reset_anomaly_predictor()
    try:
        first = get_anomaly_predictor(anomaly_model_path)
        assert get_anomaly_predictor() is first
    finally:
        reset_anomaly_predictor()


# --- inference --------------------------------------------------------------


def _features_for(session: Session, reference: str) -> tuple[Transaction, dict]:
    transaction = session.scalars(
        select(Transaction).where(Transaction.transaction_id == reference)
    ).one()
    view = to_view(transaction)
    window = build_history_window(session, view)
    return transaction, build_features(
        view, window, get_merchant_profile(session, view.merchant_id)
    )


def test_result_is_well_formed(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)

    assert 0 <= result.anomaly_score <= 100
    assert result.severity in set(AnomalySeverity)
    assert result.model_version == "isolation-forest-v1"
    assert result.exceeds_threshold == (result.anomaly_score >= result.threshold)
    assert 0 <= result.customer_deviation_score <= 100


def test_scoring_is_deterministic(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    first = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    second = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    assert first == second


def test_scoring_from_the_database_matches_scoring_from_features(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_C_CURRENT_1")
    from_db = anomaly_predictor.score(db_session, to_view(transaction))
    from_features = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    assert from_db.anomaly_score == from_features.anomaly_score


def test_scoring_tolerates_extra_columns(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """The predictor selects its subset, so a full Phase 3 row is acceptable."""
    transaction, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    features["some_future_feature"] = 1.0
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    assert 0 <= result.anomaly_score <= 100


def test_scoring_rejects_a_row_missing_behavioral_features(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    from ml.anomaly.schema import BehavioralSchemaError

    _, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    del features[BEHAVIORAL_FEATURES[0]]

    with pytest.raises(BehavioralSchemaError):
        anomaly_predictor.score_from_features("broken", features)


def test_deviations_are_measured_not_empty(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_C_CURRENT_1")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)

    assert result.top_deviations
    for deviation in result.top_deviations:
        assert deviation.feature in BEHAVIORAL_FEATURES
        assert 0.0 <= deviation.percentile <= 100.0


def test_the_coordinated_scenario_is_more_anomalous_than_the_normal_one(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """Values are measured, not prescribed; only the ordering is asserted."""
    normal_txn, normal_features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    ring_txn, ring_features = _features_for(db_session, "TXN_SCENARIO_C_CURRENT_1")

    normal = anomaly_predictor.score_from_features(normal_txn.transaction_id, normal_features)
    ring = anomaly_predictor.score_from_features(ring_txn.transaction_id, ring_features)

    assert ring.anomaly_score > normal.anomaly_score


# --- batch parity -----------------------------------------------------------


def test_batch_scoring_matches_single_scoring(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    references = [
        "TXN_SCENARIO_A_CURRENT",
        "TXN_SCENARIO_B_CURRENT",
        "TXN_SCENARIO_C_CURRENT_1",
        "TXN_SCENARIO_C_CURRENT_2",
    ]
    rows = []
    singles = []
    for reference in references:
        transaction, features = _features_for(db_session, reference)
        rows.append((reference, features))
        singles.append(anomaly_predictor.score_from_features(transaction.transaction_id, features))

    batched = anomaly_predictor.score_many(rows)

    assert len(batched) == len(singles)
    for one, many in zip(singles, batched, strict=True):
        assert many.transaction_id == one.transaction_id
        assert many.anomaly_score == one.anomaly_score
        assert many.severity == one.severity
        assert many.customer_deviation_score == one.customer_deviation_score


def test_batch_scoring_of_nothing_returns_nothing(
    anomaly_predictor: BehavioralAnomalyPredictor,
) -> None:
    assert anomaly_predictor.score_many([]) == []


# --- risk signal persistence ------------------------------------------------


def test_signals_are_written_to_risk_signals(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)

    rows = store_signals(db_session, transaction, result)
    names = {row.signal_name for row in rows}

    assert names == set(OWNED_SIGNALS)
    anomaly_row = next(row for row in rows if row.signal_name == ANOMALY_SIGNAL)
    assert int(anomaly_row.signal_value) == result.anomaly_score
    assert anomaly_row.source == result.model_version
    assert str(anomaly_row.severity) == str(result.severity).lower()


def test_rescoring_replaces_rather_than_accumulates(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_C_CURRENT_1")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)

    for _ in range(3):
        store_signals(db_session, transaction, result)

    count = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name.in_(OWNED_SIGNALS),
        )
    )
    assert count == len(OWNED_SIGNALS)


def test_storing_signals_does_not_touch_the_supervised_prediction(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """The two engines write to different tables and must not interfere."""
    transaction, features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    store_signals(db_session, transaction, result)

    predictions = db_session.scalar(
        select(func.count(RiskPredictionRow.id)).where(
            RiskPredictionRow.transaction_id == transaction.id
        )
    )
    assert predictions == 0


def test_signals_from_other_sources_are_left_alone(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """Rescoring must delete only the signals this service owns."""
    from decimal import Decimal

    from app.models.enums import SignalSeverity

    transaction, features = _features_for(db_session, "TXN_SCENARIO_A_CURRENT")
    foreign = RiskSignal(
        transaction_id=transaction.id,
        signal_name="some_other_engine_signal",
        signal_value=Decimal("1.0"),
        severity=SignalSeverity.INFO,
        source="another-model-v9",
    )
    db_session.add(foreign)
    db_session.flush()

    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    store_signals(db_session, transaction, result)

    survivors = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name == "some_other_engine_signal",
        )
    )
    assert survivors == 1


def test_bulk_replace_writes_two_signals_per_transaction(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    references = ["TXN_SCENARIO_A_CURRENT", "TXN_SCENARIO_B_CURRENT"]
    payload = []
    for reference in references:
        transaction, features = _features_for(db_session, reference)
        result = anomaly_predictor.score_from_features(reference, features)
        payload.append((transaction.id, result))

    assert bulk_replace_signals(db_session, payload) == 2
    assert bulk_replace_signals(db_session, payload) == 2

    total = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.transaction_id.in_([row[0] for row in payload]),
            RiskSignal.signal_name.in_(OWNED_SIGNALS),
        )
    )
    assert total == len(references) * len(OWNED_SIGNALS)


def test_bulk_replace_of_nothing_is_a_noop(db_session: Session) -> None:
    assert bulk_replace_signals(db_session, []) == 0


def test_customer_deviation_is_stored_as_its_own_signal(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    transaction, features = _features_for(db_session, "TXN_SCENARIO_B_CURRENT")
    result = anomaly_predictor.score_from_features(transaction.transaction_id, features)
    rows = store_signals(db_session, transaction, result)

    deviation = next(row for row in rows if row.signal_name == CUSTOMER_DEVIATION_SIGNAL)
    assert int(deviation.signal_value) == result.customer_deviation_score


def test_phase_3_feature_contract_is_unchanged() -> None:
    """Phase 4 must not have altered the supervised contract."""
    assert len(FEATURE_COLUMNS) == 74


def test_explanations_can_be_skipped_for_bulk_scoring(
    anomaly_predictor: BehavioralAnomalyPredictor, db_session: Session
) -> None:
    """Bulk scoring persists only score and severity, so it skips the analysis."""
    transaction, features = _features_for(db_session, "TXN_SCENARIO_C_CURRENT_1")
    rows = [(transaction.transaction_id, features)]

    explained = anomaly_predictor.score_many(rows, explain=True)[0]
    plain = anomaly_predictor.score_many(rows, explain=False)[0]

    assert explained.top_deviations
    assert plain.top_deviations == ()
    # Skipping the explanation must not change the score itself.
    assert plain.anomaly_score == explained.anomaly_score
    assert plain.severity == explained.severity
    assert plain.customer_deviation_score == explained.customer_deviation_score
