"""Load the anomaly model and score transactions.

Mirrors the structure of ``ml.inference.predictor``: a versioned artifact, a
feature-contract check that refuses a stale model, and a process-wide cache so
deserialisation does not dominate request latency.

The two engines are deliberately independent. Nothing here reads a supervised
prediction, and nothing in ``ml.inference`` reads an anomaly score. Weighing the
two signals together is Phase 5's job.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import numpy.typing as npt
import pandas as pd
from sqlalchemy.orm import Session

from ml.anomaly.explain import FeatureDeviation, top_deviations
from ml.anomaly.paths import ANOMALY_MODEL_PATH
from ml.anomaly.schema import (
    BEHAVIORAL_FEATURE_VERSION,
    BEHAVIORAL_FEATURES,
    CUSTOMER_RELATIVE_FEATURES,
    select,
    validate_row,
)
from ml.anomaly.scoring import (
    AnomalySeverity,
    CustomerRelativeNormalizer,
    PercentileNormalizer,
    SeverityBands,
)
from ml.features.builder import build_features
from ml.features.history import TransactionView
from ml.features.loader import get_merchant_profile
from ml.features.point_in_time import build_history_window
from ml.features.schema import FEATURE_VERSION

logger = logging.getLogger(__name__)


class AnomalyModelNotAvailableError(RuntimeError):
    """The anomaly artifact is missing or unreadable."""


class AnomalyContractError(RuntimeError):
    """The artifact was fitted against a different behavioral contract."""


@dataclass(frozen=True)
class AnomalyResult:
    """One anomaly assessment. Carries no filesystem or configuration detail."""

    transaction_id: str
    anomaly_score: int
    severity: AnomalySeverity
    model_version: str
    threshold: float
    exceeds_threshold: bool
    #: How unusual this is for *this customer*, and which feature drove it.
    customer_deviation_score: int
    customer_deviation_driver: str
    #: Measured local explanation: where each standout behaviour sits relative
    #: to the fitted normal population.
    top_deviations: tuple[FeatureDeviation, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = str(self.severity)
        payload["top_deviations"] = [d.as_dict() for d in self.top_deviations]
        return payload


class BehavioralAnomalyPredictor:
    """A fitted forest plus the normalizers and bands needed to use it."""

    def __init__(
        self,
        model: Any,
        normalizer: PercentileNormalizer,
        customer_relative: CustomerRelativeNormalizer,
        explanation_grids: dict[str, list[float]],
        metadata: dict[str, Any],
    ) -> None:
        self._model = model
        self._normalizer = normalizer
        self._customer_relative = customer_relative
        self._explanation_grids = explanation_grids
        self._metadata = metadata
        self._bands = SeverityBands.from_dict(metadata["severity_thresholds"])
        self._verify_contract()

    @property
    def model_version(self) -> str:
        return str(self._metadata["model_version"])

    @property
    def threshold(self) -> float:
        return float(self._metadata["anomaly_threshold"])

    @property
    def severity_bands(self) -> SeverityBands:
        return self._bands

    @property
    def metadata(self) -> dict[str, Any]:
        """Public metadata copy, safe to log."""
        return dict(self._metadata)

    def _verify_contract(self) -> None:
        trained = self._metadata.get("behavioral_feature_version")
        if trained != BEHAVIORAL_FEATURE_VERSION:
            raise AnomalyContractError(
                f"model was fitted on behavioral feature version {trained!r} but the running "
                f"code produces {BEHAVIORAL_FEATURE_VERSION!r}; refit before serving"
            )

        if self._metadata.get("feature_version") != FEATURE_VERSION:
            raise AnomalyContractError(
                "model was fitted on a different point-in-time feature version; "
                "refit before serving"
            )

        if list(self._metadata.get("feature_columns", [])) != list(BEHAVIORAL_FEATURES):
            raise AnomalyContractError(
                "model behavioral columns do not match the current contract; refit before serving"
            )

    @classmethod
    def load(cls, path: Path | None = None) -> BehavioralAnomalyPredictor:
        artifact_path = path or ANOMALY_MODEL_PATH
        if not artifact_path.exists():
            raise AnomalyModelNotAvailableError(
                "no anomaly model is available; run `python -m ml.anomaly.train`"
            )
        try:
            artifact = joblib.load(artifact_path)
        except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
            raise AnomalyModelNotAvailableError(
                "the anomaly model artifact could not be read"
            ) from exc

        return cls(
            artifact["model"],
            PercentileNormalizer.from_dict(artifact["normalizer"]),
            CustomerRelativeNormalizer.from_dict(artifact["customer_relative"]),
            artifact.get("explanation_grids", {}),
            artifact["metadata"],
        )

    def _raw_scores(self, frame: pd.DataFrame) -> npt.NDArray[np.float64]:
        preprocess = self._model.named_steps["preprocess"]
        forest = self._model.named_steps["forest"]
        return np.asarray(forest.score_samples(preprocess.transform(frame)), dtype=float)

    def score_many(
        self, rows: Sequence[tuple[str, dict[str, Any]]], *, explain: bool = True
    ) -> list[AnomalyResult]:
        """Score many full Phase 3 feature rows in a single forest call.

        ``explain=False`` skips the per-row local deviation analysis. Bulk
        scoring persists only the score and severity, so ranking all 48 features
        for every one of 20,000 transactions would be work nothing reads.
        """
        if not rows:
            return []

        behavioral = []
        for _, features in rows:
            narrowed = select(features)
            validate_row(narrowed)
            behavioral.append(narrowed)

        frame = pd.DataFrame(
            [[row[name] for name in BEHAVIORAL_FEATURES] for row in behavioral],
            columns=list(BEHAVIORAL_FEATURES),
        )
        anomaly_scores = self._normalizer.to_anomaly_score(self._raw_scores(frame))

        results: list[AnomalyResult] = []
        for (reference, _), narrowed, score in zip(rows, behavioral, anomaly_scores, strict=True):
            deviation, driver = self._customer_relative.deviation(
                {name: float(narrowed[name]) for name in CUSTOMER_RELATIVE_FEATURES}
            )
            value = float(score)
            results.append(
                AnomalyResult(
                    transaction_id=reference,
                    anomaly_score=int(round(value)),
                    severity=self._bands.classify(value),
                    model_version=self.model_version,
                    threshold=self.threshold,
                    exceeds_threshold=value >= self.threshold,
                    customer_deviation_score=int(round(deviation)),
                    customer_deviation_driver=driver,
                    top_deviations=(
                        tuple(top_deviations(self._explanation_grids, narrowed)) if explain else ()
                    ),
                )
            )
        return results

    def score_from_features(self, transaction_id: str, features: dict[str, Any]) -> AnomalyResult:
        """Score one already-built Phase 3 feature row."""
        return self.score_many([(transaction_id, features)])[0]

    def score(self, session: Session, transaction: TransactionView) -> AnomalyResult:
        """Build point-in-time features for a transaction and score its behaviour.

        Uses the same Phase 3 feature builder and point-in-time provider as the
        supervised engine, so the behavioural view is built from exactly the
        information that existed before the transaction.
        """
        window = build_history_window(session, transaction)
        merchant = get_merchant_profile(session, transaction.merchant_id)
        features = build_features(transaction, window, merchant)
        return self.score_from_features(transaction.transaction_id, features)


_predictor: BehavioralAnomalyPredictor | None = None
_lock = threading.Lock()


def get_anomaly_predictor(path: Path | None = None) -> BehavioralAnomalyPredictor:
    """Process-wide anomaly predictor, loaded once."""
    global _predictor
    if _predictor is None:
        with _lock:
            if _predictor is None:
                _predictor = BehavioralAnomalyPredictor.load(path)
                logger.info("Loaded anomaly model %s", _predictor.model_version)
    return _predictor


def reset_anomaly_predictor() -> None:
    """Drop the cached predictor. Used by tests and after refitting."""
    global _predictor
    with _lock:
        _predictor = None
