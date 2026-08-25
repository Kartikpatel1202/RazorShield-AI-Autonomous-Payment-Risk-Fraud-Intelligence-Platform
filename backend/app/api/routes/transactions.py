"""Read-only transaction endpoints, including the investigation context."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.enums import TransactionStatus
from app.schemas.common import DEFAULT_PAGE_SIZE, Page, PageNumber, PageSize
from app.schemas.context import TransactionContext
from app.schemas.decision import DecisionHistoryResponse
from app.schemas.entities import TransactionRead
from app.schemas.investigation import InvestigationResponse
from app.services import catalog
from app.services.context import build_transaction_context
from app.services.decision import load_history
from app.services.investigation import load_latest_for_transaction

router = APIRouter(
    prefix="/transactions",
    tags=["transactions"],
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)

TransactionRef = Path(
    description="Transaction reference (e.g. TXN_SCENARIO_B_CURRENT) or numeric primary key"
)


@router.get("", response_model=Page[TransactionRead])
def list_transactions(
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    merchant_id: int | None = Query(default=None, ge=1, description="Filter by merchant"),
    status: TransactionStatus | None = Query(default=None, description="Filter by status"),
    is_fraud: bool | None = Query(
        default=None, description="Filter by the ground-truth fraud label"
    ),
    session: Session = Depends(get_db),
) -> Page[TransactionRead]:
    """The transaction feed, newest first. Always paginated."""
    return catalog.list_transactions(  # type: ignore[return-value]
        session,
        page,
        page_size,
        merchant_id=merchant_id,
        status=status,
        is_fraud=is_fraud,
    )


@router.get("/{transaction_id}", response_model=TransactionRead)
def get_transaction(
    transaction_id: str = TransactionRef, session: Session = Depends(get_db)
) -> TransactionRead:
    """One transaction."""
    return catalog.get_transaction(session, transaction_id)  # type: ignore[return-value]


@router.get("/{transaction_id}/context", response_model=TransactionContext)
def get_transaction_context(
    transaction_id: str = TransactionRef, session: Session = Depends(get_db)
) -> TransactionContext:
    """The transaction plus the surrounding evidence.

    Returns the customer and their recent history, the paying device and IP with
    how widely each is shared, location comparisons, and transaction counts over
    several time windows. All of it is measured from stored rows - no risk score
    is computed here.
    """
    transaction = catalog.get_transaction(session, transaction_id)
    return build_transaction_context(session, transaction)


@router.get(
    "/{transaction_id}/investigation",
    response_model=InvestigationResponse,
    summary="The latest AI investigation of a transaction",
    responses={404: {"description": "No investigation exists for that transaction"}},
)
def get_transaction_investigation(
    transaction_id: str = TransactionRef, session: Session = Depends(get_db)
) -> InvestigationResponse:
    """The most recent investigation of this transaction, if one has been run."""
    from app.api.routes.investigations import render_investigation
    from app.core.errors import EntityNotFoundError

    transaction = catalog.get_transaction(session, transaction_id)
    row = load_latest_for_transaction(session, transaction)
    if row is None:
        raise EntityNotFoundError("Investigation for transaction", transaction_id)
    return render_investigation(row, transaction.transaction_id)


@router.get(
    "/{transaction_id}/decisions",
    response_model=DecisionHistoryResponse,
    summary="Every policy decision ever made about a transaction",
)
def get_transaction_decisions(
    transaction_id: str = TransactionRef, session: Session = Depends(get_db)
) -> DecisionHistoryResponse:
    """The full, immutable decision history, oldest first.

    Re-deciding a transaction appends a row rather than replacing one, so this
    list is the audit trail: what was decided, under which policy version, on
    what inputs, and when.
    """
    from app.api.routes.risk import decision_row_to_response

    transaction = catalog.get_transaction(session, transaction_id)
    rows = load_history(session, transaction)
    return DecisionHistoryResponse(
        transaction_id=transaction.transaction_id,
        decisions=[decision_row_to_response(row, transaction.transaction_id) for row in rows],
    )
