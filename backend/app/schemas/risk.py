"""Request and response models for the risk prediction endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

#: Transaction references are opaque business keys. Constraining the charset
#: keeps hostile input out of the lookup path; the value is only ever used as a
#: bound query parameter.
TRANSACTION_REFERENCE_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"


class RiskPredictionRequest(BaseModel):
    """Identifies the transaction to score."""

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=TRANSACTION_REFERENCE_PATTERN,
        description="Transaction reference (e.g. TXN_SCENARIO_B_CURRENT) or numeric primary key",
        examples=["TXN_SCENARIO_B_CURRENT"],
    )


class RiskPredictionResponse(ORMModel):
    """A model-produced fraud probability and its 0-100 restatement."""

    transaction_id: str
    fraud_probability: float = Field(
        ge=0.0, le=1.0, description="Model output. Never hardcoded or rule-derived."
    )
    risk_score: int = Field(
        ge=0, le=100, description="round(fraud_probability * 100). A restatement, not a policy."
    )
    model_version: str
    threshold: float = Field(
        ge=0.0, le=1.0, description="Operating point selected on the validation fold."
    )
    exceeds_threshold: bool
    created_at: datetime
