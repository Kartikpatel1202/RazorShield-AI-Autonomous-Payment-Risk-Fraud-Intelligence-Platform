"""Operations console endpoints: explorer, detail, audit, policy and health.

All read-only. Nothing here scores, decides, investigates or edits policy.

Every list is paginated, every filter is a bound parameter, and every free-text
input is validated at the edge before it reaches a query.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import ceil
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from agent.config import get_agent_settings
from app.api.deps import require
from app.core.config import get_settings
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.enums import ActorType, DecisionAction, TransactionStatus
from app.schemas.common import DEFAULT_PAGE_SIZE, Page, PageMeta, PageNumber, PageSize
from app.schemas.operations import (
    AuditEntry,
    AuditSummaryResponse,
    ComponentHealth,
    ExplorerRow,
    PolicyEvidence,
    PolicyFailSafe,
    PolicyResponse,
    PolicyRuleRead,
    PolicyThresholds,
    SystemHealthResponse,
    TransactionDetailResponse,
)
from app.schemas.risk import TRANSACTION_REFERENCE_PATTERN
from app.services import audit, catalog, detail, explorer
from ml.anomaly.predictor import get_anomaly_predictor
from ml.inference.predictor import get_predictor
from policy.loader import get_policy
from policy.reasons import ReasonCode
from policy.rules import RULE_PRIMARY_ACTION, rule_description
from policy.schema import KNOWN_RULE_IDS, PolicyValidationError

router = APIRouter(tags=["operations"])


class SortKey(StrEnum):
    """Sortable explorer columns. A closed set - nothing else reaches SQL."""

    TIMESTAMP = "timestamp"
    AMOUNT = "amount"
    FRAUD_PROBABILITY = "fraud_probability"
    ANOMALY_SCORE = "anomaly_score"
    TRANSACTION_ID = "transaction_id"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnomalySeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


#: Free-text search is constrained to the transaction-reference charset. It is
#: used as a bound LIKE parameter either way; the pattern keeps hostile input
#: out of the query plan rather than relying on escaping alone.
SearchTerm = Annotated[
    str | None,
    Query(
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]*$",
        description="Transaction reference substring",
    ),
]
ReferenceParam = Annotated[str | None, Query(max_length=64, pattern=TRANSACTION_REFERENCE_PATTERN)]


# --------------------------------------------------------------------------
# Transaction explorer
# --------------------------------------------------------------------------
@router.get(
    "/transactions/explorer",
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
    response_model=Page[ExplorerRow],
    summary="Filtered, sorted, paginated transaction feed with risk columns",
)
def explore_transactions(
    session: Session = Depends(get_db),
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    search: SearchTerm = None,
    decision: Annotated[DecisionAction | None, Query(description="Current decision")] = None,
    risk_level: Annotated[RiskLevel | None, Query(description="Policy risk band")] = None,
    anomaly_severity: Annotated[AnomalySeverity | None, Query()] = None,
    merchant_id: ReferenceParam = None,
    customer_id: ReferenceParam = None,
    status: Annotated[TransactionStatus | None, Query()] = None,
    is_fraud: Annotated[bool | None, Query(description="Ground-truth label")] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    min_probability: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    max_probability: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    sort_by: Annotated[SortKey, Query()] = SortKey.TIMESTAMP,
    descending: Annotated[bool, Query()] = True,
) -> Page[ExplorerRow]:
    """One page of the explorer.

    Filtering, sorting and paging all happen in the database. The browser never
    receives more than ``page_size`` rows, whatever the dataset size.
    """
    result = explorer.explore(
        session,
        page,
        page_size,
        thresholds=get_policy().thresholds,
        search=search,
        decision=decision,
        risk_level=str(risk_level) if risk_level else None,
        anomaly_severity=str(anomaly_severity) if anomaly_severity else None,
        merchant_id=merchant_id,
        customer_id=customer_id,
        status=status,
        is_fraud=is_fraud,
        date_from=date_from,
        date_to=date_to,
        min_probability=min_probability,
        max_probability=max_probability,
        sort_by=str(sort_by),
        descending=descending,
    )
    return Page[ExplorerRow](items=[ExplorerRow(**row) for row in result.items], meta=result.meta)


# --------------------------------------------------------------------------
# Transaction detail
# --------------------------------------------------------------------------
@router.get(
    "/transactions/{transaction_id}/detail",
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
    response_model=TransactionDetailResponse,
    summary="The complete risk pipeline for one transaction",
    responses={404: {"description": "No such transaction"}},
)
def get_transaction_detail(
    transaction_id: str, session: Session = Depends(get_db)
) -> TransactionDetailResponse:
    """Payment facts, both model signals, the investigation, the decision and the audit trail.

    One request rather than six, so the detail page renders without a waterfall.
    """
    transaction = catalog.get_transaction(session, transaction_id)
    return TransactionDetailResponse(**detail.build_detail(session, transaction))


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
@router.get(
    "/audit",
    dependencies=[Depends(require(Permission.AUDIT_READ))],
    response_model=Page[AuditEntry],
    summary="The audit trail, filtered and paginated",
)
def list_audit(
    session: Session = Depends(get_db),
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    event_type: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]*$")] = None,
    actor_type: Annotated[ActorType | None, Query()] = None,
    transaction_id: ReferenceParam = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
) -> Page[AuditEntry]:
    """Every recorded event, newest first.

    Enough per row to answer "why did RazorShield make this decision?" without
    opening each entry, with the full event document available when you do.
    """
    statement = audit.statement(
        event_type=event_type,
        actor_type=actor_type,
        transaction_id=transaction_id,
        created_after=created_after,
        created_before=created_before,
    )
    entries, total = audit.page(session, statement, page, page_size)
    total_pages = ceil(total / page_size) if total else 0
    return Page[AuditEntry](
        items=[AuditEntry(**entry) for entry in entries],
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        ),
    )


@router.get(
    "/audit/summary",
    dependencies=[Depends(require(Permission.AUDIT_READ))],
    response_model=AuditSummaryResponse,
    summary="Event counts per type",
)
def audit_summary(session: Session = Depends(get_db)) -> AuditSummaryResponse:
    """One grouped query behind the audit page's filter chips."""
    return AuditSummaryResponse(
        counts=audit.event_type_counts(session),
        known_event_types=list(audit.KNOWN_EVENT_TYPES),
    )


# --------------------------------------------------------------------------
# Policy viewer
# --------------------------------------------------------------------------
@router.get(
    "/policy",
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
    response_model=PolicyResponse,
    summary="The active decision policy, read-only",
    responses={503: {"description": "The policy configuration is invalid"}},
)
def get_active_policy() -> PolicyResponse:
    """What the decision engine is currently enforcing.

    Read-only by design: Phase 7 exposes no write path. Changing policy means
    changing a reviewed, versioned file - not submitting a form.
    """
    try:
        policy = get_policy()
    except PolicyValidationError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The risk policy configuration is invalid.",
        ) from exc

    thresholds = policy.thresholds
    return PolicyResponse(
        policy_version=policy.policy_version,
        description=policy.description,
        source=policy.source,
        thresholds=PolicyThresholds(
            fraud_block=thresholds.fraud_block,
            fraud_high=thresholds.fraud_high,
            fraud_medium=thresholds.fraud_medium,
            anomaly_critical=thresholds.anomaly_critical,
            anomaly_high=thresholds.anomaly_high,
            anomaly_medium=thresholds.anomaly_medium,
        ),
        evidence=PolicyEvidence(
            min_independent_sources_for_block=(policy.evidence.min_independent_sources_for_block),
            min_high_findings_for_review=policy.evidence.min_high_findings_for_review,
            min_investigation_confidence=policy.evidence.min_investigation_confidence,
        ),
        fail_safe=PolicyFailSafe(
            missing_supervised_signal=str(policy.fail_safe.missing_supervised_signal),
            missing_anomaly_signal=str(policy.fail_safe.missing_anomaly_signal),
            missing_investigation=str(policy.fail_safe.missing_investigation),
            require_investigation_for_block=policy.fail_safe.require_investigation_for_block,
        ),
        action_precedence=[str(action) for action in policy.actions.precedence],
        default_action=str(policy.actions.default),
        human_review_required_for=sorted(
            str(action) for action in policy.actions.human_review_required_for
        ),
        rules=[
            PolicyRuleRead(
                rule_id=rule_id,
                action=RULE_PRIMARY_ACTION.get(rule_id, "unknown"),
                enabled=policy.is_enabled(rule_id),
                description=rule_description(rule_id),
            )
            for rule_id in sorted(KNOWN_RULE_IDS)
        ],
        reason_codes=[str(code) for code in ReasonCode],
    )


# --------------------------------------------------------------------------
# System health
# --------------------------------------------------------------------------
def _database_health(session: Session) -> ComponentHealth:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - the status *is* the error handling
        return ComponentHealth(name="database", status="unavailable", detail=type(exc).__name__)
    return ComponentHealth(name="database", status="ok")


def _model_health(name: str, loader: Any) -> ComponentHealth:
    """Report whether a model artefact can be loaded, without loading it twice."""
    try:
        predictor = loader()
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(name=name, status="unavailable", detail=type(exc).__name__)
    version = getattr(predictor, "model_version", None)
    return ComponentHealth(name=name, status="ok", version=version)


@router.get(
    "/system/health",
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
    response_model=SystemHealthResponse,
    summary="Status of every subsystem the console depends on",
)
def system_health(session: Session = Depends(get_db)) -> SystemHealthResponse:
    """Roll up the health of each subsystem.

    Reuses the same loaders the risk endpoints use rather than reimplementing
    availability checks - if a model loads here it loads there.
    """
    components = [
        # `version` carries the deployed commit when the platform supplies one.
        # It answers the question a status page otherwise cannot: whether the
        # code running here is the code that was last pushed.
        ComponentHealth(
            name="backend",
            status="ok",
            version=get_settings().build_commit,
        ),
        _database_health(session),
        _model_health("fraud_model", get_predictor),
        _model_health("anomaly_model", get_anomaly_predictor),
    ]

    try:
        settings = get_agent_settings()
        is_mock = str(settings.provider) == "mock"
        components.append(
            ComponentHealth(
                name="investigation_agent",
                status="ok",
                detail=("deterministic mock provider" if is_mock else "live provider"),
                version=str(settings.provider),
            )
        )
    except Exception as exc:  # noqa: BLE001
        components.append(
            ComponentHealth(
                name="investigation_agent", status="unavailable", detail=type(exc).__name__
            )
        )

    try:
        policy = get_policy()
        components.append(
            ComponentHealth(name="policy_engine", status="ok", version=policy.policy_version)
        )
    except PolicyValidationError as exc:
        components.append(
            ComponentHealth(name="policy_engine", status="unavailable", detail=str(exc)[:200])
        )

    worst = "ok"
    for component in components:
        if component.status == "unavailable":
            worst = "unavailable"
            break
        if component.status == "degraded":
            worst = "degraded"

    return SystemHealthResponse(status=worst, components=components)
