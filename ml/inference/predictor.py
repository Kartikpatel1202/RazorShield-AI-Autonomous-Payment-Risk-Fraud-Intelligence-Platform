"""Load the trained model and score transactions.

The predictor is the only way a probability enters the rest of the system. It
loads a versioned artifact, checks that the feature contract the artifact was
trained under still matches the one the running code produces, and refuses to
score otherwise - a stale model quietly served against changed features would be
worse than no prediction at all.

**Score mapping.** ``risk_score = round(fraud_probability * 100)``, clamped to
0-100. It is a linear, lossless-to-two-decimals restatement of the probability
in a form humans read comfortably; it applies no policy, no banding and no
business rule. Deciding what to *do* at a given score belongs to the decision
engine in a later phase.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from ml.config import MODEL_PATH
from ml.features.builder import build_features
from ml.features.history import TransactionView
from ml.features.loader import get_merchant_profile
from ml.features.point_in_time import build_history_window
from ml.features.schema import FEATURE_COLUMNS, FEATURE_VERSION, validate_row

logger = logging.getLogger(__name__)

RISK_SCORE_MIN = 0
RISK_SCORE_MAX = 100


class ModelNotAvailableError(RuntimeError):
    """The trained model artifact is missing or unreadable."""


class ModelContractError(RuntimeError):
    """The artifact was trained against a different feature contract."""


@dataclass(frozen=True)
class RiskPrediction:
    """One model output. Carries no filesystem or configuration detail."""

    transaction_id: str
    fraud_probability: float
    risk_score: int
    model_version: str
    threshold: float
    exceeds_threshold: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_risk_score(probability: float) -> int:
    """Map a probability onto the transparent 0-100 scale."""
    return max(RISK_SCORE_MIN, min(RISK_SCORE_MAX, round(probability * 100)))


class FraudRiskPredictor:
    """A loaded model plus the metadata needed to use it safely."""

    def __init__(self, model: Any, metadata: dict[str, Any]) -> None:
        self._model = model
        self._metadata = metadata
        self._verify_contract()

    @property
    def model_version(self) -> str:
        return str(self._metadata["model_version"])

    @property
    def threshold(self) -> float:
        return float(self._metadata["threshold"])

    @property
    def metadata(self) -> dict[str, Any]:
        """Public metadata copy, safe to log or expose in diagnostics."""
        return dict(self._metadata)

    def _verify_contract(self) -> None:
        trained_version = self._metadata.get("feature_version")
        if trained_version != FEATURE_VERSION:
            raise ModelContractError(
                f"model was trained on feature version {trained_version!r} but the running "
                f"code produces {FEATURE_VERSION!r}; retrain before serving"
            )

        trained_columns = list(self._metadata.get("feature_columns", []))
        if trained_columns != list(FEATURE_COLUMNS):
            raise ModelContractError(
                "model feature columns do not match the current schema; retrain before serving"
            )

    @classmethod
    def load(cls, path: Path | None = None) -> FraudRiskPredictor:
        artifact_path = path or MODEL_PATH
        if not artifact_path.exists():
            raise ModelNotAvailableError(
                "no trained model is available; run `python -m ml.training.train`"
            )
        try:
            artifact = joblib.load(artifact_path)
        except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
            raise ModelNotAvailableError("the trained model artifact could not be read") from exc

        return cls(artifact["model"], artifact["metadata"])

    def predict_from_features(
        self, transaction_id: str, features: dict[str, Any]
    ) -> RiskPrediction:
        """Score one already-built feature row."""
        validate_row(features)
        frame = pd.DataFrame(
            [[features[name] for name in FEATURE_COLUMNS]], columns=list(FEATURE_COLUMNS)
        )
        probability = float(self._model.predict_proba(frame)[0, 1])

        return RiskPrediction(
            transaction_id=transaction_id,
            fraud_probability=probability,
            risk_score=to_risk_score(probability),
            model_version=self.model_version,
            threshold=self.threshold,
            exceeds_threshold=probability >= self.threshold,
        )

    def predict_many(self, rows: Sequence[tuple[str, dict[str, Any]]]) -> list[RiskPrediction]:
        """Score many pre-built feature rows in a single model call.

        Per-row ``predict_proba`` is dominated by scikit-learn's per-call
        overhead; batching turns thousands of those into one matrix operation.
        """
        if not rows:
            return []

        for _, features in rows:
            validate_row(features)

        frame = pd.DataFrame(
            [[features[name] for name in FEATURE_COLUMNS] for _, features in rows],
            columns=list(FEATURE_COLUMNS),
        )
        probabilities = self._model.predict_proba(frame)[:, 1]

        return [
            RiskPrediction(
                transaction_id=reference,
                fraud_probability=float(probability),
                risk_score=to_risk_score(float(probability)),
                model_version=self.model_version,
                threshold=self.threshold,
                exceeds_threshold=float(probability) >= self.threshold,
            )
            for (reference, _), probability in zip(rows, probabilities, strict=True)
        ]

    def predict(self, session: Session, transaction: TransactionView) -> RiskPrediction:
        """Build point-in-time features for a transaction and score it.

        Uses the same feature builder as training, fed by the SQL point-in-time
        provider, so a live score is computed from exactly the information that
        existed before the transaction.
        """
        window = build_history_window(session, transaction)
        merchant = get_merchant_profile(session, transaction.merchant_id)
        features = build_features(transaction, window, merchant)
        return self.predict_from_features(transaction.transaction_id, features)


_predictor: FraudRiskPredictor | None = None
_lock = threading.Lock()


def get_predictor(path: Path | None = None) -> FraudRiskPredictor:
    """Process-wide predictor, loaded once.

    Deserialising the artifact on every request would dominate prediction
    latency; the model is immutable once trained, so a single instance is safe
    to share.
    """
    global _predictor
    if _predictor is None:
        with _lock:
            if _predictor is None:
                _predictor = FraudRiskPredictor.load(path)
                logger.info("Loaded risk model %s", _predictor.model_version)
    return _predictor


def reset_predictor() -> None:
    """Drop the cached predictor. Used by tests and after retraining."""
    global _predictor
    with _lock:
        _predictor = None
