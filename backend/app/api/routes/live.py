"""Live ingestion, the simulator, and the server-sent event stream.

What these endpoints cannot do, by construction: change a policy, retrain or
reload a model, alter a historical decision, run arbitrary SQL or Python, or
read a secret. Ingestion accepts a bounded, typed payment event and hands it to
the same pipeline the batch path uses; the simulator only chooses what
behaviour to generate.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ingest_rate_limit, require, simulator_rate_limit
from app.core.permissions import Permission
from app.db.session import get_db
from app.models import Merchant, RiskDecision, RiskEvent, Transaction
from app.models.enums import DecisionAction, SimulatorScenario
from app.schemas.live import (
    EventPage,
    IngestResponse,
    LiveMetrics,
    RiskEventRead,
    ScenarioListResponse,
    ScenarioRead,
    SimulatorStartRequest,
    SimulatorStatus,
    StageLatencies,
    TransactionEventIn,
)
from app.services import events as event_service
from app.services import ingest as ingest_service
from app.simulator.engine import QUEUE_SIZE, SimulatorConfig, engine
from app.simulator.scenarios import SCENARIO_DOCS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live"])


def _to_response(result: ingest_service.IngestResult) -> IngestResponse:
    return IngestResponse(
        transaction_id=result.reference,
        accepted=result.error is None,
        duplicate=result.duplicate,
        simulated=ingest_service.is_simulated(result.reference),
        fraud_probability=result.fraud_probability,
        risk_score=result.risk_score,
        anomaly_score=result.anomaly_score,
        anomaly_severity=result.anomaly_severity,
        investigated=result.investigated,
        investigation_id=result.investigation_id,
        decision=result.decision,
        decision_id=result.decision_id,
        matched_rules=result.matched_rules,
        reason_codes=result.reason_codes,
        requires_human_review=result.requires_human_review,
        stage_latencies_ms=StageLatencies(**result.stage_latencies_ms),
        total_ms=result.total_ms,
        error=result.error,
        failed_stage=result.failed_stage,
        correlation_id=result.correlation_id,
    )


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
@router.post(
    "/events/transactions",
    dependencies=[
        Depends(require(Permission.TRANSACTIONS_INGEST)),
        Depends(ingest_rate_limit),
    ],
    response_model=IngestResponse,
    summary="Submit one payment event to the live risk pipeline",
    responses={
        422: {"description": "The event failed validation"},
        503: {"description": "A risk service required by the pipeline is unavailable"},
    },
)
def ingest_transaction(
    payload: TransactionEventIn, session: Session = Depends(get_db)
) -> IngestResponse:
    """Run one transaction through Phases 3 to 6 and return what they decided.

    Idempotent on ``transaction_id``: submitting the same reference twice
    returns the first result and creates no second decision.

    The response reports the decision the *policy* reached. Nothing in the
    request can influence it beyond describing the payment.
    """
    event = ingest_service.TransactionEvent(
        transaction_id=payload.transaction_id,
        amount=payload.amount,
        currency=payload.currency,
        customer_id=payload.customer_id,
        merchant_id=payload.merchant_id,
        payment_method=payload.payment_method,
        country=payload.country,
        city=payload.city,
        timestamp=payload.timestamp,
        device_id=payload.device_id,
        device_type=payload.device_type,
        ip_address=payload.ip_address,
        ip_country=payload.ip_country,
        ip_is_proxy=payload.ip_is_proxy,
        status=payload.status,
        failed_attempts=payload.failed_attempts,
    )
    try:
        result = ingest_service.ingest(session, event)
    except ingest_service.IngestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _to_response(result)


# --------------------------------------------------------------------------
# The event stream
# --------------------------------------------------------------------------
@router.get(
    "/events/stream",
    dependencies=[
        Depends(require(Permission.EVENTS_READ)),
    ],
    summary="Server-sent stream of live risk events",
    response_class=StreamingResponse,
)
async def stream_events(
    request: Request,
    last_event_id: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "Resume after this sequence number. The browser sends it "
                "automatically as the Last-Event-ID header after a reconnect."
            ),
        ),
    ] = None,
) -> StreamingResponse:
    """Stream events as they happen, resuming without gaps or duplicates.

    A reconnecting client is sent exactly what it missed, from the durable
    ``risk_events`` table, before live delivery resumes. Both the query
    parameter and the standard ``Last-Event-ID`` header are accepted; the header
    wins, because the browser sets it without being asked.
    """
    header_id = request.headers.get("last-event-id")
    resume_from = last_event_id
    if header_id is not None:
        try:
            resume_from = int(header_id)
        except ValueError:
            # A malformed header is not worth failing the connection over; the
            # client simply starts from the recent window instead.
            logger.debug("Ignoring malformed Last-Event-ID %r", header_id)

    # Subscribe *before* reading the backlog. The other order leaves a window
    # in which an event published between the read and the subscribe is in
    # neither, and the client would silently miss it.
    try:
        queue = await event_service.broker.subscribe()
    except event_service.TooManySubscribersError as exc:
        # Refusing is the honest answer: accepting a stream we cannot feed
        # would leave the client showing a live badge over a dead feed.
        logger.warning("Refused SSE subscription: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many live streams are open. Try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc

    from app.db.session import SessionLocal

    with SessionLocal() as session:
        if resume_from is not None:
            backlog = event_service.events_since(session, after_sequence=resume_from)
        else:
            backlog = event_service.recent_events(session, limit=25)

    async def generate() -> AsyncIterator[str]:
        try:
            async for chunk in event_service.sse_stream(queue, backlog=backlog):
                yield chunk
        finally:
            await event_service.broker.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold
            # events until the buffer filled and make the stream look dead.
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/events",
    dependencies=[
        Depends(require(Permission.EVENTS_READ)),
    ],
    response_model=EventPage,
    summary="Recent risk events, or everything after a sequence number",
)
def list_events(
    session: Session = Depends(get_db),
    after: Annotated[
        int | None, Query(ge=0, description="Return events after this sequence")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPage:
    """The durable stream, for a client that cannot hold an SSE connection."""
    if after is not None:
        events = event_service.events_since(session, after_sequence=after, limit=limit)
    else:
        events = event_service.recent_events(session, limit=limit)
    latest = session.scalar(select(func.coalesce(func.max(RiskEvent.sequence), 0))) or 0
    return EventPage(events=[RiskEventRead(**item) for item in events], latest_sequence=int(latest))


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------
def _default_merchant(session: Session) -> str:
    merchant = session.scalars(select(Merchant).order_by(Merchant.id).limit(1)).first()
    if merchant is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No merchant exists to attribute simulated transactions to. "
                "The database has not been bootstrapped: run "
                "`python scripts/bootstrap.py`, or deploy with BOOTSTRAP_ON_START=true."
            ),
        )
    return merchant.external_merchant_id


@router.get(
    "/simulator/scenarios",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
    ],
    response_model=ScenarioListResponse,
    summary="The documented scenarios and what each generates",
)
def list_scenarios() -> ScenarioListResponse:
    """What each scenario produces.

    Each entry describes *behaviour*. None of them names an expected decision,
    because the decision is the pipeline's to make and measuring it is the
    point of running the simulator at all.
    """
    return ScenarioListResponse(
        scenarios=[
            ScenarioRead(
                scenario=str(doc.scenario),
                title=doc.title,
                behaviour=doc.behaviour,
                expected_signal=doc.expected_signal,
            )
            for doc in SCENARIO_DOCS.values()
        ],
        note=(
            "Scenarios control transaction characteristics only. Fraud probability, "
            "anomaly score and the final decision are computed by the Phase 3, 4 and 6 "
            "services from that behaviour and are never set by the simulator."
        ),
    )


@router.post(
    "/simulator/start",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
    response_model=SimulatorStatus,
    summary="Start generating simulated transactions",
    responses={409: {"description": "A run is already in progress"}},
)
async def start_simulator(
    payload: SimulatorStartRequest, session: Session = Depends(get_db)
) -> SimulatorStatus:
    """Begin a bounded run.

    Bounded is the default and not an option: ``max_transactions`` has a
    ceiling, and the run stops when it is reached.
    """
    merchant_id = _default_merchant(session)
    config = SimulatorConfig(
        scenario=payload.scenario,
        transactions_per_second=payload.transactions_per_second,
        max_transactions=payload.max_transactions,
        seed=payload.seed,
    )
    try:
        return SimulatorStatus(**await engine.start(config, merchant_id=merchant_id))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/simulator/stop",
    response_model=SimulatorStatus,
    summary="Stop the current run",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
)
async def stop_simulator() -> SimulatorStatus:
    """Cancel the producer and workers, then wait for them to finish."""
    return SimulatorStatus(**await engine.stop())


@router.post(
    "/simulator/pause",
    response_model=SimulatorStatus,
    summary="Pause generation",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
)
async def pause_simulator() -> SimulatorStatus:
    try:
        return SimulatorStatus(**await engine.pause())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/simulator/resume",
    response_model=SimulatorStatus,
    summary="Resume generation",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
)
async def resume_simulator() -> SimulatorStatus:
    try:
        return SimulatorStatus(**await engine.resume())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/simulator/reset",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
    response_model=SimulatorStatus,
    summary="Stop and clear the run counters",
)
async def reset_simulator() -> SimulatorStatus:
    """Clears counters only. No transaction, decision or event is deleted."""
    return SimulatorStatus(**await engine.reset())


@router.get(
    "/simulator/status",
    response_model=SimulatorStatus,
    summary="Current run state",
    dependencies=[Depends(require(Permission.SIMULATOR_CONTROL))],
)
def simulator_status() -> SimulatorStatus:
    return SimulatorStatus(**engine.status())


@router.post(
    "/simulator/replay/{scenario}",
    dependencies=[
        Depends(require(Permission.SIMULATOR_CONTROL)),
        Depends(simulator_rate_limit),
    ],
    response_model=SimulatorStatus,
    summary="Replay a scenario's behaviour through the live pipeline",
    responses={409: {"description": "A run is already in progress"}},
)
async def replay_scenario(
    scenario: SimulatorScenario,
    session: Session = Depends(get_db),
    transactions: Annotated[int, Query(ge=1, le=200)] = 12,
    transactions_per_second: Annotated[float, Query(ge=0.1, le=10.0)] = 1.0,
    seed: Annotated[int, Query(ge=0, le=2**31 - 1)] = 42,
) -> SimulatorStatus:
    """Re-run a scenario's transaction sequence through the real pipeline.

    This regenerates the *behaviour* and lets Phases 3 to 6 decide afresh. It
    does not copy the stored decisions of the seeded scenario - replaying a
    recorded answer would demonstrate nothing.
    """
    merchant_id = _default_merchant(session)
    config = SimulatorConfig(
        scenario=scenario,
        transactions_per_second=transactions_per_second,
        max_transactions=transactions,
        seed=seed,
    )
    try:
        return SimulatorStatus(**await engine.start(config, merchant_id=merchant_id))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Live metrics
# --------------------------------------------------------------------------
@router.get(
    "/live/metrics",
    dependencies=[
        Depends(require(Permission.EVENTS_READ)),
    ],
    response_model=LiveMetrics,
    summary="Live counters for the simulated stream",
)
def live_metrics(session: Session = Depends(get_db)) -> LiveMetrics:
    """Counters over simulated transactions, from the database and the engine.

    Scoped to simulated traffic on purpose: mixing 20,000 seeded transactions
    into a "live" counter would make the number meaningless the moment the page
    loaded.
    """
    prefix = f"{ingest_service.SIMULATED_PREFIX}%"
    status_snapshot = engine.status()

    rows = session.execute(
        select(RiskDecision.action, func.count())
        .select_from(RiskDecision)
        .join(Transaction, Transaction.id == RiskDecision.transaction_id)
        .where(Transaction.transaction_id.like(prefix))
        .group_by(RiskDecision.action)
    ).all()
    by_action = {str(action): int(count) for action, count in rows}

    review = by_action.get(str(DecisionAction.REVIEW), 0)
    block = by_action.get(str(DecisionAction.BLOCK), 0)

    investigations = (
        session.scalar(
            select(func.count(func.distinct(RiskEvent.transaction_id))).where(
                RiskEvent.event_type == "investigation_completed",
                RiskEvent.transaction_reference.like(prefix),
            )
        )
        or 0
    )
    total_events = session.scalar(select(func.count(RiskEvent.id))) or 0
    latest = session.scalar(select(func.coalesce(func.max(RiskEvent.sequence), 0))) or 0

    return LiveMetrics(
        transactions_processed=sum(by_action.values()),
        transactions_per_second=status_snapshot["observed_tps"],
        # "High risk" is the policy's own definition: it asked for a human.
        high_risk_count=review + block,
        review_count=review,
        block_count=block,
        approve_count=by_action.get(str(DecisionAction.APPROVE), 0),
        step_up_count=by_action.get(str(DecisionAction.STEP_UP), 0),
        active_investigations=int(investigations),
        queue_depth=status_snapshot["queue_depth"],
        queue_capacity=QUEUE_SIZE,
        uptime_seconds=status_snapshot["uptime_seconds"],
        simulator_state=status_snapshot["state"],
        scenario=status_snapshot["scenario"],
        connected_clients=event_service.broker.subscriber_count,
        dropped_deliveries=event_service.broker.dropped_deliveries,
        total_events=int(total_events),
        latest_sequence=int(latest),
    )
