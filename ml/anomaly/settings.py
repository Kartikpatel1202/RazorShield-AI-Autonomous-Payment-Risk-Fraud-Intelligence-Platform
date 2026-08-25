"""Typed view of ``ml/anomaly/config.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ml.training.settings import SplitConfig
from ml.training.settings import load_config as load_training_config

ANOMALY_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass(frozen=True)
class ThresholdConfig:
    objective: str
    beta: float
    minimum_precision: float


@dataclass(frozen=True)
class SensitivityConfig:
    repeats: int
    top_features: int


@dataclass(frozen=True)
class AnomalyConfig:
    """Everything that determines the fitted anomaly model."""

    random_seed: int
    behavioral_feature_version: str
    isolation_forest: dict[str, Any]
    severity_percentiles: tuple[float, float, float]
    threshold: ThresholdConfig
    sensitivity: SensitivityConfig
    #: Reused from the supervised config so both engines see identical folds.
    split: SplitConfig
    source_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "random_seed": self.random_seed,
            "behavioral_feature_version": self.behavioral_feature_version,
            "isolation_forest": dict(self.isolation_forest),
            "severity_percentiles": list(self.severity_percentiles),
            "threshold": {
                "objective": self.threshold.objective,
                "beta": self.threshold.beta,
                "minimum_precision": self.threshold.minimum_precision,
            },
            "split": {
                "train": self.split.train,
                "validation": self.split.validation,
                "test": self.split.test,
            },
        }


def load_anomaly_config(path: Path | None = None) -> AnomalyConfig:
    """Read and validate the anomaly configuration."""
    config_path = path or ANOMALY_CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    percentiles = raw["severity_percentiles"]
    if len(percentiles) != 3 or list(percentiles) != sorted(percentiles):
        raise ValueError("severity_percentiles must be three ascending values")

    return AnomalyConfig(
        random_seed=int(raw["random_seed"]),
        behavioral_feature_version=str(raw["behavioral_feature_version"]),
        isolation_forest=dict(raw["isolation_forest"]),
        severity_percentiles=(
            float(percentiles[0]),
            float(percentiles[1]),
            float(percentiles[2]),
        ),
        threshold=ThresholdConfig(**raw["threshold"]),
        sensitivity=SensitivityConfig(**raw["sensitivity"]),
        split=load_training_config().split,
        source_path=config_path,
    )
