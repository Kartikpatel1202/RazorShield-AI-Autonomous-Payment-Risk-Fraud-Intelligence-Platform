"""Request and response models for the behavioral anomaly endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN


class AnomalyRequest(BaseModel):
    """Identifies the transaction whose behaviour should be assessed."""

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=TRANSACTION_REFERENCE_PATTERN,
        description="Transaction reference (e.g. TXN_SCENARIO_C_CURRENT_1) or numeric primary key",
        examples=["TXN_SCENARIO_C_CURRENT_1"],
    )


class FeatureDeviationRead(BaseModel):
    """Where one behaviour sits relative to the fitted normal population."""

    feature: str
    value: float
    percentile: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Percentage of normal behaviour this value exceeds. Directional, not a risk claim."
        ),
    )


class AnomalyResponse(BaseModel):
    """An unsupervised behavioral assessment, independent of the fraud model."""

    transaction_id: str
    anomaly_score: int = Field(
        ge=0,
        le=100,
        description=(
            "Percentage of known-normal behaviour this transaction is more unusual than. "
            "Not a fraud probability: a perfectly typical payment sits near 50."
        ),
    )
    severity: str = Field(description="LOW, MEDIUM, HIGH or CRITICAL")
    model_version: str
    threshold: float = Field(
        ge=0.0, le=100.0, description="Operating point selected on the validation fold."
    )
    exceeds_threshold: bool
    customer_deviation_score: int = Field(
        ge=0, le=100, description="How unusual this is for this customer specifically."
    )
    customer_deviation_driver: str
    top_deviations: list[FeatureDeviationRead]
