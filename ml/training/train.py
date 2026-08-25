"""Train, tune, calibrate and evaluate the fraud risk models.

    python -m ml.training.train

Order of operations, and why:

1. Split chronologically - train is strictly older than validation, which is
   strictly older than test.
2. Fit the logistic-regression baseline on train.
3. Search the XGBoost grid, scoring each candidate on **validation** PR-AUC.
   The test fold is untouched throughout.
4. Choose the operating threshold on validation.
5. Measure calibration on validation; apply isotonic regression only if the raw
   probabilities are measurably off.
6. Evaluate both models on validation and test, and only then report test
   numbers.

Every metric written to disk comes from these runs. Nothing is typed by hand.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

import joblib
import numpy as np
import numpy.typing as npt
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

from app.core.logging import configure_logging
from ml.config import (
    BASELINE_MODEL_PATH,
    DATASET_METADATA_PATH,
    FEATURE_IMPORTANCE_PATH,
    METRICS_PATH,
    MODEL_PATH,
    ensure_directories,
)
from ml.features.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from ml.training.build_dataset import load_dataset
from ml.training.evaluation import (
    Evaluation,
    ThresholdChoice,
    evaluate,
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
from ml.training.settings import TrainingConfig, load_config
from ml.training.split import ChronologicalSplit, Fold, split_chronologically

logger = logging.getLogger(__name__)

BASELINE_VERSION = "logistic-regression-v1"
PRIMARY_VERSION = "xgboost-v1"

Labels = npt.NDArray[np.int_]
Probabilities = npt.NDArray[np.float64]


class GainEntry(TypedDict):
    feature: str
    gain: float


class PermutationEntry(TypedDict):
    feature: str
    mean_pr_auc_drop: float
    std: float


PERMUTATION_REPEATS = 5
TOP_FEATURES = 20


def _probabilities(model: Pipeline, fold: Fold) -> Probabilities:
    return np.asarray(model.predict_proba(features_of(fold.frame))[:, 1], dtype=float)


def _labels(fold: Fold) -> Labels:
    return np.asarray(fold.frame[TARGET_COLUMN].to_numpy(), dtype=int)


def train_baseline(config: TrainingConfig, split: ChronologicalSplit) -> tuple[Pipeline, float]:
    """Fit the logistic-regression baseline on the training fold."""
    started = time.perf_counter()
    model = build_baseline(config.baseline.params, config.random_seed)
    model.fit(features_of(split.train.frame), _labels(split.train))
    elapsed = time.perf_counter() - started
    logger.info("Baseline trained in %.2fs", elapsed)
    return model, elapsed


def search_primary(
    config: TrainingConfig, split: ChronologicalSplit
) -> tuple[Pipeline, dict[str, Any], list[dict[str, Any]], float]:
    """Grid-search XGBoost, scoring candidates on validation PR-AUC."""
    from sklearn.metrics import average_precision_score

    train_labels = _labels(split.train)
    validation_labels = _labels(split.validation)
    weight = positive_class_weight(split.train.frame[TARGET_COLUMN])
    logger.info("scale_pos_weight from the training fold: %.2f", weight)

    started = time.perf_counter()
    trials: list[dict[str, Any]] = []
    best_model: Pipeline | None = None
    best_params: dict[str, Any] = {}
    best_score = -1.0

    for index, grid_params in enumerate(config.primary.grid, start=1):
        candidate = build_primary(
            config.primary.fixed_params, grid_params, weight, config.random_seed
        )
        candidate.fit(features_of(split.train.frame), train_labels)
        score = float(
            average_precision_score(validation_labels, _probabilities(candidate, split.validation))
        )
        trials.append({"params": dict(grid_params), "validation_pr_auc": score})
        logger.info(
            "  candidate %d/%d validation PR-AUC=%.4f", index, len(config.primary.grid), score
        )

        if score > best_score:
            best_model, best_params, best_score = candidate, dict(grid_params), score

    elapsed = time.perf_counter() - started
    if best_model is None:  # pragma: no cover - grid is never empty in practice
        raise RuntimeError("the XGBoost grid produced no candidates")

    logger.info("Best candidate validation PR-AUC=%.4f in %.2fs", best_score, elapsed)
    return best_model, best_params, trials, elapsed


def calibrate_if_needed(
    model: Pipeline, config: TrainingConfig, split: ChronologicalSplit
) -> tuple[Any, dict[str, Any]]:
    """Apply isotonic calibration only when the raw model is measurably off.

    Calibration is fitted on the validation fold, which the test fold never
    sees. Adding it unconditionally would be unnecessary complexity; the
    measured error is reported either way.
    """
    validation_labels = _labels(split.validation)
    raw_probabilities = _probabilities(model, split.validation)
    required, raw_error = needs_calibration(
        validation_labels, raw_probabilities, config.calibration
    )

    report: dict[str, Any] = {
        "raw_expected_calibration_error": raw_error,
        "limit": config.calibration.expected_calibration_error_limit,
        "applied": False,
        "method": None,
        "calibrated_expected_calibration_error": None,
    }

    if not required:
        logger.info(
            "Calibration not applied: validation ECE %.4f is within the %.4f limit",
            raw_error,
            config.calibration.expected_calibration_error_limit,
        )
        return model, report

    calibrated = CalibratedClassifierCV(FrozenEstimator(model), method=config.calibration.method)
    calibrated.fit(features_of(split.validation.frame), validation_labels)

    calibrated_error = needs_calibration(
        validation_labels,
        np.asarray(calibrated.predict_proba(features_of(split.validation.frame))[:, 1]),
        config.calibration,
    )[1]

    report.update(
        applied=True,
        method=config.calibration.method,
        calibrated_expected_calibration_error=calibrated_error,
    )
    logger.info(
        "Calibration applied (%s): validation ECE %.4f -> %.4f",
        config.calibration.method,
        raw_error,
        calibrated_error,
    )
    return calibrated, report


def measure_feature_importance(
    model: Pipeline, split: ChronologicalSplit, random_seed: int
) -> dict[str, Any]:
    """Gain importance plus permutation importance on the validation fold.

    Gain says how much each split improved the objective during training.
    Permutation importance says how much validation PR-AUC actually degrades when
    a column is shuffled - a direct measure of what the model relies on. Both are
    measured, neither assumed.
    """
    names = transformed_feature_names(model)
    classifier = model.named_steps["classifier"]
    gains = np.asarray(classifier.feature_importances_, dtype=float)

    gain_entries: list[GainEntry] = [
        GainEntry(feature=name, gain=float(value)) for name, value in zip(names, gains, strict=True)
    ]
    gain_ranking = sorted(gain_entries, key=lambda entry: entry["gain"], reverse=True)

    logger.info("Measuring permutation importance on the validation fold...")
    result = permutation_importance(
        model,
        features_of(split.validation.frame),
        _labels(split.validation),
        scoring="average_precision",
        n_repeats=PERMUTATION_REPEATS,
        random_state=random_seed,
        n_jobs=1,
    )
    permutation_entries: list[PermutationEntry] = [
        PermutationEntry(feature=feature, mean_pr_auc_drop=float(mean), std=float(std))
        for feature, mean, std in zip(
            FEATURE_COLUMNS, result.importances_mean, result.importances_std, strict=True
        )
    ]
    permutation_ranking = sorted(
        permutation_entries, key=lambda entry: entry["mean_pr_auc_drop"], reverse=True
    )

    return {
        "gain": gain_ranking[:TOP_FEATURES],
        "permutation": permutation_ranking[:TOP_FEATURES],
        "permutation_repeats": PERMUTATION_REPEATS,
        "permutation_scoring": "average_precision",
    }


def _evaluate_model(
    model: Any, split: ChronologicalSplit, threshold: float, bins: int
) -> dict[str, Any]:
    results: dict[str, Evaluation] = {}
    for fold in (split.validation, split.test):
        probabilities = np.asarray(model.predict_proba(features_of(fold.frame))[:, 1], dtype=float)
        results[fold.name] = evaluate(fold.name, _labels(fold), probabilities, threshold, bins)
    return {name: value.as_dict() for name, value in results.items()}


def build_artifact(
    model: Any,
    *,
    model_version: str,
    model_name: str,
    config: TrainingConfig,
    threshold: float,
    training_rows: int,
    params: dict[str, Any],
    calibrated: bool,
    calibration_method: str | None,
) -> dict[str, Any]:
    """Everything needed to reload and serve the model.

    Deliberately contains no filesystem paths, connection strings or environment
    values - only the model and the metadata required to validate its input.
    """
    return {
        "model": model,
        "metadata": {
            "model_version": model_version,
            "model_name": model_name,
            "feature_version": FEATURE_VERSION,
            "dataset_version": config.dataset_version,
            "feature_columns": list(FEATURE_COLUMNS),
            "numeric_features": list(NUMERIC_FEATURES),
            "categorical_features": list(CATEGORICAL_FEATURES),
            "threshold": threshold,
            "trained_at": datetime.now(UTC).isoformat(),
            "training_rows": training_rows,
            "random_seed": config.random_seed,
            "calibrated": calibrated,
            "calibration_method": calibration_method,
            "params": params,
        },
    }


def run(config_path: Path | None = None) -> dict[str, Any]:
    """Execute the full training run and return the metrics document."""
    config = load_config(config_path)
    ensure_directories()

    frame = load_dataset()
    dataset_metadata = json.loads(DATASET_METADATA_PATH.read_text(encoding="utf-8"))
    split = split_chronologically(frame, config.split)

    logger.info(
        "Split: train=%d validation=%d test=%d",
        split.train.rows,
        split.validation.rows,
        split.test.rows,
    )

    baseline, baseline_seconds = train_baseline(config, split)
    primary, primary_params, trials, primary_seconds = search_primary(config, split)

    # Threshold and calibration are both chosen on validation, never on test.
    validation_labels = _labels(split.validation)
    baseline_threshold = select_threshold(
        validation_labels, _probabilities(baseline, split.validation), config.threshold
    )
    primary_threshold_raw = select_threshold(
        validation_labels, _probabilities(primary, split.validation), config.threshold
    )

    served, calibration_report = calibrate_if_needed(primary, config, split)
    primary_threshold: ThresholdChoice = (
        select_threshold(
            validation_labels,
            np.asarray(served.predict_proba(features_of(split.validation.frame))[:, 1]),
            config.threshold,
        )
        if calibration_report["applied"]
        else primary_threshold_raw
    )

    bins = config.calibration.bins
    baseline_metrics = _evaluate_model(baseline, split, baseline_threshold.threshold, bins)
    primary_metrics = _evaluate_model(served, split, primary_threshold.threshold, bins)

    importance = measure_feature_importance(primary, split, config.random_seed)

    # Model selection uses validation PR-AUC; test is only ever reported.
    better = (
        PRIMARY_VERSION
        if primary_metrics["validation"]["pr_auc"] >= baseline_metrics["validation"]["pr_auc"]
        else BASELINE_VERSION
    )
    logger.info("Selected model: %s", better)

    joblib.dump(
        build_artifact(
            served,
            model_version=PRIMARY_VERSION,
            model_name=config.primary.name,
            config=config,
            threshold=primary_threshold.threshold,
            training_rows=split.train.rows,
            params=primary_params,
            calibrated=calibration_report["applied"],
            calibration_method=calibration_report["method"],
        ),
        MODEL_PATH,
    )
    joblib.dump(
        build_artifact(
            baseline,
            model_version=BASELINE_VERSION,
            model_name=config.baseline.name,
            config=config,
            threshold=baseline_threshold.threshold,
            training_rows=split.train.rows,
            params=config.baseline.params,
            calibrated=False,
            calibration_method=None,
        ),
        BASELINE_MODEL_PATH,
    )

    metrics: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": config.as_dict(),
        "dataset": dataset_metadata,
        "split": split.summary(),
        "models": {
            BASELINE_VERSION: {
                "name": config.baseline.name,
                "params": config.baseline.params,
                "training_seconds": round(baseline_seconds, 3),
                "threshold": baseline_threshold.as_dict(),
                "metrics": baseline_metrics,
            },
            PRIMARY_VERSION: {
                "name": config.primary.name,
                "params": {**config.primary.fixed_params, **primary_params},
                "training_seconds": round(primary_seconds, 3),
                "grid_trials": trials,
                "threshold": primary_threshold.as_dict(),
                "calibration": calibration_report,
                "metrics": primary_metrics,
            },
        },
        "selected_model": better,
        "feature_importance": importance,
    }

    METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    FEATURE_IMPORTANCE_PATH.write_text(json.dumps(importance, indent=2) + "\n", encoding="utf-8")
    logger.info("Metrics -> %s", METRICS_PATH)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    metrics = run(args.config)
    primary = metrics["models"][PRIMARY_VERSION]["metrics"]["test"]
    logger.info(
        "XGBoost test: PR-AUC=%.4f ROC-AUC=%.4f precision=%.3f recall=%.3f F1=%.3f",
        primary["pr_auc"],
        primary["roc_auc"],
        primary["precision"],
        primary["recall"],
        primary["f1"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
