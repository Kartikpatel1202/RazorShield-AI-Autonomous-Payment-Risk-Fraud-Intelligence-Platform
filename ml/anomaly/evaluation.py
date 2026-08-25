"""Evaluating an unsupervised score against labels.

The fraud label is used **here and only here**: to measure whether the anomaly
signal carries information. It never reaches the forest, the feature contract or
inference. See ``ml/anomaly/train.py`` for the one other place the label is
touched - excluding known fraud from the fitting population, which is documented
as a deliberate, disclosed choice.

Brier score and calibration error are deliberately absent. An anomaly score is a
rank within normal behaviour, not a probability of fraud, so treating it as one
would be a category error.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)

Labels = npt.NDArray[np.int_]
Scores = npt.NDArray[np.float64]

REPORTED_PERCENTILES: tuple[int, ...] = (5, 25, 50, 75, 90, 95, 99)


@dataclass(frozen=True)
class ScoreDistribution:
    """Where a group's anomaly scores sit."""

    count: int
    mean: float
    percentiles: dict[str, float]

    @classmethod
    def measure(cls, scores: Scores) -> ScoreDistribution:
        if scores.size == 0:
            return cls(count=0, mean=0.0, percentiles={f"p{p}": 0.0 for p in REPORTED_PERCENTILES})
        return cls(
            count=int(scores.size),
            mean=float(np.mean(scores)),
            percentiles={f"p{p}": float(np.percentile(scores, p)) for p in REPORTED_PERCENTILES},
        )


@dataclass(frozen=True)
class AnomalyEvaluation:
    """How well the anomaly score separates fraud on one fold."""

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
    #: PR-AUC of a random ranker, i.e. the prevalence. Lift above this is the
    #: only honest way to read PR-AUC on a heavily imbalanced fold.
    pr_auc_baseline: float
    lift_over_random: float
    confusion: dict[str, int]
    legitimate_scores: ScoreDistribution
    fraud_scores: ScoreDistribution

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["legitimate_scores"] = asdict(self.legitimate_scores)
        payload["fraud_scores"] = asdict(self.fraud_scores)
        return payload


def evaluate_anomaly(
    fold: str, labels: Labels, scores: Scores, threshold: float
) -> AnomalyEvaluation:
    """Score an anomaly ranking against ground truth. All numbers are measured."""
    predictions = (scores >= threshold).astype(int)

    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, predictions, average="binary", zero_division=0
    )
    true_negatives, false_positives, false_negatives, true_positives = confusion_matrix(
        labels, predictions, labels=[0, 1]
    ).ravel()

    prevalence = float(labels.mean()) if labels.size else 0.0
    pr_auc = float(average_precision_score(labels, scores))

    return AnomalyEvaluation(
        fold=fold,
        threshold=float(threshold),
        rows=int(labels.size),
        positives=int(labels.sum()),
        prevalence=prevalence,
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        pr_auc=pr_auc,
        roc_auc=float(roc_auc_score(labels, scores)),
        pr_auc_baseline=prevalence,
        lift_over_random=pr_auc / prevalence if prevalence else 0.0,
        confusion={
            "true_negatives": int(true_negatives),
            "false_positives": int(false_positives),
            "false_negatives": int(false_negatives),
            "true_positives": int(true_positives),
        },
        legitimate_scores=ScoreDistribution.measure(scores[labels == 0]),
        fraud_scores=ScoreDistribution.measure(scores[labels == 1]),
    )


def severity_breakdown(labels: Labels, severities: list[str]) -> dict[str, dict[str, float | int]]:
    """Rows and measured fraud rate inside each severity band.

    This is what justifies the band boundaries: a band is only useful if the
    fraud rate inside it actually rises.
    """
    breakdown: dict[str, dict[str, float | int]] = {}
    severity_array = np.asarray(severities)

    for band in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        mask = severity_array == band
        count = int(mask.sum())
        fraud = int(labels[mask].sum()) if count else 0
        breakdown[band] = {
            "rows": count,
            "fraud_rows": fraud,
            "fraud_rate": fraud / count if count else 0.0,
        }
    return breakdown
