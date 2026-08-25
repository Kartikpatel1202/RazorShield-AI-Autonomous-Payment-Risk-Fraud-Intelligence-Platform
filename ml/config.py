"""Filesystem layout and artifact naming for the ML package.

Paths are derived from the repository root so every entrypoint - CLI, tests,
API, container - resolves to the same locations without configuration.
"""

from __future__ import annotations

from pathlib import Path

ML_ROOT = Path(__file__).resolve().parent
REPO_ROOT = ML_ROOT.parent

DATA_DIR = ML_ROOT / "data" / "processed"
MODELS_DIR = ML_ROOT / "models"
TRAINING_DIR = ML_ROOT / "training"

DEFAULT_CONFIG_PATH = TRAINING_DIR / "config.yaml"

#: Bumped whenever the dataset build changes shape.
DATASET_VERSION = "v1"

DATASET_PATH = DATA_DIR / f"fraud_dataset_{DATASET_VERSION}.csv"
DATASET_METADATA_PATH = DATA_DIR / f"fraud_dataset_{DATASET_VERSION}.meta.json"

MODEL_PATH = MODELS_DIR / "xgboost_fraud_v1.joblib"
BASELINE_MODEL_PATH = MODELS_DIR / "logistic_regression_fraud_v1.joblib"
METRICS_PATH = MODELS_DIR / "metrics.json"
FEATURE_IMPORTANCE_PATH = MODELS_DIR / "feature_importance.json"

EVALUATION_REPORT_PATH = REPO_ROOT / "docs" / "ml-evaluation.md"


def ensure_directories() -> None:
    """Create the output directories if they do not exist yet."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
