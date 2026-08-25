"""Structured evidence.

Evidence is produced by **code**, from tool results. The language model never
creates an evidence item, never edits one, and never supplies a number that ends
up in one. It may only cite evidence by id when writing findings, and a citation
to an id that was not produced by a tool is rejected.

That is the whole grounding mechanism: facts flow tool -> evidence -> finding,
and the model sits at the last hop only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceSeverity(StrEnum):
    """How much attention one piece of evidence warrants on its own."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[EvidenceSeverity, int] = {
    EvidenceSeverity.INFO: 0,
    EvidenceSeverity.LOW: 1,
    EvidenceSeverity.MEDIUM: 2,
    EvidenceSeverity.HIGH: 3,
    EvidenceSeverity.CRITICAL: 4,
}


class Evidence(BaseModel):
    """One fact observed by one tool at one point in time."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(pattern=r"^EV-\d{3,}$", examples=["EV-001"])
    source_tool: str
    claim: str = Field(
        description="A plain-language statement of what was observed. Written by code."
    )
    value: float | None = Field(
        default=None, description="The measured quantity behind the claim, when numeric."
    )
    severity: EvidenceSeverity
    transaction_id: str
    #: The point-in-time boundary this evidence was gathered under - the
    #: timestamp of the transaction being investigated, not wall-clock now.
    observed_before: datetime
    details: dict[str, Any] = Field(default_factory=dict)


def evidence_id(sequence: int) -> str:
    """``EV-001``, ``EV-002``, ... - stable within one investigation."""
    return f"EV-{sequence:03d}"
