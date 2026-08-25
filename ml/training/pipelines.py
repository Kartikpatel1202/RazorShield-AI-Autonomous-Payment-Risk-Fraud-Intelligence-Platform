"""Model pipelines.

Both models share one preprocessing definition so their inputs are identical
and their comparison is fair. Preprocessing lives inside the fitted pipeline, so
the artifact that gets saved is self-contained: raw feature rows in,
probabilities out, no separate transformation step to keep in sync.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from ml.features.schema import CATEGORICAL_FEATURES, FEATURE_COLUMNS, NUMERIC_FEATURES


def _encoder() -> OneHotEncoder:
    """One-hot encoder that tolerates categories unseen during training.

    A payment method or country absent from the training window must not crash a
    live prediction; it encodes to all-zeros instead.
    """
    return OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype="float64")


def scaled_preprocessor() -> ColumnTransformer:
    """Standardised numerics + one-hot categoricals, for the linear baseline."""
    return ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), list(NUMERIC_FEATURES)),
            ("categorical", _encoder(), list(CATEGORICAL_FEATURES)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def tree_preprocessor() -> ColumnTransformer:
    """Raw numerics + one-hot categoricals; gradient boosting needs no scaling."""
    return ColumnTransformer(
        transformers=[
            ("numeric", "passthrough", list(NUMERIC_FEATURES)),
            ("categorical", _encoder(), list(CATEGORICAL_FEATURES)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_baseline(params: dict[str, Any], random_seed: int) -> Pipeline:
    """Logistic regression with balanced class weights.

    ``class_weight="balanced"`` re-weights the loss by inverse class frequency,
    which is the linear-model equivalent of the boosting model's
    ``scale_pos_weight``. Without it a 1.5% positive rate would push the model to
    predict "legitimate" for everything.
    """
    return Pipeline(
        steps=[
            ("preprocess", scaled_preprocessor()),
            ("classifier", LogisticRegression(random_state=random_seed, **params)),
        ]
    )


def build_primary(
    fixed_params: dict[str, Any],
    grid_params: dict[str, Any],
    scale_pos_weight: float,
    random_seed: int,
) -> Pipeline:
    """XGBoost tuned for a heavily imbalanced binary target.

    ``scale_pos_weight`` is set to the negative/positive ratio of the *training*
    fold, so the gradient contribution of the rare class is scaled up to match
    the majority class rather than being drowned out.
    """
    return Pipeline(
        steps=[
            ("preprocess", tree_preprocessor()),
            (
                "classifier",
                XGBClassifier(
                    **fixed_params,
                    **grid_params,
                    scale_pos_weight=scale_pos_weight,
                    random_state=random_seed,
                ),
            ),
        ]
    )


def features_of(frame: pd.DataFrame) -> pd.DataFrame:
    """The model input columns, in the schema's declared order."""
    return frame[list(FEATURE_COLUMNS)]


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    """Column names produced by a fitted pipeline's preprocessing step."""
    preprocessor: ColumnTransformer = pipeline.named_steps["preprocess"]
    return [str(name) for name in preprocessor.get_feature_names_out()]


def positive_class_weight(labels: pd.Series[int]) -> float:
    """Negative-to-positive ratio, used as ``scale_pos_weight``."""
    positives = int(labels.sum())
    negatives = len(labels) - positives
    if positives == 0:
        raise ValueError("cannot compute a class weight without positive examples")
    return negatives / positives
