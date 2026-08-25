"""Reason codes.

Stable, machine-readable identifiers for *why* a decision came out the way it
did. Deterministic by construction: each is emitted by a specific rule when a
specific condition holds, never generated from prose and never inferred.

They are part of the audit contract, so treat them as append-only - renaming one
would silently change the meaning of historical decisions.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """Why a decision was reached."""

    # --- supervised model ---------------------------------------------------
    VERY_HIGH_FRAUD_PROBABILITY = "VERY_HIGH_FRAUD_PROBABILITY"
    HIGH_FRAUD_PROBABILITY = "HIGH_FRAUD_PROBABILITY"
    MODERATE_FRAUD_PROBABILITY = "MODERATE_FRAUD_PROBABILITY"
    LOW_FRAUD_PROBABILITY = "LOW_FRAUD_PROBABILITY"

    # --- behavioural anomaly ------------------------------------------------
    CRITICAL_BEHAVIORAL_ANOMALY = "CRITICAL_BEHAVIORAL_ANOMALY"
    HIGH_BEHAVIORAL_ANOMALY = "HIGH_BEHAVIORAL_ANOMALY"
    ELEVATED_BEHAVIORAL_ANOMALY = "ELEVATED_BEHAVIORAL_ANOMALY"
    LOW_BEHAVIORAL_ANOMALY = "LOW_BEHAVIORAL_ANOMALY"

    # --- the two engines relative to one another ----------------------------
    MODEL_DISAGREEMENT = "MODEL_DISAGREEMENT"
    MODEL_AGREEMENT = "MODEL_AGREEMENT"

    # --- investigation evidence ---------------------------------------------
    INDEPENDENT_CORROBORATION = "INDEPENDENT_CORROBORATION"
    MULTIPLE_HIGH_SEVERITY_FINDINGS = "MULTIPLE_HIGH_SEVERITY_FINDINGS"
    COORDINATED_ACTIVITY = "COORDINATED_ACTIVITY"
    INSUFFICIENT_CORROBORATION = "INSUFFICIENT_CORROBORATION"
    LOW_INVESTIGATION_CONFIDENCE = "LOW_INVESTIGATION_CONFIDENCE"
    NO_CONCERNING_EVIDENCE = "NO_CONCERNING_EVIDENCE"

    # --- fail-safe ----------------------------------------------------------
    SUPERVISED_SIGNAL_UNAVAILABLE = "SUPERVISED_SIGNAL_UNAVAILABLE"
    ANOMALY_SIGNAL_UNAVAILABLE = "ANOMALY_SIGNAL_UNAVAILABLE"
    INVESTIGATION_UNAVAILABLE = "INVESTIGATION_UNAVAILABLE"
    BLOCK_WITHHELD_PENDING_INVESTIGATION = "BLOCK_WITHHELD_PENDING_INVESTIGATION"
