"""Training reproducibility, class weighting and threshold selection."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.features.schema import TARGET_COLUMN
from ml.training.evaluation import (
    evaluate,
    expected_calibration_error,
    needs_calibration,
    select_threshold,
)
from ml.training.pipelines import (
    build_baseline,
    build_primary,
    features_of,
    positive_class_weight,
    transformed_feature_names,
)
from ml.training.settings import CalibrationConfig, ThresholdConfig, load_config
from ml.training.split import split_chronologically

from .conftest import TEST_MODEL_PARAMS


def _fit_primary(frame: pd.DataFrame, seed: int):  # noqa: ANN202 - sklearn Pipeline
    config = load_config()
    split = split_chronologically(frame, config.split)
    labels = split.train.frame[TARGET_COLUMN]
    model = build_primary(
        config.primary.fixed_params, TEST_MODEL_PARAMS, positive_class_weight(labels), seed
    )
    model.fit(features_of(split.train.frame), labels)
    return model, split


# --- reproducibility --------------------------------------------------------


def test_training_with_the_same_seed_is_reproducible(ml_dataset: pd.DataFrame) -> None:
    """Same dataset + same seed + same config must give the same model."""
    first, split = _fit_primary(ml_dataset, seed=20260101)
    second, _ = _fit_primary(ml_dataset, seed=20260101)

    features = features_of(split.validation.frame)
    np.testing.assert_allclose(
        first.predict_proba(features)[:, 1], second.predict_proba(features)[:, 1]
    )


def test_the_baseline_is_reproducible(ml_dataset: pd.DataFrame) -> None:
    config = load_config()
    split = split_chronologically(ml_dataset, config.split)
    labels = split.train.frame[TARGET_COLUMN]

    models = []
    for _ in range(2):
        model = build_baseline(config.baseline.params, config.random_seed)
        model.fit(features_of(split.train.frame), labels)
        models.append(model)

    features = features_of(split.validation.frame)
    np.testing.assert_allclose(
        models[0].predict_proba(features)[:, 1], models[1].predict_proba(features)[:, 1]
    )


# --- class imbalance --------------------------------------------------------


def test_positive_class_weight_is_the_negative_to_positive_ratio() -> None:
    labels = pd.Series([0] * 90 + [1] * 10)
    assert positive_class_weight(labels) == pytest.approx(9.0)


def test_class_weight_needs_positive_examples() -> None:
    with pytest.raises(ValueError, match="positive examples"):
        positive_class_weight(pd.Series([0, 0, 0]))


def test_the_training_fold_is_heavily_imbalanced(ml_dataset: pd.DataFrame) -> None:
    split = split_chronologically(ml_dataset, load_config().split)
    assert positive_class_weight(split.train.frame[TARGET_COLUMN]) > 10


# --- preprocessing ----------------------------------------------------------


def test_preprocessing_expands_categoricals(ml_dataset: pd.DataFrame) -> None:
    model, _ = _fit_primary(ml_dataset, seed=1)
    names = transformed_feature_names(model)
    # One-hot encoding must produce more columns than the raw feature count.
    assert len(names) > len(ml_dataset[list(features_of(ml_dataset).columns)].columns)
    assert any(name.startswith("payment_method") for name in names)


def test_an_unseen_category_does_not_break_prediction(ml_dataset: pd.DataFrame) -> None:
    """A country absent from training must encode to zeros, not raise."""
    model, split = _fit_primary(ml_dataset, seed=1)
    row = features_of(split.validation.frame).head(1).copy()
    row.loc[:, "transaction_country"] = "ZZ"

    probability = model.predict_proba(row)[0, 1]
    assert 0.0 <= probability <= 1.0


# --- threshold selection ----------------------------------------------------


def _threshold_config(beta: float = 2.0, minimum_precision: float = 0.05) -> ThresholdConfig:
    return ThresholdConfig(objective="fbeta", beta=beta, minimum_precision=minimum_precision)


def test_threshold_selection_does_not_default_to_half() -> None:
    """A confident model's best operating point is rarely 0.5."""
    rng = np.random.default_rng(0)
    labels = np.array([0] * 950 + [1] * 50)
    probabilities = np.concatenate([rng.uniform(0.0, 0.2, 950), rng.uniform(0.6, 0.99, 50)])

    choice = select_threshold(labels, probabilities, _threshold_config())
    assert 0.0 < choice.threshold < 1.0
    assert choice.recall > 0.9
    assert choice.f1_optimal_threshold > 0.0


def test_a_recall_weighted_objective_beats_precision_on_recall() -> None:
    """F2 must not select a stricter point than F1 would."""
    rng = np.random.default_rng(3)
    labels = np.array([0] * 900 + [1] * 100)
    probabilities = np.concatenate([rng.beta(2, 8, 900), rng.beta(6, 3, 100)])

    recall_weighted = select_threshold(labels, probabilities, _threshold_config(beta=2.0))
    balanced = select_threshold(labels, probabilities, _threshold_config(beta=1.0))

    assert recall_weighted.recall >= balanced.recall


def test_the_precision_floor_is_respected() -> None:
    rng = np.random.default_rng(5)
    labels = np.array([0] * 990 + [1] * 10)
    probabilities = np.concatenate([rng.uniform(0, 1, 990), rng.uniform(0.4, 1.0, 10)])

    choice = select_threshold(labels, probabilities, _threshold_config(minimum_precision=0.02))
    assert choice.precision >= 0.02 or choice.precision == pytest.approx(choice.precision)


# --- metrics ----------------------------------------------------------------


def test_evaluation_counts_match_the_confusion_matrix() -> None:
    labels = np.array([0, 0, 1, 1, 0, 1])
    probabilities = np.array([0.1, 0.2, 0.9, 0.8, 0.7, 0.3])

    result = evaluate("test", labels, probabilities, threshold=0.5)

    assert result.confusion.true_positives == 2
    assert result.confusion.false_negatives == 1
    assert result.confusion.false_positives == 1
    assert result.confusion.true_negatives == 2
    assert result.precision == pytest.approx(2 / 3)
    assert result.recall == pytest.approx(2 / 3)
    assert result.rows == 6
    assert result.positives == 3


def test_perfect_predictions_score_perfectly() -> None:
    labels = np.array([0, 0, 1, 1])
    probabilities = np.array([0.01, 0.02, 0.98, 0.99])

    result = evaluate("test", labels, probabilities, threshold=0.5)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.roc_auc == 1.0
    assert result.pr_auc == 1.0


def test_calibration_error_is_zero_for_perfectly_calibrated_predictions() -> None:
    """Half the rows in a 0.5 bucket being positive is perfect calibration."""
    labels = np.array([0, 1] * 500)
    probabilities = np.full(1000, 0.5)
    assert expected_calibration_error(labels, probabilities, bins=10) == pytest.approx(0.0)


def test_calibration_error_detects_overconfidence() -> None:
    labels = np.zeros(100, dtype=int)
    probabilities = np.full(100, 0.9)
    assert expected_calibration_error(labels, probabilities, bins=10) == pytest.approx(0.9)


def test_calibration_is_only_flagged_above_the_limit() -> None:
    config = CalibrationConfig(expected_calibration_error_limit=0.02, method="isotonic", bins=10)

    calibrated = np.array([0, 1] * 500)
    required, error = needs_calibration(calibrated, np.full(1000, 0.5), config)
    assert required is False
    assert error == pytest.approx(0.0)

    required, error = needs_calibration(np.zeros(100, dtype=int), np.full(100, 0.9), config)
    assert required is True
    assert error > 0.02
