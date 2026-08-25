"""Behavioral anomaly detection (Phase 4).

An Isolation Forest over a 48-feature behavioral subset of the Phase 3
point-in-time features. It answers a different question from the supervised
model - "how unusual is this behaviour?" rather than "does this look like known
fraud?" - and deliberately produces an independent signal. Combining the two is
Phase 5's job, not this package's.
"""

from ml.anomaly.schema import BEHAVIORAL_FEATURE_VERSION, BEHAVIORAL_FEATURES
from ml.anomaly.scoring import AnomalySeverity

__all__ = ["BEHAVIORAL_FEATURES", "BEHAVIORAL_FEATURE_VERSION", "AnomalySeverity"]
