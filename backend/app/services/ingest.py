"""The live pipeline: one transaction, end to end, through the existing services.

This module contains **no risk logic**. It persists a transaction, then calls
Phase 3, Phase 4, Phase 5 and Phase 6 in order and records what each one did.
Every number it reports was computed by a service that already existed; if this
file disagreed with the batch path about a transaction, that would be a bug in
this file.

Three properties are load-bearing:

* **Idempotent.** ``transactions.transaction_id`` is unique. A repeated event
  returns the first result instead of producing a second decision.
* **Fail-safe.** If a model is unavailable the pipeline does not skip to an
  approval - it lets Phase 6's own fail-safe rules see a missing signal and
  decide accordingly, which is `REVIEW`, never `APPROVE`.
* **Ordered.** Each stage emits an event carrying its position, so a consumer
  can tell a missing stage from a slow one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import correlation_scope, get_correlation_id, new_correlation_id
from app.core.metrics import (
    processing_latency,
    transactions_duplicate_total,
    transactions_failed_total,
    transactions_processed_total,
)
from app.core.observability import LifecycleEvent, log_lifecycle
from app.models import Customer, Device, IpAddress, Merchant, Transaction
from app.models.enums import (
    DeviceType,
    PaymentMethod,
    RiskEventType,
    TransactionStatus,
)
from app.services import anomaly as anomaly_service
from app.services import decision as decision_service
from app.services import events as event_service
from app.services import investigation as investigation_service
from app.services import risk as risk_service
from policy.loader import get_policy
from policy.rules import investigation_warranted

logger = logging.getLogger(__name__)

#: Any ORM entity `_first_seen` can get-or-create.
_Entity = TypeVar("_Entity")

#: Prefix marking a transaction as simulator-generated. Chosen over a schema
#: column so a 20,000-row table needs no migration, and so the marking is
#: visible in every log line, URL and audit entry without a join. Nothing may
#: present a transaction carrying this prefix as production traffic.
SIMULATED_PREFIX = "SIM_"


def is_simulated(reference: str) -> bool:
    return reference.startswith(SIMULATED_PREFIX)


@dataclass
class IngestResult:
    """What the pipeline did with one transaction."""

    reference: str
    transaction_id: int
    duplicate: bool = False
    fraud_probability: float | None = None
    risk_score: int | None = None
    anomaly_score: int | None = None
    anomaly_severity: str | None = None
    investigated: bool = False
    investigation_id: str | None = None
    decision: str | None = None
    decision_id: str | None = None
    matched_rules: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    requires_human_review: bool = False
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    total_ms: float = 0.0
    error: str | None = None
    #: Which stage raised, when ``error`` is set. Names match the metric label
    #: and the keys of ``stage_latencies_ms``.
    failed_stage: str | None = None
    correlation_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TransactionEvent:
    """A validated inbound payment event.

    Deliberately not the ORM model. An ingestion endpoint that accepted
    arbitrary column names would let a caller set ``is_fraud`` - the ground
    truth label - or write a risk score directly. This carries only the fields
    a payment processor would legitimately know.
    """

    transaction_id: str
    amount: Decimal
    currency: str
    customer_id: str
    merchant_id: str
    payment_method: PaymentMethod
    country: str
    city: str
    timestamp: datetime
    device_id: str | None = None
    device_type: DeviceType | None = None
    ip_address: str | None = None
    ip_country: str | None = None
    ip_is_proxy: bool = False
    status: TransactionStatus = TransactionStatus.PENDING
    failed_attempts: int = 0


class IngestError(RuntimeError):
    """The event could not be ingested."""


class PipelineStageError(RuntimeError):
    """A pipeline stage failed, remembering *which* one.

    Without this the ``except Exception`` in :func:`ingest` knows only that
    something broke. Which service broke is the first question anyone asks
    during an incident, and it is the label the failure metric needs, so the
    stage is captured at the point the call is made rather than guessed
    afterwards from how far the latency map got.
    """

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage} failed: {type(cause).__name__}: {cause}")


@contextmanager
def _stage(name: str, result: IngestResult) -> Iterator[None]:
    """Time one stage into the result, and label any failure with its name.

    The Prometheus histogram for each stage is observed by the service that owns
    it, not here: the batch path and the HTTP endpoints call those services too,
    and a histogram fed from only one of three entry points would misreport.
    ``stage_latencies_ms`` is a different thing - it is this run's timings,
    returned to this caller in the ingestion response.
    """
    started = time.perf_counter()
    try:
        yield
    except IngestError:
        # A bad merchant reference is the caller's error, not a stage failure.
        raise
    except Exception as exc:
        raise PipelineStageError(name, exc) from exc
    result.stage_latencies_ms[name] = (time.perf_counter() - started) * 1000


# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------
def _first_seen(session: Session, statement: Select[Any], build: Callable[[], _Entity]) -> _Entity:
    """Get-or-create an entity that several pipeline workers may first-see at once.

    The obvious shape - SELECT, and INSERT when it misses - is a race, and the
    simulator is built to lose it. Three workers process the queue concurrently,
    each on its own ``Session``, and a scenario deliberately reuses one customer
    across a burst of transactions. The burst's first few events therefore run
    the miss-then-insert path simultaneously for the same identifier, and
    PostgreSQL enforces the unique constraint against the row the other worker
    has not yet committed. One worker wins; the rest raise ``IntegrityError``
    and lose their transaction to the pipeline's failure path.

    That is not a hypothetical. It cost two of every twelve simulated
    transactions - concentrated in the opening seconds of a run, which is
    exactly when someone is watching the live feed.

    The insert therefore runs inside a SAVEPOINT. A constraint violation rolls
    back only that savepoint, leaving the surrounding transaction usable, and
    the row the winner committed is then visible to a second SELECT. If that
    still finds nothing the violation was some other constraint, and it is
    re-raised rather than swallowed.
    """
    existing = session.scalar(statement)
    if existing is not None:
        return existing

    try:
        with session.begin_nested():
            entity = build()
            session.add(entity)
            session.flush()
        return entity
    except IntegrityError:
        # The savepoint is already rolled back; the session is usable again.
        winner = session.scalar(statement)
        if winner is None:
            raise
        logger.debug("Lost a first-seen race; using the row the other worker created")
        return winner


def _resolve_merchant(session: Session, external_id: str) -> Merchant:
    merchant = session.scalar(select(Merchant).where(Merchant.external_merchant_id == external_id))
    if merchant is None:
        raise IngestError(f"unknown merchant {external_id!r}")
    return merchant


def _resolve_customer(session: Session, external_id: str, merchant: Merchant) -> Customer:
    """Find the customer, or create one for a first-seen identifier.

    A live stream sees customers the seeded dataset never had. Creating them is
    correct - a real processor does the same on first contact - and the point-in
    -time feature layer handles a customer with no history exactly as it handles
    any new account.
    """
    return _first_seen(
        session,
        select(Customer).where(Customer.external_customer_id == external_id),
        lambda: Customer(
            merchant_id=merchant.id,
            external_customer_id=external_id,
            email=f"{external_id.lower()}@simulated.invalid",
            account_created_at=datetime.now(UTC),
            country="IN",
            city="Mumbai",
        ),
    )


def _resolve_device(
    session: Session, fingerprint: str | None, device_type: DeviceType | None, seen_at: datetime
) -> Device | None:
    if not fingerprint:
        return None
    device = _first_seen(
        session,
        select(Device).where(Device.device_id == fingerprint),
        lambda: Device(
            device_id=fingerprint,
            device_type=device_type or DeviceType.WEB_DESKTOP,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            is_trusted=False,
        ),
    )
    # Keep last_seen_at current; the behavioural features read it.
    if device.last_seen_at < seen_at:
        device.last_seen_at = seen_at
    return device


def _resolve_ip(
    session: Session,
    address: str | None,
    country: str,
    city: str,
    seen_at: datetime,
    *,
    is_proxy: bool,
) -> IpAddress | None:
    if not address:
        return None
    record = _first_seen(
        session,
        select(IpAddress).where(IpAddress.ip_address == address),
        lambda: IpAddress(
            ip_address=address,
            country=country,
            city=city,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            # A proxy is given a poor reputation because that is what the Phase
            # 5 IP tool reads. The value is a simulated property of the address,
            # not a risk judgement about the transaction.
            reputation_score=Decimal("11.50") if is_proxy else Decimal("80.00"),
            is_proxy=is_proxy,
        ),
    )
    if record.last_seen_at < seen_at:
        record.last_seen_at = seen_at
    return record


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------
def _emit(
    session: Session,
    result: IngestResult,
    transaction: Transaction | None,
    event_type: RiskEventType,
    sequence: int,
    payload: dict[str, Any],
) -> None:
    event = event_service.record_event(
        session,
        transaction=transaction,
        reference=result.reference,
        event_type=event_type,
        transaction_sequence=sequence,
        payload={**payload, "simulated": is_simulated(result.reference)},
    )
    result.events.append(event_service.event_as_dict(event))


def _existing(session: Session, reference: str) -> Transaction | None:
    return session.scalar(select(Transaction).where(Transaction.transaction_id == reference))


def _duplicate_result(session: Session, transaction: Transaction) -> IngestResult:
    """Report the first run's outcome without re-running anything.

    The point of idempotency here is not just avoiding duplicate rows - it is
    that re-running would produce a *second decision*, and decisions are
    append-only history. Two decisions for one submitted event would be a
    permanent, unexplainable artefact in the audit trail.
    """
    result = IngestResult(
        reference=transaction.transaction_id,
        transaction_id=transaction.id,
        duplicate=True,
    )
    # `build_context` is how Phase 6 reads the stored signals. Reusing it here
    # means a duplicate reports exactly what the decision engine would see,
    # rather than a second reading of the same tables that could drift from it.
    context = decision_service.build_context(session, transaction)
    if context.supervised.available:
        result.fraud_probability = context.supervised.fraud_probability
        result.risk_score = context.supervised.risk_score
    if context.anomaly.available:
        result.anomaly_score = context.anomaly.anomaly_score
        result.anomaly_severity = context.anomaly.severity

    investigation = investigation_service.load_latest_for_transaction(session, transaction)
    if investigation is not None:
        result.investigated = True
        result.investigation_id = investigation.public_id

    history = decision_service.load_history(session, transaction)
    if history:
        latest = history[-1]
        result.decision = str(latest.action).upper()
        result.decision_id = latest.public_id
        result.matched_rules = list(latest.matched_rules)
        result.reason_codes = list(latest.reason_codes)
        result.requires_human_review = bool(latest.requires_human_review)
    return result


def ingest(session: Session, event: TransactionEvent) -> IngestResult:
    """Run one transaction through the whole risk pipeline.

    Commits on success. On failure the session is rolled back, a
    ``processing_failed`` event is recorded in a fresh transaction, and the
    error is returned rather than raised - a live stream must report a bad
    event, not stop.
    """
    # A simulator worker has no request to inherit an id from; an HTTP caller
    # does. Reusing the ambient one when present is what joins the log lines
    # written here to the request that submitted the transaction.
    correlation_id = get_correlation_id() or new_correlation_id()
    with correlation_scope(correlation_id):
        return _ingest_inner(session, event)


def _ingest_inner(session: Session, event: TransactionEvent) -> IngestResult:
    started = time.perf_counter()

    existing = _existing(session, event.transaction_id)
    if existing is not None:
        transactions_duplicate_total.inc()
        log_lifecycle(
            LifecycleEvent.TRANSACTION_DUPLICATE,
            transaction_id=event.transaction_id,
            simulated=is_simulated(event.transaction_id),
        )
        return _duplicate_result(session, existing)

    log_lifecycle(
        LifecycleEvent.TRANSACTION_RECEIVED,
        transaction_id=event.transaction_id,
        simulated=is_simulated(event.transaction_id),
        amount=float(event.amount),
        currency=event.currency,
        merchant_id=event.merchant_id,
    )

    try:
        result = _run_pipeline(session, event)
        session.commit()
    except IngestError:
        # A malformed or unresolvable event is the caller's problem, not a
        # pipeline failure. Re-raised so the route answers 422 rather than
        # reporting a processing error the caller cannot act on.
        session.rollback()
        raise
    except IntegrityError:
        # Lost a race with a concurrent submission of the same reference. The
        # winner's result is the correct answer for both callers.
        session.rollback()
        duplicate = _existing(session, event.transaction_id)
        if duplicate is None:
            raise
        transactions_duplicate_total.inc()
        log_lifecycle(
            LifecycleEvent.TRANSACTION_DUPLICATE,
            transaction_id=event.transaction_id,
            race=True,
        )
        return _duplicate_result(session, duplicate)
    except Exception as exc:  # noqa: BLE001 - the stream must survive one bad event
        session.rollback()
        stage = exc.stage if isinstance(exc, PipelineStageError) else "unknown"
        cause = exc.cause if isinstance(exc, PipelineStageError) else exc
        transactions_failed_total.labels(stage=stage).inc()
        logger.exception("Pipeline failed for %s at stage %s", event.transaction_id, stage)
        log_lifecycle(
            LifecycleEvent.PIPELINE_FAILED,
            level=logging.ERROR,
            transaction_id=event.transaction_id,
            stage=stage,
            error_type=type(cause).__name__,
        )
        failed = IngestResult(
            reference=event.transaction_id,
            transaction_id=0,
            # The exception *class* only. The message is not safe to return:
            # a model loader names the artifact path it could not find, and a
            # driver error can echo the connection string. Both are useful to an
            # operator and are logged above with the correlation id; neither
            # belongs in a response body or on the public event stream.
            error=type(cause).__name__,
            failed_stage=stage,
        )
        _record_failure(session, failed)
        failed.total_ms = (time.perf_counter() - started) * 1000
        return failed

    elapsed = time.perf_counter() - started
    processing_latency.observe(elapsed)
    transactions_processed_total.inc()
    result.total_ms = elapsed * 1000
    result.correlation_id = get_correlation_id()

    for payload in result.events:
        event_service.broker.publish(payload)
    return result


def _record_failure(session: Session, result: IngestResult) -> None:
    """Record the failure in its own transaction, so it survives the rollback."""
    try:
        transaction = _existing(session, result.reference)
        _emit(
            session,
            result,
            transaction,
            RiskEventType.PROCESSING_FAILED,
            sequence=99,
            payload={"error": result.error, "stage": result.failed_stage},
        )
        session.commit()
        for payload in result.events:
            event_service.broker.publish(payload)
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("Could not record failure event for %s", result.reference)


def _run_pipeline(session: Session, event: TransactionEvent) -> IngestResult:
    """Persist, score, detect, investigate when warranted, then decide."""
    # Built before the first stage so a failure in persistence has somewhere to
    # record its timing, and so every stage below writes into one object.
    result = IngestResult(reference=event.transaction_id, transaction_id=0)

    with _stage("persistence", result):
        merchant = _resolve_merchant(session, event.merchant_id)
        customer = _resolve_customer(session, event.customer_id, merchant)
        device = _resolve_device(session, event.device_id, event.device_type, event.timestamp)
        ip_record = _resolve_ip(
            session,
            event.ip_address,
            event.ip_country or event.country,
            event.city,
            event.timestamp,
            is_proxy=event.ip_is_proxy,
        )

    transaction = Transaction(
        transaction_id=event.transaction_id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        device_id=device.id if device else None,
        ip_address_id=ip_record.id if ip_record else None,
        amount=event.amount,
        currency=event.currency,
        payment_method=event.payment_method,
        status=event.status,
        transaction_timestamp=event.timestamp,
        country=event.country,
        city=event.city,
        failed_attempts=event.failed_attempts,
        # Never set from the wire. `is_fraud` is the dataset's ground-truth
        # label; a live event has no business asserting it, and letting one do
        # so would poison every metric that treats it as ground truth.
        is_fraud=False,
    )
    session.add(transaction)
    session.flush()
    result.transaction_id = transaction.id

    sequence = 1
    _emit(
        session,
        result,
        transaction,
        RiskEventType.TRANSACTION_RECEIVED,
        sequence,
        {
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "customer_id": customer.external_customer_id,
            "merchant_id": merchant.external_merchant_id,
            "country": transaction.country,
            "city": transaction.city,
            "device_id": device.device_id if device else None,
            "ip_address": ip_record.ip_address if ip_record else None,
        },
    )

    # --- Phase 3 ---------------------------------------------------------
    with _stage("risk_scoring", result):
        prediction, _ = risk_service.predict_and_store(session, transaction)
    result.fraud_probability = prediction.fraud_probability
    result.risk_score = prediction.risk_score
    sequence += 1
    _emit(
        session,
        result,
        transaction,
        RiskEventType.RISK_SCORED,
        sequence,
        {
            "fraud_probability": prediction.fraud_probability,
            "risk_score": prediction.risk_score,
            "model_version": prediction.model_version,
        },
    )

    # --- Phase 4 ---------------------------------------------------------
    with _stage("anomaly_detection", result):
        anomaly_result, _ = anomaly_service.score_and_store(session, transaction)
    result.anomaly_score = anomaly_result.anomaly_score
    result.anomaly_severity = str(anomaly_result.severity)
    sequence += 1
    _emit(
        session,
        result,
        transaction,
        RiskEventType.ANOMALY_DETECTED,
        sequence,
        {
            "anomaly_score": anomaly_result.anomaly_score,
            "severity": str(anomaly_result.severity),
            "model_version": anomaly_result.model_version,
        },
    )

    # --- Phase 5, only when the policy would want it ---------------------
    with _stage("policy_load", result):
        policy = get_policy()
        context = decision_service.build_context(session, transaction)
    if investigation_warranted(context, policy):
        sequence += 1
        _emit(
            session,
            result,
            transaction,
            RiskEventType.INVESTIGATION_STARTED,
            sequence,
            {
                "reason": "fraud probability or anomaly score is elevated",
                "fraud_probability": result.fraud_probability,
                "anomaly_score": result.anomaly_score,
            },
        )
        with _stage("investigation", result):
            agent_result, investigation_row = investigation_service.run_investigation(
                session, transaction
            )
        result.investigated = True
        result.investigation_id = investigation_row.public_id
        sequence += 1
        _emit(
            session,
            result,
            transaction,
            RiskEventType.INVESTIGATION_COMPLETED,
            sequence,
            {
                "investigation_id": investigation_row.public_id,
                "status": str(agent_result.status),
                "risk_level": str(agent_result.risk_level),
                "confidence": agent_result.confidence,
                "findings": len(agent_result.findings),
                "evidence": len(agent_result.evidence),
                "agent_is_mock": agent_result.llm.is_mock,
            },
        )

    # --- Phase 6 ---------------------------------------------------------
    with _stage("decision", result):
        policy_result, decision_row = decision_service.decide_and_store(
            session, transaction, policy=policy
        )
    result.decision = str(policy_result.action)
    result.decision_id = decision_row.public_id
    result.matched_rules = list(policy_result.matched_rules)
    result.reason_codes = list(policy_result.reason_codes)
    result.requires_human_review = policy_result.requires_human_review
    sequence += 1
    _emit(
        session,
        result,
        transaction,
        RiskEventType.DECISION_CREATED,
        sequence,
        {
            "decision": str(policy_result.action),
            "decision_id": decision_row.public_id,
            "policy_version": policy_result.policy_version,
            "matched_rules": list(policy_result.matched_rules),
            "reason_codes": list(policy_result.reason_codes),
            "requires_human_review": policy_result.requires_human_review,
            "fraud_probability": result.fraud_probability,
            "anomaly_score": result.anomaly_score,
        },
    )

    return result


__all__ = [
    "SIMULATED_PREFIX",
    "IngestError",
    "IngestResult",
    "TransactionEvent",
    "ingest",
    "is_simulated",
]
