"""The investigation record and the model's structured decision documents.

Two families live here:

* **Output schemas** (:class:`Investigation`, :class:`Finding`) - what the agent
  produces and the API returns.
* **Decision schemas** (:class:`ToolDecision`, :class:`FinalReport`) - the only
  shapes the language model is allowed to emit.

The decision schemas are deliberately impoverished. ``ToolDecision`` carries a
tool **name from a fixed enum and no arguments at all**: every tool runs bound to
the transaction under investigation, so the model cannot pivot the agent onto an
unrelated customer, device or IP address. ``FinalReport`` carries prose plus
evidence ids, which are validated against evidence the tools actually produced.
There is no field through which the model can express an action, a score, or a
database write.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.schemas.evidence import Evidence, EvidenceSeverity


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RecommendedAction(StrEnum):
    """What the investigator suggests a human or policy engine consider.

    Deliberately named ``recommended_action`` and never ``decision``: Phase 5
    investigates and explains. The deterministic decision engine that actually
    approves, blocks or steps up a payment is a later phase, and nothing in this
    package can execute any of these.
    """

    APPROVE = "APPROVE"
    STEP_UP = "STEP_UP"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class InvestigationStatus(StrEnum):
    COMPLETED = "completed"
    #: The agent ran but could not gather enough independent evidence.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: The language model could not be reached or kept returning unusable output.
    AGENT_UNAVAILABLE = "agent_unavailable"
    #: Something else went wrong; no investigation was produced.
    FAILED = "failed"


class ToolName(StrEnum):
    """Every tool the agent may invoke. The model can name these and nothing else."""

    GET_TRANSACTION_CONTEXT = "get_transaction_context"
    GET_CUSTOMER_HISTORY = "get_customer_history"
    GET_DEVICE_HISTORY = "get_device_history"
    GET_IP_HISTORY = "get_ip_history"
    GET_VELOCITY = "get_velocity"
    GET_LOCATION_HISTORY = "get_location_history"
    GET_ML_PREDICTION = "get_ml_prediction"
    GET_ANOMALY_RESULT = "get_anomaly_result"


# --- decision schemas (the model's only output surface) ---------------------


class ToolDecision(BaseModel):
    """The model's choice of what to investigate next."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(
        max_length=1200,
        description="Why this evidence is needed next, or why the picture is complete.",
    )
    enough_evidence: bool = Field(
        description="True when the gathered evidence already answers the question."
    )
    next_tool: ToolName | None = Field(
        default=None,
        description="The tool to run next. Ignored when enough_evidence is true.",
    )


class DraftFinding(BaseModel):
    """A finding as the model proposes it, before grounding is enforced."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=120)
    severity: EvidenceSeverity
    explanation: str = Field(max_length=1200)
    evidence_ids: list[str] = Field(
        min_length=1,
        description="Ids of evidence supporting this finding. Unknown ids are rejected.",
    )


class FinalReport(BaseModel):
    """The model's closing assessment."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=2000)
    risk_level: RiskLevel
    findings: list[DraftFinding] = Field(default_factory=list)
    recommended_action: RecommendedAction


# --- output schemas ---------------------------------------------------------


class Finding(BaseModel):
    """A grounded finding: every evidence id has been verified to exist."""

    finding_id: str = Field(pattern=r"^F-\d{3,}$", examples=["F-001"])
    title: str
    severity: EvidenceSeverity
    explanation: str
    evidence_ids: list[str] = Field(min_length=1)


class ToolCallRecord(BaseModel):
    """One tool invocation in the trace."""

    sequence: int
    tool: ToolName
    latency_ms: float
    evidence_ids: list[str] = Field(default_factory=list)
    succeeded: bool = True
    error: str | None = None


class LLMTrace(BaseModel):
    """Which model produced this investigation, and how much it cost."""

    provider: str
    model: str
    is_mock: bool = Field(
        description="True when a deterministic test double produced this investigation."
    )
    calls: int = 0
    total_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class ConfidenceBasis(BaseModel):
    """The measured factors behind the confidence score.

    Confidence is computed by the application from these, not taken from the
    model's own claim about how sure it feels.
    """

    independent_sources: int
    corroborating_signals: int
    evidence_completeness: float = Field(ge=0.0, le=1.0)
    signal_agreement: float = Field(ge=0.0, le=1.0)
    tool_failures: int = 0
    notes: list[str] = Field(default_factory=list)


class Investigation(BaseModel):
    """The complete, auditable result of one investigation."""

    investigation_id: str
    transaction_id: str
    status: InvestigationStatus
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_basis: ConfidenceBasis
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_action: RecommendedAction
    tools_used: list[ToolName] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    llm: LLMTrace
    iteration_count: int = 0
    started_at: datetime
    completed_at: datetime | None = None

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {item.evidence_id: item for item in self.evidence}

    def as_trace(self) -> dict[str, Any]:
        """Persistable trace. Contains no prompts, no secrets, no raw model text."""
        return {
            "investigation_id": self.investigation_id,
            "iteration_count": self.iteration_count,
            "tools_used": [str(tool) for tool in self.tools_used],
            "tool_calls": [call.model_dump(mode="json") for call in self.tool_calls],
            "llm": self.llm.model_dump(mode="json"),
            "model_versions": self.model_versions,
        }
