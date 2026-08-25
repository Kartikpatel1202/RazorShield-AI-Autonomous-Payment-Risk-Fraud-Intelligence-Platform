"""Artifact locations for the anomaly engine."""

from __future__ import annotations

from ml.config import MODELS_DIR, REPO_ROOT

ANOMALY_MODEL_PATH = MODELS_DIR / "isolation_forest_v1.joblib"
ANOMALY_METRICS_PATH = MODELS_DIR / "anomaly_metrics.json"
ANOMALY_SENSITIVITY_PATH = MODELS_DIR / "anomaly_sensitivity.json"
SIGNAL_MATRIX_PATH = MODELS_DIR / "signal_matrix.json"

ANOMALY_REPORT_PATH = REPO_ROOT / "docs" / "anomaly-evaluation.md"
