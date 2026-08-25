"""Investigation endpoints.

The agent investigates and explains. Nothing here approves, blocks or steps up a
payment: ``recommended_action`` is advice for the deterministic decision engine
in a later phase, and this module has no path to act on it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import EntityNotFoundError
from app.core.permissions import Permission
from app.db.session import get_db
from app.models import Investigation as InvestigationRow
from app.schemas.investigation import (
    InvestigationRequest,
    InvestigationResponse,
)
from app.services import catalog
from app.services.investigation import load_by_public_id, run_investigation

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/investigations",
    tags=["investigations"],
    # Reading an investigation is a viewer right; the POST below additionally
    # requires INVESTIGATIONS_RUN, because running one costs money and writes
    # evidence rows.
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
)

InvestigationRef = Path(
    description="Public investigation id, e.g. INV-9F2C1A4B8D3E",
    pattern=r"^[A-Za-z0-9_-]{1,32}$",
)


def render_investigation(
    row: InvestigationRow, transaction_reference: str
) -> InvestigationResponse:
    """Render a stored investigation. Never exposes prompts or credentials."""
    report: dict[str, Any] = row.report or {}
    return InvestigationResponse(
        investigation_id=row.public_id or "",
        transaction_id=transaction_reference,
        status=str(row.status),
        risk_level=str(report.get("risk_level", "LOW")),
        confidence=float(row.confidence) if row.confidence is not None else 0.0,
        confidence_basis=report.get("confidence_basis", {}),
        summary=row.summary or "",
        findings=report.get("findings", []),
        evidence=report.get("evidence", []),
        recommended_action=str(report.get("recommended_action", "REVIEW")),
        tools_used=report.get("trace", {}).get("tools_used", []),
        tool_calls=report.get("trace", {}).get("tool_calls", []),
        model_versions=report.get("trace", {}).get("model_versions", {}),
        llm=report.get("trace", {}).get("llm", {}),
        iteration_count=row.iteration_count,
        agent_is_mock=row.agent_is_mock,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


@router.post(
    "",
    response_model=InvestigationResponse,
    summary="Investigate a transaction with the AI risk agent",
    dependencies=[Depends(require(Permission.INVESTIGATIONS_RUN))],
    responses={404: {"description": "No such transaction"}},
)
def create_investigation(
    payload: InvestigationRequest, session: Session = Depends(get_db)
) -> InvestigationResponse:
    """Run an evidence-grounded investigation and store the result.

    The agent reads the existing fraud prediction and anomaly signal, gathers
    further evidence with read-only tools, and explains what it found. Every
    finding cites evidence a tool produced.

    A failure of the language model is reported as
    ``status="agent_unavailable"`` with the evidence gathered so far - never as
    a fabricated investigation.
    """
    transaction = catalog.get_transaction(session, payload.transaction_id)
    result, row = run_investigation(session, transaction)
    session.commit()

    return render_investigation(row, transaction.transaction_id)


@router.get(
    "/{investigation_id}",
    response_model=InvestigationResponse,
    summary="Fetch a stored investigation",
    responses={404: {"description": "No such investigation"}},
)
def get_investigation(
    investigation_id: str = InvestigationRef, session: Session = Depends(get_db)
) -> InvestigationResponse:
    """One investigation with its findings, evidence and tool trace."""
    row = load_by_public_id(session, investigation_id)
    if row is None:
        raise EntityNotFoundError("Investigation", investigation_id)

    reference = row.transaction.transaction_id if row.transaction else ""
    return render_investigation(row, reference)
