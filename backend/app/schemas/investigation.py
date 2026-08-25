"""Request and response models for the investigation endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN


class InvestigationRequest(BaseModel):
    """Identifies the transaction to investigate."""

    transaction_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=TRANSACTION_REFERENCE_PATTERN,
        description="Transaction reference (e.g. TXN_SCENARIO_C_CURRENT_1) or numeric primary key",
        examples=["TXN_SCENARIO_C_CURRENT_1"],
    )


class InvestigationResponse(BaseModel):
    """A completed investigation: what was found, and what it rests on."""

    investigation_id: str
    transaction_id: str
    status: str = Field(
        description=(
            "completed, insufficient_evidence, agent_unavailable or failed. "
            "A failed language model is reported, never fabricated around."
        )
    )
    risk_level: str
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Computed by the application from measured factors, not taken from the "
            "model's own claim. See confidence_basis."
        ),
    )
    confidence_basis: dict[str, Any] = Field(default_factory=dict)
    summary: str
    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Each finding cites evidence ids that a tool actually produced.",
    )
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str = Field(
        description=(
            "Advice for a human reviewer or the later decision engine: APPROVE, "
            "STEP_UP, REVIEW or BLOCK. This system does not execute it."
        )
    )
    tools_used: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider, model, call count and token usage. Never prompt text.",
    )
    iteration_count: int = 0
    agent_is_mock: bool = Field(
        description="True when a deterministic test double produced this investigation."
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
