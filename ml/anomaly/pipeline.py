"""The behavioral preprocessing + Isolation Forest pipeline.

Preprocessing is inside the fitted pipeline, so the saved artifact is
self-contained: raw behavioral feature rows go in, raw forest scores come out,
with no separate transformation step that could drift out of sync.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer

from ml.anomaly.schema import BEHAVIORAL_FEATURES, LOG1P_FEATURES


def _log1p_columns() -> list[str]:
    return [name for name in BEHAVIORAL_FEATURES if name in LOG1P_FEATURES]


def _passthrough_columns() -> list[str]:
    return [name for name in BEHAVIORAL_FEATURES if name not in LOG1P_FEATURES]


def behavioral_preprocessor() -> ColumnTransformer:
    """``log1p`` the heavy-tailed columns, pass the rest through untouched.

    No scaling: Isolation Forest splits on axis-aligned thresholds, so a
    monotone rescale of a single column changes nothing about which points are
    easy to isolate. ``log1p`` is applied anyway because it is *not* just a
    rescale in effect - it changes how much of each split range the tail
    occupies, which is what stops one 85,000-rupee payment from dominating every
    random cut on `amount`.
    """
    return ColumnTransformer(
        transformers=[
            (
                "log1p",
                FunctionTransformer(np.log1p, feature_names_out="one-to-one", validate=False),
                _log1p_columns(),
            ),
            ("passthrough", "passthrough", _passthrough_columns()),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_forest(params: dict[str, Any], random_seed: int) -> Pipeline:
    """Preprocessing + Isolation Forest, ready to fit."""
    return Pipeline(
        steps=[
            ("preprocess", behavioral_preprocessor()),
            ("forest", IsolationForest(**params, random_state=random_seed)),
        ]
    )


def behavioral_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """The behavioral columns of a Phase 3 dataset, in contract order."""
    missing = [name for name in BEHAVIORAL_FEATURES if name not in frame.columns]
    if missing:
        raise KeyError(f"dataset is missing behavioral feature(s): {missing}")
    return frame[list(BEHAVIORAL_FEATURES)]


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    """Column names produced by the fitted preprocessing step."""
    preprocessor: ColumnTransformer = pipeline.named_steps["preprocess"]
    return [str(name) for name in preprocessor.get_feature_names_out()]
