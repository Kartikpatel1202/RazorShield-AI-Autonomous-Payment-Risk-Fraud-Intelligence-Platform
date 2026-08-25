"""Turning Isolation Forest output into a stable, interpretable score.

## The raw output

``IsolationForest.score_samples(X)`` returns the negated mean path length needed
to isolate each point, normalised by the forest's expected path length. It is
**higher for more normal points** and typically lands in roughly ``[-0.7, -0.35]``
for this data. Those numbers are meaningless outside the fitted forest: they
depend on ``max_samples``, tree depth and the training distribution, so they
cannot be shown to a person or compared across model versions.

## The mapping

The raw score is converted to an **empirical percentile of the fitting
population**:

```
p            = fraction of fitted normal transactions with a raw score <= this one
anomaly_score = round(100 * (1 - p))
```

So `anomaly_score` answers exactly one question:

> **This transaction is more unusual than N% of known-normal behaviour.**

Properties this buys:

* bounded 0-100 and monotone in the raw score - order is never distorted;
* stable across retrains, because it is a rank rather than a raw distance;
* directly interpretable without knowing anything about Isolation Forest.

The one thing it is **not** is a probability of fraud. Because the reference
population is normal traffic, ordinary transactions spread across the whole
range and a perfectly typical payment scores near 50, not near 0. A score of 50
means "median normal", not "half fraudulent". Severity, not the raw number, is
the part meant to be read at a glance - and its bands are measured from the
validation distribution rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

#: Resolution of the stored percentile grid. 1001 points resolve 0.1% steps,
#: which is finer than the severity bands need and keeps the artifact small.
QUANTILE_POINTS = 1001

RawScores = npt.NDArray[np.float64]
AnomalyScores = npt.NDArray[np.float64]


class AnomalySeverity(StrEnum):
    """Human-readable band for an anomaly score."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class PercentileNormalizer:
    """Maps raw forest scores onto 0-100 by rank within the fitting population."""

    #: Ascending grid of raw ``score_samples`` values from the fitted population.
    grid: tuple[float, ...]

    @classmethod
    def fit(cls, raw_scores: RawScores) -> PercentileNormalizer:
        if raw_scores.size == 0:
            raise ValueError("cannot fit a normalizer on an empty population")
        probabilities = np.linspace(0.0, 1.0, QUANTILE_POINTS)
        grid = np.quantile(np.asarray(raw_scores, dtype=float), probabilities)
        return cls(grid=tuple(float(value) for value in grid))

    def normal_percentile(self, raw_scores: RawScores) -> AnomalyScores:
        """Fraction of the fitting population at or below each raw score."""
        grid = np.asarray(self.grid, dtype=float)
        positions = np.searchsorted(grid, np.asarray(raw_scores, dtype=float), side="right")
        return np.asarray(positions / len(grid), dtype=np.float64)

    def to_anomaly_score(self, raw_scores: RawScores) -> AnomalyScores:
        """Raw forest scores -> 0-100, where 100 is the most anomalous."""
        anomaly = 100.0 * (1.0 - self.normal_percentile(raw_scores))
        return np.clip(anomaly, 0.0, 100.0)

    def as_dict(self) -> dict[str, Any]:
        return {"method": "empirical_percentile", "grid": list(self.grid)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PercentileNormalizer:
        return cls(grid=tuple(float(value) for value in payload["grid"]))


@dataclass(frozen=True)
class SeverityBands:
    """Anomaly-score boundaries, measured from the validation distribution.

    Chosen as percentiles of the validation scores rather than round numbers:
    the bands then describe how rare a score actually is in live-shaped traffic,
    and the measured fraud rate inside each band is reported alongside them.
    """

    medium: float
    high: float
    critical: float
    source_percentiles: tuple[float, float, float]

    def __post_init__(self) -> None:
        if not self.medium <= self.high <= self.critical:
            raise ValueError(
                f"severity bands must be ascending, got "
                f"medium={self.medium} high={self.high} critical={self.critical}"
            )

    @classmethod
    def from_scores(
        cls,
        scores: AnomalyScores,
        percentiles: tuple[float, float, float] = (90.0, 97.0, 99.0),
    ) -> SeverityBands:
        medium, high, critical = (float(np.percentile(scores, p)) for p in percentiles)
        # Percentiles can collide when many scores tie; nudging keeps the bands
        # ordered and non-empty rather than silently collapsing them.
        high = max(high, medium)
        critical = max(critical, high)
        return cls(medium=medium, high=high, critical=critical, source_percentiles=percentiles)

    def classify(self, score: float) -> AnomalySeverity:
        if score >= self.critical:
            return AnomalySeverity.CRITICAL
        if score >= self.high:
            return AnomalySeverity.HIGH
        if score >= self.medium:
            return AnomalySeverity.MEDIUM
        return AnomalySeverity.LOW

    def classify_many(self, scores: AnomalyScores) -> list[AnomalySeverity]:
        return [self.classify(float(score)) for score in scores]

    def as_dict(self) -> dict[str, Any]:
        return {
            "medium": self.medium,
            "high": self.high,
            "critical": self.critical,
            "source_percentiles": list(self.source_percentiles),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SeverityBands:
        percentiles = payload.get("source_percentiles", [90.0, 97.0, 99.0])
        return cls(
            medium=float(payload["medium"]),
            high=float(payload["high"]),
            critical=float(payload["critical"]),
            source_percentiles=(
                float(percentiles[0]),
                float(percentiles[1]),
                float(percentiles[2]),
            ),
        )


@dataclass(frozen=True)
class CustomerRelativeNormalizer:
    """Percentile grids for the customer-relative features.

    The global forest answers "unusual for the population". This answers
    "unusual for *this* customer", without fitting one model per customer - the
    dataset has ~1,500 customers and most have too few transactions to support
    an individual model.

    Each customer-relative feature is ranked against the same fitting
    population, and the reported deviation is the **strongest** of those ranks:
    a payment is customer-anomalous if any one of its customer-relative
    behaviours is extreme, not only if all of them are.
    """

    grids: dict[str, tuple[float, ...]]

    @classmethod
    def fit(cls, values: dict[str, RawScores]) -> CustomerRelativeNormalizer:
        probabilities = np.linspace(0.0, 1.0, QUANTILE_POINTS)
        return cls(
            grids={
                name: tuple(
                    float(value)
                    for value in np.quantile(np.asarray(column, dtype=float), probabilities)
                )
                for name, column in values.items()
            }
        )

    def deviation(self, features: dict[str, float]) -> tuple[float, str]:
        """Return ``(score, driving_feature)`` for one transaction.

        The rank is the fraction of the normal population this value **strictly
        exceeds** (``side="left"``), not the fraction it is greater than or
        equal to. That distinction matters here in a way it does not for the
        global score: these features are discrete and heavily massed at zero -
        most customers make no payment in any given five minutes - so a
        ``side="right"`` rank would place the *modal, most ordinary* value at
        the top of the range and report a perfectly normal payment as a 100%
        deviation. Measuring what a value exceeds gives 0 for the mode, which is
        what a deviation score should say.
        """
        best_score = 0.0
        driver = ""
        for name, grid_values in self.grids.items():
            grid = np.asarray(grid_values, dtype=float)
            position = int(np.searchsorted(grid, float(features[name]), side="left"))
            score = 100.0 * position / len(grid)
            if score > best_score or not driver:
                best_score, driver = score, name
        return min(100.0, best_score), driver

    def as_dict(self) -> dict[str, Any]:
        return {name: list(grid) for name, grid in self.grids.items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CustomerRelativeNormalizer:
        return cls(
            grids={name: tuple(float(value) for value in grid) for name, grid in payload.items()}
        )
