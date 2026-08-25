"""Local deviation analysis: which behaviours made this transaction unusual.

Isolation Forest has no native feature importance, and fabricating one would be
worse than reporting none. This module measures a different, well-defined thing:
for a single transaction, where each behavioral feature sits within the
distribution of the fitted normal population.

That is a *local* explanation - "this payment's velocity exceeds 99.8% of normal
behaviour" - not a claim about which features the forest's splits relied on. The
global counterpart is the permutation sensitivity measured during training.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

#: Coarser than the score grids: 101 points resolve 1% steps, which is all a
#: human-facing explanation needs, and keeps the artifact small.
EXPLANATION_QUANTILE_POINTS = 101


@dataclass(frozen=True)
class FeatureDeviation:
    """Where one feature sits relative to normal behaviour."""

    feature: str
    value: float
    #: Percentage of the normal population this value strictly exceeds.
    percentile: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "value": self.value,
            "percentile": round(self.percentile, 2),
        }


def fit_percentile_grids(
    columns: dict[str, npt.NDArray[np.float64]],
) -> dict[str, list[float]]:
    """Build per-feature quantile grids from the fitting population."""
    probabilities = np.linspace(0.0, 1.0, EXPLANATION_QUANTILE_POINTS)
    return {
        name: [
            float(value) for value in np.quantile(np.asarray(values, dtype=float), probabilities)
        ]
        for name, values in columns.items()
    }


def top_deviations(
    grids: dict[str, list[float]],
    features: dict[str, Any],
    limit: int = 8,
    minimum_percentile: float = 90.0,
) -> list[FeatureDeviation]:
    """The behaviours that stand out most for one transaction.

    Ranked by how far above the normal population each value sits, keeping only
    values in the top decile so the list is short enough to read.

    **This is directional, not a risk claim.** A feature can sit at the 100th
    percentile for entirely benign reasons - a long-standing customer has an
    unusually high ``previous_transaction_count``, and an old device has an
    unusually high ``device_age_hours``. The list says *where this behaviour
    sits*, not *that it is suspicious*. Read it together with the anomaly score
    and severity, which is what the forest actually concluded; interpreting the
    combination is the investigating agent's job in a later phase.
    """
    deviations: list[FeatureDeviation] = []

    for name, grid_values in grids.items():
        if name not in features:
            continue
        grid = np.asarray(grid_values, dtype=float)
        value = float(features[name])
        position = int(np.searchsorted(grid, value, side="left"))
        percentile = 100.0 * position / len(grid)
        if percentile >= minimum_percentile:
            deviations.append(FeatureDeviation(feature=name, value=value, percentile=percentile))

    deviations.sort(key=lambda entry: (entry.percentile, entry.feature), reverse=True)
    return deviations[:limit]
