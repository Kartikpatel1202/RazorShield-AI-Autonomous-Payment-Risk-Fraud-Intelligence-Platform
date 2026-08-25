"""Typed view of ``ml/training/config.yaml``.

Loading the configuration through dataclasses means a malformed or incomplete
config fails at startup with a clear message, rather than producing a quietly
different model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ml.config import DEFAULT_CONFIG_PATH


@dataclass(frozen=True)
class SplitConfig:
    train: float
    validation: float
    test: float

    def __post_init__(self) -> None:
        total = self.train + self.validation + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split ratios must sum to 1.0, got {total}")
        if min(self.train, self.validation, self.test) <= 0:
            raise ValueError("every split must receive a positive share")


@dataclass(frozen=True)
class ThresholdConfig:
    objective: str
    beta: float
    minimum_precision: float


@dataclass(frozen=True)
class CalibrationConfig:
    expected_calibration_error_limit: float
    method: str
    bins: int


@dataclass(frozen=True)
class ModelConfig:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrimaryModelConfig:
    name: str
    fixed_params: dict[str, Any]
    grid: list[dict[str, Any]]


@dataclass(frozen=True)
class TrainingConfig:
    """Everything that determines the trained model."""

    random_seed: int
    feature_version: str
    dataset_version: str
    split: SplitConfig
    threshold: ThresholdConfig
    calibration: CalibrationConfig
    baseline: ModelConfig
    primary: PrimaryModelConfig
    source_path: Path

    def as_dict(self) -> dict[str, Any]:
        """Serialisable copy, stamped into the model artifact and metrics."""
        return {
            "random_seed": self.random_seed,
            "feature_version": self.feature_version,
            "dataset_version": self.dataset_version,
            "split": {
                "train": self.split.train,
                "validation": self.split.validation,
                "test": self.split.test,
            },
            "threshold": {
                "objective": self.threshold.objective,
                "beta": self.threshold.beta,
                "minimum_precision": self.threshold.minimum_precision,
            },
            "calibration": {
                "expected_calibration_error_limit": (
                    self.calibration.expected_calibration_error_limit
                ),
                "method": self.calibration.method,
                "bins": self.calibration.bins,
            },
            "baseline": {"name": self.baseline.name, "params": self.baseline.params},
            "primary": {
                "name": self.primary.name,
                "fixed_params": self.primary.fixed_params,
                "grid_size": len(self.primary.grid),
            },
        }


def load_config(path: Path | None = None) -> TrainingConfig:
    """Read and validate the training configuration."""
    config_path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    return TrainingConfig(
        random_seed=int(raw["random_seed"]),
        feature_version=str(raw["feature_version"]),
        dataset_version=str(raw["dataset_version"]),
        split=SplitConfig(**raw["split"]),
        threshold=ThresholdConfig(**raw["threshold"]),
        calibration=CalibrationConfig(**raw["calibration"]),
        baseline=ModelConfig(name=raw["baseline"]["name"], params=raw["baseline"]["params"]),
        primary=PrimaryModelConfig(
            name=raw["primary"]["name"],
            fixed_params=raw["primary"]["fixed_params"],
            grid=list(raw["primary"]["grid"]),
        ),
        source_path=config_path,
    )
