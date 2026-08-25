"""The investigation state.

A single mutable object threaded through the graph's nodes. Everything the agent
knows lives here, and the final investigation record is assembled from it - so
what gets persisted is exactly what the agent actually observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent.llm.base import LLMCallRecord, LLMUsage
from agent.schemas.evidence import Evidence, EvidenceSeverity, evidence_id
from agent.schemas.investigation import (
    InvestigationStatus,
    ToolCallRecord,
    ToolName,
)
from agent.tools.base import EvidenceDraft


@dataclass
class InvestigationState:
    """Everything gathered during one investigation."""

    investigation_id: str
    transaction_id: str
    boundary: datetime

    evidence: list[Evidence] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    #: Payload summaries keyed by tool, used to build the next prompt.
    observations: dict[ToolName, str] = field(default_factory=dict)
    #: Raw payloads, cached so a repeated tool choice costs nothing.
    payloads: dict[ToolName, dict[str, Any]] = field(default_factory=dict)

    reasoning_log: list[str] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    iteration_count: int = 0
    tool_failures: int = 0
    status: InvestigationStatus = InvestigationStatus.COMPLETED
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @property
    def tools_used(self) -> list[ToolName]:
        """Tools that ran, in order, without duplicates."""
        seen: list[ToolName] = []
        for call in self.tool_calls:
            if call.tool not in seen:
                seen.append(call.tool)
        return seen

    @property
    def evidence_ids(self) -> set[str]:
        return {item.evidence_id for item in self.evidence}

    @property
    def llm_usage(self) -> LLMUsage:
        total = LLMUsage()
        for call in self.llm_calls:
            total = total + call.usage
        return total

    @property
    def llm_latency_ms(self) -> float:
        return sum(call.latency_ms for call in self.llm_calls)

    def has_run(self, tool: ToolName) -> bool:
        return tool in self.payloads

    def record_evidence(self, tool: ToolName, drafts: list[EvidenceDraft]) -> list[str]:
        """Turn tool observations into immutable evidence and return their ids.

        This is the only place :class:`Evidence` is created. The language model
        has no path to this function.
        """
        created: list[str] = []
        for draft in drafts:
            item = Evidence(
                evidence_id=evidence_id(len(self.evidence) + 1),
                source_tool=str(tool),
                claim=draft.claim,
                value=draft.value,
                severity=draft.severity,
                transaction_id=self.transaction_id,
                observed_before=self.boundary,
                details=draft.details,
            )
            self.evidence.append(item)
            created.append(item.evidence_id)
        return created

    def notable_evidence(self) -> list[Evidence]:
        return [
            item
            for item in self.evidence
            if item.severity
            in {EvidenceSeverity.MEDIUM, EvidenceSeverity.HIGH, EvidenceSeverity.CRITICAL}
        ]

    def render_evidence(self) -> str:
        """The evidence list as the model sees it."""
        if not self.evidence:
            return "(no evidence gathered yet)"
        return "\n".join(
            f"{item.evidence_id} [{item.severity}] ({item.source_tool}) {item.claim}"
            for item in self.evidence
        )

    def render_tool_log(self) -> str:
        """Which tools have run and what they returned, for the next prompt."""
        if not self.tool_calls:
            return "(no tools run yet)"
        lines: list[str] = []
        for call in self.tool_calls:
            lines.append(f"  - tool: {call.tool}")
            summary = self.observations.get(call.tool)
            if summary:
                lines.append(f"    result: {summary}")
            if not call.succeeded:
                lines.append(f"    failed: {call.error}")
        return "\n".join(lines)
