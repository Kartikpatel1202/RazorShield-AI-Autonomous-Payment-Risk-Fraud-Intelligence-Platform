"""Loading the monitoring configuration.

Mirrors the policy loader's discipline: typed, validated, cached, and it raises
rather than silently substituting defaults. A monitoring threshold that quietly
fell back to something nobody configured would produce alerts - or silence -
that no one could explain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "monitoring.yaml"


class MonitoringConfigError(ValueError):
    """The monitoring configuration is unusable."""


@dataclass(frozen=True)
class DriftConfig:
    """PSI bands and the sample floors below which PSI is not reported."""

    psi_watch: float
    psi_drift: float
    min_samples: int
    bins: int
    baseline_days: int
    current_days: int

    def problems(self) -> list[str]:
        found: list[str] = []
        if not 0 < self.psi_watch < self.psi_drift:
            found.append(
                f"psi_watch ({self.psi_watch}) must be above zero and below "
                f"psi_drift ({self.psi_drift})"
            )
        if self.min_samples < 1:
            found.append("min_samples must be at least 1")
        if self.bins < 2:
            found.append("bins must be at least 2")
        for name in ("baseline_days", "current_days"):
            if getattr(self, name) < 1:
                found.append(f"{name} must be at least 1")
        return found


@dataclass(frozen=True)
class MetricsConfig:
    """Sample floors and the override rate that marks a rule for attention."""

    min_labeled_samples: int
    high_override_rate: float
    min_rule_triggers: int

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.min_labeled_samples < 1:
            found.append("min_labeled_samples must be at least 1")
        if not 0 < self.high_override_rate <= 1:
            found.append("high_override_rate must be in (0, 1]")
        if self.min_rule_triggers < 1:
            found.append("min_rule_triggers must be at least 1")
        return found


@dataclass(frozen=True)
class MonitoringConfig:
    drift: DriftConfig
    metrics: MetricsConfig
    source: str = "<memory>"

    def validate(self) -> None:
        problems = self.drift.problems() + self.metrics.problems()
        if problems:
            detail = "\n".join(f"  - {problem}" for problem in problems)
            raise MonitoringConfigError(f"monitoring configuration is invalid:\n{detail}")

    def as_dict(self) -> dict[str, Any]:
        """Serialisable snapshot, returned alongside any metric it governed."""
        return {
            "psi_watch": self.drift.psi_watch,
            "psi_drift": self.drift.psi_drift,
            "min_samples": self.drift.min_samples,
            "bins": self.drift.bins,
            "min_labeled_samples": self.metrics.min_labeled_samples,
            "high_override_rate": self.metrics.high_override_rate,
            "min_rule_triggers": self.metrics.min_rule_triggers,
        }


def parse_config(raw: dict[str, Any], source: str = "<memory>") -> MonitoringConfig:
    missing = [key for key in ("drift", "metrics") if key not in raw]
    if missing:
        raise MonitoringConfigError(f"missing section(s): {missing}")
    try:
        config = MonitoringConfig(
            drift=DriftConfig(**raw["drift"]),
            metrics=MetricsConfig(**raw["metrics"]),
            source=source,
        )
    except TypeError as exc:
        raise MonitoringConfigError(f"malformed configuration: {exc}") from exc
    config.validate()
    return config


def load_config(path: Path | None = None) -> MonitoringConfig:
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise MonitoringConfigError(f"{config_path.name} does not exist")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MonitoringConfigError(f"{config_path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise MonitoringConfigError(f"{config_path.name} must contain a mapping")
    return parse_config(raw, source=config_path.name)


@lru_cache(maxsize=4)
def get_monitoring_config(path: Path | None = None) -> MonitoringConfig:
    return load_config(path)


def reset_monitoring_cache() -> None:
    get_monitoring_config.cache_clear()
