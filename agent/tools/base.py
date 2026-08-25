"""Shared plumbing for the investigation tools.

Three properties hold for every tool in this package, and the tests enforce all
three:

1. **Read-only.** Tools issue ``SELECT`` only. There is no generic query tool,
   no SQL string ever comes from the model, and nothing here can INSERT, UPDATE
   or DELETE.
2. **Bound to one transaction.** A tool receives a :class:`ToolContext` built by
   the application from the transaction under investigation. The model chooses a
   tool *name*; it never supplies an id. It therefore cannot pivot the agent
   onto an unrelated customer, device or IP address.
3. **Point-in-time.** Every historical query is filtered by the same
   ``(timestamp, id)`` boundary used in Phases 3 and 4. A tool investigating a
   10:00 transaction cannot see an 11:00 one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agent.schemas.evidence import EvidenceSeverity
from app.models import Transaction
from ml.features.history import TransactionView
from ml.features.loader import to_view


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool is allowed to know, fixed by the application."""

    session: Session
    transaction: Transaction
    view: TransactionView

    @property
    def boundary(self) -> datetime:
        """The point-in-time cutoff: nothing at or after this may be reported."""
        return self.view.timestamp

    @property
    def reference(self) -> str:
        return self.view.transaction_id

    @classmethod
    def build(cls, session: Session, transaction: Transaction) -> ToolContext:
        return cls(session=session, transaction=transaction, view=to_view(transaction))


@dataclass(frozen=True)
class EvidenceDraft:
    """A tool's observation, before it is assigned an evidence id."""

    claim: str
    severity: EvidenceSeverity
    value: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns: a payload for the model, plus evidence for the record.

    ``payload`` is summarised into the prompt. ``evidence`` becomes immutable
    :class:`~agent.schemas.evidence.Evidence` items that findings must cite.
    """

    payload: dict[str, Any]
    evidence: list[EvidenceDraft] = field(default_factory=list)


def severity_for_ratio(
    value: float, medium: float, high: float, critical: float
) -> EvidenceSeverity:
    """Band a measured ratio. Thresholds are supplied by the caller, never guessed."""
    if value >= critical:
        return EvidenceSeverity.CRITICAL
    if value >= high:
        return EvidenceSeverity.HIGH
    if value >= medium:
        return EvidenceSeverity.MEDIUM
    return EvidenceSeverity.INFO


def summarise(payload: dict[str, Any], limit: int = 900) -> str:
    """Compact, readable rendering of a tool payload for the prompt.

    Truncated so one verbose tool cannot crowd the rest of the investigation out
    of the context window.
    """
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, float):
            rendered = f"{value:.4g}"
        elif isinstance(value, list):
            rendered = f"[{len(value)} items]" if len(value) > 6 else str(value)
        elif isinstance(value, dict):
            rendered = "{" + ", ".join(f"{k}={v}" for k, v in list(value.items())[:6]) + "}"
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")

    text = "; ".join(parts)
    return text if len(text) <= limit else text[: limit - 3] + "..."
