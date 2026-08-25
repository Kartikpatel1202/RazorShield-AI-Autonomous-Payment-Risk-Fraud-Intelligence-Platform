"""Metrics, threshold selection and calibration assessment.

Accuracy is deliberately absent from the headline metrics. At 1.47% prevalence a
model that predicts "legitimate" for every transaction scores 98.5% accurate and
catches no fraud whatsoever, so accuracy carries no information here. PR-AUC is
the primary comparison metric because it summarises performance across all
operating points while ignoring the vast, easy true-negative mass that inflates
ROC-AUC on imbalanced data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    precision_recall_fscore_support,
    roc_auc_score,
)

from ml.training.settings import CalibrationConfig, ThresholdConfig

#: Ground-truth labels (0/1) and predicted probabilities, as dense arrays.
Labels = npt.NDArray[np.int_]
Probabilities = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ConfusionCounts:
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int


@dataclass(frozen=True)
class Evaluation:
    """A model's performance on one fold at one threshold."""

    fold: str
    threshold: float
    rows: int
    positives: int
    prevalence: float
    precision: float
    recall: float
    f1: float
    pr_auc: float
    roc_auc: float
    confusion: ConfusionCounts
    brier_score: float
    expected_calibration_error: float

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confusion"] = asdict(self.confusion)
        return payload


def expected_calibration_error(
    labels: Labels, probabilities: Probabilities, bins: int = 10
) -> float:
    """Mean absolute gap between predicted probability and observed frequency.

    Equal-width bins over [0, 1], weighted by how many predictions land in each.
    0 means the predicted probabilities can be read as real probabilities.
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    indices = np.clip(np.digitize(probabilities, edges[1:-1], right=False), 0, bins - 1)

    total_error = 0.0
    for index in range(bins):
        mask = indices == index
        count = int(mask.sum())
        if count == 0:
            continue
        gap = abs(float(probabilities[mask].mean()) - float(labels[mask].mean()))
        total_error += gap * count

    return total_error / len(labels) if len(labels) else 0.0


def evaluate(
    fold: str,
    labels: Labels,
    probabilities: Probabilities,
    threshold: float,
    calibration_bins: int = 10,
) -> Evaluation:
    """Score predictions on one fold. Every number comes from the arrays given."""
    predictions = (probabilities >= threshold).astype(int)

    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, predictions, average="binary", zero_division=0
    )
    matrix = confusion_matrix(labels, predictions, labels=[0, 1])
    true_negatives, false_positives, false_negatives, true_positives = matrix.ravel()

    return Evaluation(
        fold=fold,
        threshold=float(threshold),
        rows=int(len(labels)),
        positives=int(labels.sum()),
        prevalence=float(labels.mean()) if len(labels) else 0.0,
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        pr_auc=float(average_precision_score(labels, probabilities)),
        roc_auc=float(roc_auc_score(labels, probabilities)),
        confusion=ConfusionCounts(
            true_negatives=int(true_negatives),
            false_positives=int(false_positives),
            false_negatives=int(false_negatives),
            true_positives=int(true_positives),
        ),
        brier_score=float(brier_score_loss(labels, probabilities)),
        expected_calibration_error=expected_calibration_error(
            labels, probabilities, calibration_bins
        ),
    )


@dataclass(frozen=True)
class ThresholdChoice:
    """The selected operating point and why it was chosen."""

    threshold: float
    precision: float
    recall: float
    f1: float
    fbeta: float
    beta: float
    objective: str
    f1_optimal_threshold: float
    f1_optimal_f1: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fbeta(precision: Probabilities, recall: Probabilities, beta: float) -> Probabilities:
    beta_squared = beta * beta
    denominator = beta_squared * precision + recall
    with np.errstate(divide="ignore", invalid="ignore"):
        score = (1 + beta_squared) * precision * recall / denominator
    return np.nan_to_num(score, nan=0.0, posinf=0.0, neginf=0.0)


def select_threshold(
    labels: Labels, probabilities: Probabilities, config: ThresholdConfig
) -> ThresholdChoice:
    """Pick an operating point on the validation fold.

    0.5 is not assumed. The sweep walks every threshold the validation data
    actually distinguishes and maximises F-beta, subject to a floor on precision
    that keeps the review queue from filling with noise.
    """
    precision, recall, thresholds = precision_recall_curve(labels, probabilities)
    # precision_recall_curve returns one more point than thresholds.
    precision, recall = precision[:-1], recall[:-1]

    if len(thresholds) == 0:
        return ThresholdChoice(0.5, 0.0, 0.0, 0.0, 0.0, config.beta, config.objective, 0.5, 0.0)

    fbeta_scores = _fbeta(precision, recall, config.beta)
    f1_scores = _fbeta(precision, recall, 1.0)

    eligible = precision >= config.minimum_precision
    if not eligible.any():
        eligible = np.ones_like(precision, dtype=bool)

    masked = np.where(eligible, fbeta_scores, -1.0)
    best = int(np.argmax(masked))
    f1_best = int(np.argmax(f1_scores))

    return ThresholdChoice(
        threshold=float(thresholds[best]),
        precision=float(precision[best]),
        recall=float(recall[best]),
        f1=float(f1_scores[best]),
        fbeta=float(fbeta_scores[best]),
        beta=config.beta,
        objective=config.objective,
        f1_optimal_threshold=float(thresholds[f1_best]),
        f1_optimal_f1=float(f1_scores[f1_best]),
    )


def needs_calibration(
    labels: Labels, probabilities: Probabilities, config: CalibrationConfig
) -> tuple[bool, float]:
    """Whether the raw probabilities are miscalibrated enough to be worth fixing.

    Returns ``(needed, measured_error)`` so the decision is always reported with
    the number that drove it - including when no calibration is applied.
    """
    error = expected_calibration_error(labels, probabilities, config.bins)
    return error > config.expected_calibration_error_limit, error
