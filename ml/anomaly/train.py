"""Fit, calibrate and evaluate the behavioral anomaly engine.

    python -m ml.anomaly.train

Order of operations:

1. Load the Phase 3 dataset and split it with the **same** chronological
   configuration, so both engines see identical folds.
2. Build the fitting population: training-fold rows with known fraud removed.
3. Fit the Isolation Forest on the behavioral subset of those rows.
4. Fit the percentile normalizer on the fitting population's raw scores.
5. Read severity bands off the **validation** score distribution.
6. Select the binary operating threshold on **validation**.
7. Evaluate validation and test, and only then report test numbers.

## Where the fraud label is used, and where it is not

The label is used in exactly two places, both disclosed:

* **Constructing the fitting population** - known fraud is excluded so the
  forest learns the shape of normal behaviour rather than a blend of both
  classes. This is a training-population filter, not a feature.
* **Evaluation and threshold selection** on validation/test.

It is **never** passed to the model, never part of the behavioral contract, and
never consulted at inference. This engine is therefore *label-informed in its
fitting population* but *label-blind in its inputs* - a distinction worth being
precise about rather than claiming it is purely unsupervised.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.pipeline import Pipeline

from app.core.logging import configure_logging
from ml.anomaly.evaluation import evaluate_anomaly, severity_breakdown
from ml.anomaly.explain import fit_percentile_grids
from ml.anomaly.paths import (
    ANOMALY_METRICS_PATH,
    ANOMALY_MODEL_PATH,
    ANOMALY_SENSITIVITY_PATH,
)
from ml.anomaly.pipeline import behavioral_frame, build_forest
from ml.anomaly.schema import (
    BEHAVIORAL_FEATURE_VERSION,
    BEHAVIORAL_FEATURES,
    CUSTOMER_RELATIVE_FEATURES,
    FEATURE_GROUPS,
    LOG1P_FEATURES,
)
from ml.anomaly.scoring import (
    CustomerRelativeNormalizer,
    PercentileNormalizer,
    SeverityBands,
)
from ml.anomaly.settings import AnomalyConfig, load_anomaly_config
from ml.config import DATASET_METADATA_PATH, ensure_directories
from ml.features.schema import FEATURE_VERSION, TARGET_COLUMN
from ml.training.build_dataset import load_dataset
from ml.training.evaluation import select_threshold
from ml.training.settings import ThresholdConfig as SupervisedThresholdConfig
from ml.training.split import ChronologicalSplit, Fold, split_chronologically

logger = logging.getLogger(__name__)

MODEL_VERSION = "isolation-forest-v1"


Labels = npt.NDArray[np.int_]
Scores = npt.NDArray[np.float64]


def _labels(fold: Fold) -> Labels:
    return np.asarray(fold.frame[TARGET_COLUMN].to_numpy(), dtype=int)


def build_fitting_population(train: Fold) -> pd.DataFrame:
    """Training-fold rows with known fraud removed.

    Fitting on a population that still contained 1.3% fraud would teach the
    forest that those patterns are part of normal density, which is exactly what
    it must not learn.
    """
    population = train.frame[train.frame[TARGET_COLUMN] == 0]
    if population.empty:
        raise ValueError("fitting population is empty after removing known fraud")
    return population


def fit_model(
    config: AnomalyConfig, population: pd.DataFrame
) -> tuple[
    Pipeline, PercentileNormalizer, CustomerRelativeNormalizer, dict[str, list[float]], float
]:
    """Fit the forest and both normalizers on the normal population."""
    started = time.perf_counter()
    model = build_forest(config.isolation_forest, config.random_seed)
    features = behavioral_frame(population)
    model.fit(features)
    elapsed = time.perf_counter() - started

    raw_scores = np.asarray(
        model.named_steps["forest"].score_samples(
            model.named_steps["preprocess"].transform(features)
        ),
        dtype=float,
    )

    normalizer = PercentileNormalizer.fit(raw_scores)
    customer_relative = CustomerRelativeNormalizer.fit(
        {name: population[name].to_numpy(dtype=float) for name in CUSTOMER_RELATIVE_FEATURES}
    )
    explanation_grids = fit_percentile_grids(
        {name: population[name].to_numpy(dtype=float) for name in BEHAVIORAL_FEATURES}
    )

    logger.info(
        "Fitted Isolation Forest on %d normal transactions in %.2fs", len(population), elapsed
    )
    return model, normalizer, customer_relative, explanation_grids, elapsed


def raw_scores_for(model: Pipeline, frame: pd.DataFrame) -> Scores:
    """Raw ``score_samples`` for a set of transactions (higher = more normal)."""
    features = behavioral_frame(frame)
    transformed = model.named_steps["preprocess"].transform(features)
    return np.asarray(model.named_steps["forest"].score_samples(transformed), dtype=float)


def anomaly_scores_for(
    model: Pipeline, normalizer: PercentileNormalizer, frame: pd.DataFrame
) -> Scores:
    return normalizer.to_anomaly_score(raw_scores_for(model, frame))


def measure_sensitivity(
    config: AnomalyConfig,
    model: Pipeline,
    normalizer: PercentileNormalizer,
    validation: Fold,
) -> list[dict[str, Any]]:
    """Permutation sensitivity: PR-AUC drop when a behavioral feature is shuffled.

    Isolation Forest has no native feature importance, and inventing one would
    be worse than reporting none. This measures the real thing: shuffle a
    column, rescore, and see how much the anomaly ranking's agreement with
    ground truth degrades. Labels are used for measurement only.
    """
    from sklearn.metrics import average_precision_score

    labels = _labels(validation)
    baseline = float(
        average_precision_score(labels, anomaly_scores_for(model, normalizer, validation.frame))
    )

    rng = np.random.default_rng(config.random_seed)
    results: list[dict[str, Any]] = []

    for name in BEHAVIORAL_FEATURES:
        drops: list[float] = []
        for _ in range(config.sensitivity.repeats):
            shuffled = validation.frame.copy()
            shuffled[name] = rng.permutation(shuffled[name].to_numpy())
            score = float(
                average_precision_score(labels, anomaly_scores_for(model, normalizer, shuffled))
            )
            drops.append(baseline - score)
        results.append(
            {
                "feature": name,
                "mean_pr_auc_drop": float(np.mean(drops)),
                "std": float(np.std(drops)),
            }
        )

    results.sort(key=lambda entry: float(entry["mean_pr_auc_drop"]), reverse=True)
    return results


def build_artifact(
    model: Pipeline,
    normalizer: PercentileNormalizer,
    customer_relative: CustomerRelativeNormalizer,
    explanation_grids: dict[str, list[float]],
    bands: SeverityBands,
    config: AnomalyConfig,
    threshold: float,
    fitting_rows: int,
) -> dict[str, Any]:
    """Everything needed to reload and serve the anomaly model.

    Carries no filesystem paths, connection strings or environment values.
    """
    return {
        "model": model,
        "normalizer": normalizer.as_dict(),
        "customer_relative": customer_relative.as_dict(),
        "explanation_grids": explanation_grids,
        "metadata": {
            "model_version": MODEL_VERSION,
            "model_name": "isolation_forest",
            "behavioral_feature_version": BEHAVIORAL_FEATURE_VERSION,
            "feature_version": FEATURE_VERSION,
            "feature_columns": list(BEHAVIORAL_FEATURES),
            "customer_relative_features": list(CUSTOMER_RELATIVE_FEATURES),
            "log1p_features": sorted(LOG1P_FEATURES),
            "normalization": "empirical_percentile_of_fitting_population",
            "severity_thresholds": bands.as_dict(),
            "anomaly_threshold": threshold,
            "trained_at": datetime.now(UTC).isoformat(),
            "fitting_rows": fitting_rows,
            "random_seed": config.random_seed,
            "params": dict(config.isolation_forest),
        },
    }


def run(config_path: Path | None = None) -> dict[str, Any]:
    """Execute the full anomaly training run and return the metrics document."""
    config = load_anomaly_config(config_path)
    ensure_directories()

    frame = load_dataset()
    dataset_metadata = json.loads(DATASET_METADATA_PATH.read_text(encoding="utf-8"))
    split: ChronologicalSplit = split_chronologically(frame, config.split)

    population = build_fitting_population(split.train)
    logger.info(
        "Split: train=%d (fitting on %d normal) validation=%d test=%d",
        split.train.rows,
        len(population),
        split.validation.rows,
        split.test.rows,
    )

    model, normalizer, customer_relative, explanation_grids, fit_seconds = fit_model(
        config, population
    )

    # Severity bands and the operating threshold both come from validation.
    validation_scores = anomaly_scores_for(model, normalizer, split.validation.frame)
    validation_labels = _labels(split.validation)

    bands = SeverityBands.from_scores(validation_scores, config.severity_percentiles)
    logger.info(
        "Severity bands from validation p%.0f/p%.0f/p%.0f: medium>=%.1f high>=%.1f critical>=%.1f",
        *config.severity_percentiles,
        bands.medium,
        bands.high,
        bands.critical,
    )

    choice = select_threshold(
        validation_labels,
        validation_scores,
        SupervisedThresholdConfig(
            objective=config.threshold.objective,
            beta=config.threshold.beta,
            minimum_precision=config.threshold.minimum_precision,
        ),
    )
    logger.info(
        "Threshold %.2f (F%.0f): validation precision=%.4f recall=%.4f",
        choice.threshold,
        config.threshold.beta,
        choice.precision,
        choice.recall,
    )

    test_scores = anomaly_scores_for(model, normalizer, split.test.frame)
    test_labels = _labels(split.test)

    metrics: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "config": config.as_dict(),
        "dataset": dataset_metadata,
        "split": split.summary(),
        "fitting_population": {
            "rows": len(population),
            "excluded_known_fraud": int(split.train.positives),
            "source": "train fold, known fraud removed",
            "start": population["transaction_timestamp"].min().isoformat(),
            "end": population["transaction_timestamp"].max().isoformat(),
        },
        "behavioral_features": {
            "count": len(BEHAVIORAL_FEATURES),
            "groups": {name: list(group) for name, group in FEATURE_GROUPS.items()},
            "log1p_transformed": sorted(LOG1P_FEATURES),
        },
        "training_seconds": round(fit_seconds, 3),
        "threshold": choice.as_dict(),
        "severity_bands": bands.as_dict(),
        "severity_breakdown": {
            "validation": severity_breakdown(
                validation_labels, [str(s) for s in bands.classify_many(validation_scores)]
            ),
            "test": severity_breakdown(
                test_labels, [str(s) for s in bands.classify_many(test_scores)]
            ),
        },
        "metrics": {
            "validation": evaluate_anomaly(
                "validation", validation_labels, validation_scores, choice.threshold
            ).as_dict(),
            "test": evaluate_anomaly("test", test_labels, test_scores, choice.threshold).as_dict(),
        },
    }

    logger.info("Measuring permutation sensitivity across %d features...", len(BEHAVIORAL_FEATURES))
    sensitivity = measure_sensitivity(config, model, normalizer, split.validation)
    metrics["sensitivity"] = {
        "method": "permutation, validation average precision",
        "repeats": config.sensitivity.repeats,
        "features": sensitivity[: config.sensitivity.top_features],
    }

    joblib.dump(
        build_artifact(
            model,
            normalizer,
            customer_relative,
            explanation_grids,
            bands,
            config,
            choice.threshold,
            len(population),
        ),
        ANOMALY_MODEL_PATH,
    )
    ANOMALY_METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    ANOMALY_SENSITIVITY_PATH.write_text(
        json.dumps(metrics["sensitivity"], indent=2) + "\n", encoding="utf-8"
    )
    logger.info("Model   -> %s", ANOMALY_MODEL_PATH.name)
    logger.info("Metrics -> %s", ANOMALY_METRICS_PATH.name)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    metrics = run(args.config)
    test = metrics["metrics"]["test"]
    logger.info(
        "Anomaly test: PR-AUC=%.4f (%.1fx random) ROC-AUC=%.4f precision=%.3f recall=%.3f F1=%.3f",
        test["pr_auc"],
        test["lift_over_random"],
        test["roc_auc"],
        test["precision"],
        test["recall"],
        test["f1"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
