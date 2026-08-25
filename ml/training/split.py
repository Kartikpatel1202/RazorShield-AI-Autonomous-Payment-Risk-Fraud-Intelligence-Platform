"""Chronological train / validation / test splitting.

A random split would let the model learn from transactions that happen *after*
the ones it is judged on, which is exactly the mistake this project is built to
avoid. Splitting by time reproduces how the model would really be used: fit on
the past, evaluate on the future.

The dataset is already sorted by ``(transaction_timestamp, id)``; the split is
therefore three contiguous slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ml.features.schema import TARGET_COLUMN
from ml.training.settings import SplitConfig


@dataclass(frozen=True)
class Fold:
    """One contiguous slice of the timeline."""

    name: str
    frame: pd.DataFrame

    @property
    def rows(self) -> int:
        return len(self.frame)

    @property
    def positives(self) -> int:
        return int(self.frame[TARGET_COLUMN].sum())

    @property
    def prevalence(self) -> float:
        return self.positives / self.rows if self.rows else 0.0

    @property
    def start(self) -> pd.Timestamp:
        return self.frame["transaction_timestamp"].min()

    @property
    def end(self) -> pd.Timestamp:
        return self.frame["transaction_timestamp"].max()

    def summary(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "fraud_rows": self.positives,
            "legitimate_rows": self.rows - self.positives,
            "fraud_prevalence": self.prevalence,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True)
class ChronologicalSplit:
    train: Fold
    validation: Fold
    test: Fold

    def summary(self) -> dict[str, Any]:
        return {
            "train": self.train.summary(),
            "validation": self.validation.summary(),
            "test": self.test.summary(),
        }


class SplitError(ValueError):
    """The requested split cannot produce usable folds."""


def split_chronologically(frame: pd.DataFrame, config: SplitConfig) -> ChronologicalSplit:
    """Cut the sorted dataset into three time-ordered folds."""
    if not frame["transaction_timestamp"].is_monotonic_increasing:
        raise SplitError("dataset must be sorted by transaction_timestamp before splitting")

    total = len(frame)
    train_end = int(total * config.train)
    validation_end = train_end + int(total * config.validation)

    folds = ChronologicalSplit(
        train=Fold("train", frame.iloc[:train_end].copy()),
        validation=Fold("validation", frame.iloc[train_end:validation_end].copy()),
        test=Fold("test", frame.iloc[validation_end:].copy()),
    )

    for fold in (folds.train, folds.validation, folds.test):
        if fold.rows == 0:
            raise SplitError(f"{fold.name} fold is empty")
        if fold.positives == 0:
            raise SplitError(
                f"{fold.name} fold contains no fraud examples; it cannot be scored meaningfully"
            )

    # The whole point of the split: strict temporal ordering between folds.
    if folds.train.end > folds.validation.start:
        raise SplitError("train fold overlaps validation in time")
    if folds.validation.end > folds.test.start:
        raise SplitError("validation fold overlaps test in time")

    return folds
