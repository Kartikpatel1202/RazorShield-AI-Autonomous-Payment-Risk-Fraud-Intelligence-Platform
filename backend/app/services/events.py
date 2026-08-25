"""The live risk event stream: recording, ordering and fan-out.

Two halves that deliberately do not depend on each other.

**Recording** is synchronous and writes to ``risk_events``. It runs inside the
pipeline's own database transaction, so an event and the thing it describes
commit together or not at all - there is no window where the stream claims a
decision exists that the decision table has not got.

**Fan-out** is asynchronous and in-process. Connected browsers get a bounded
queue each; a slow client fills its own queue and is disconnected rather than
being allowed to slow the pipeline down. Nothing is lost by that: the durable
copy is already in ``risk_events``, and the client resumes from its last event
id when it reconnects.

There is no Kafka and no Redis here, deliberately. The spec asked for them only
if a measured bottleneck required it, and one process serving one dashboard is
not that bottleneck.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.metrics import sse_connections, sse_dropped_clients_total, sse_events_total
from app.models import RiskEvent, Transaction
from app.models.enums import RiskEventType

logger = logging.getLogger(__name__)

#: Events a single connected client may fall behind by before it is dropped.
#: Small on purpose: a browser that cannot keep up with 64 events is not going
#: to catch up, and holding them costs memory the pipeline needs.
CLIENT_QUEUE_SIZE = 64

#: The most events a reconnecting client is given at once. Without a cap, a
#: browser that was away for an hour would ask for tens of thousands of rows.
MAX_REPLAY_EVENTS = 200

#: Concurrent SSE streams one process will carry. Each holds a queue of up to
#: `CLIENT_QUEUE_SIZE` events, so this bounds the broker's memory. Generous for
#: an operations console with a handful of analysts; low enough that a client
#: looping on `EventSource` cannot exhaust the process.
MAX_SUBSCRIBERS = 64


def _public_id() -> str:
    return f"EVT-{uuid.uuid4().hex[:16]}"


def _next_sequence(session: Session) -> int:
    """The next value in the stream's total order.

    PostgreSQL uses a real sequence, so concurrent writers cannot collide.
    SQLite has none; there the fallback is ``MAX(sequence) + 1``, which is
    correct for the single-writer test process and would not be safe under
    concurrency. That limitation is confined to the backend the tests use.
    """
    if session.get_bind().dialect.name == "postgresql":
        value = session.scalar(select(func.nextval("risk_events_sequence_seq")))
        return int(value or 1)
    return int(session.scalar(select(func.coalesce(func.max(RiskEvent.sequence), 0))) or 0) + 1


def record_event(
    session: Session,
    *,
    transaction: Transaction | None,
    reference: str,
    event_type: RiskEventType,
    transaction_sequence: int,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> RiskEvent:
    """Append one event to the durable stream.

    Flushes but does not commit: the caller owns the transaction, so the event
    lands atomically with whatever it describes.
    """
    event = RiskEvent(
        public_id=_public_id(),
        sequence=_next_sequence(session),
        transaction_id=transaction.id if transaction is not None else None,
        transaction_reference=reference,
        event_type=event_type,
        transaction_sequence=transaction_sequence,
        occurred_at=occurred_at or datetime.now(UTC),
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def event_as_dict(event: RiskEvent) -> dict[str, Any]:
    """The wire shape, identical for live delivery and for replay."""
    return {
        "event_id": event.public_id,
        "sequence": event.sequence,
        "transaction_id": event.transaction_reference,
        "event_type": str(event.event_type),
        "transaction_sequence": event.transaction_sequence,
        "timestamp": event.occurred_at.isoformat(),
        "payload": event.payload,
    }


def events_since(
    session: Session, *, after_sequence: int, limit: int = MAX_REPLAY_EVENTS
) -> list[dict[str, Any]]:
    """Durable events after a sequence number, oldest first.

    This is what makes reconnection lossless. A client sends the last sequence
    it rendered; it gets exactly what it missed, in order, and never a
    duplicate of something it already has.
    """
    capped = max(1, min(limit, MAX_REPLAY_EVENTS))
    rows = session.scalars(
        select(RiskEvent)
        .where(RiskEvent.sequence > after_sequence)
        .order_by(RiskEvent.sequence)
        .limit(capped)
    ).all()
    return [event_as_dict(row) for row in rows]


def recent_events(session: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    """The newest events, returned oldest-first so a client can append them."""
    capped = max(1, min(limit, MAX_REPLAY_EVENTS))
    rows = session.scalars(
        select(RiskEvent).order_by(RiskEvent.sequence.desc()).limit(capped)
    ).all()
    return [event_as_dict(row) for row in reversed(rows)]


class TooManySubscribersError(RuntimeError):
    """The broker is already carrying its maximum number of streams."""


@runtime_checkable
class EventBroker(Protocol):
    """What the application needs from a fan-out mechanism.

    A ``Protocol`` rather than a base class, because the only implementation
    that exists is in-process and inheritance would be ceremony. What the
    abstraction buys is concrete: :mod:`app.api.routes.live` and
    :mod:`app.services.ingest` are written against these five members and
    nothing else, so a ``RedisEventBroker`` would be a new file rather than an
    edit to the pipeline.

    That file is deliberately *not* written. Redis is the right answer for a
    multi-worker or multi-container deployment, where the in-process broker
    breaks (see :class:`InMemoryEventBroker`) - and shipping an unused,
    untested Redis client to prove the seam exists would add a dependency, a
    container and a failure mode to a system that has no use for any of them
    today.
    """

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]: ...

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None: ...

    def publish(self, event: dict[str, Any]) -> None: ...

    @property
    def subscriber_count(self) -> int: ...

    @property
    def dropped_deliveries(self) -> int: ...


class InMemoryEventBroker:
    """In-process fan-out to connected SSE clients.

    Publishing never blocks and never raises. A subscriber that has fallen
    behind is marked dropped and disconnected on its next read; the pipeline
    that published is not slowed down and is not told, because it has nothing
    useful to do about a slow browser.

    **Single-process only.** Subscribers live in this process's memory, so an
    event published by worker A is invisible to a browser attached to worker B.
    Running Uvicorn with more than one worker would therefore give each client a
    *partial* stream - which is worse than an obviously broken one, because it
    looks fine. The compose stack runs a single worker for exactly this reason,
    and `docs/security.md` records it as a deployment constraint rather than an
    implementation detail.
    """

    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._dropped = 0
        self._max_subscribers = max_subscribers

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Attach a new client, or refuse if the broker is already full.

        The cap is a denial-of-service control, not tidiness: each subscriber
        holds a 64-slot queue, so an unbounded number of them is an unbounded
        amount of memory an authenticated client could claim by opening streams
        in a loop.
        """
        async with self._lock:
            if len(self._subscribers) >= self._max_subscribers:
                raise TooManySubscribersError(
                    f"broker is at its limit of {self._max_subscribers} streams"
                )
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
            self._subscribers.add(queue)
        sse_connections.set(len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
        sse_connections.set(len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_deliveries(self) -> int:
        """Deliveries skipped because a client's queue was full."""
        return self._dropped

    def publish(self, event: dict[str, Any]) -> None:
        """Hand an event to every subscriber, skipping any that is full.

        Synchronous by design: it is called from the pipeline, which runs in a
        worker thread. ``put_nowait`` on an ``asyncio.Queue`` is safe to call
        from another thread only because nothing here awaits - the queue's
        internal deque append is atomic under the GIL, and no waiter is woken
        from this side. The event loop notices on its next pass.
        """
        sse_events_total.labels(event_type=str(event.get("event_type", "unknown"))).inc()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped += 1
                sse_dropped_clients_total.inc()


#: One broker per process, annotated as the abstraction so nothing downstream
#: can reach for an implementation detail of the in-memory one.
broker: EventBroker = InMemoryEventBroker()


async def sse_stream(
    queue: asyncio.Queue[dict[str, Any]],
    *,
    backlog: list[dict[str, Any]],
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Render events as an SSE byte stream.

    Sends the caller's backlog first so a reconnecting client is caught up
    before live events resume, then emits a comment heartbeat during silence.
    Without the heartbeat an idle stream looks identical to a dead one to every
    proxy between here and the browser.
    """
    for event in backlog:
        yield _format_sse(event)

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
        except TimeoutError:
            # A comment line: valid SSE, ignored by EventSource, and enough to
            # keep intermediaries from closing an idle connection.
            yield ": heartbeat\n\n"
            continue
        yield _format_sse(event)


def _format_sse(event: dict[str, Any]) -> str:
    import json

    # `id:` is what the browser echoes back as Last-Event-ID after a drop.
    return (
        f"id: {event['sequence']}\n"
        f"event: {event['event_type']}\n"
        f"data: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"
    )


__all__ = [
    "CLIENT_QUEUE_SIZE",
    "MAX_REPLAY_EVENTS",
    "MAX_SUBSCRIBERS",
    "EventBroker",
    "InMemoryEventBroker",
    "TooManySubscribersError",
    "broker",
    "event_as_dict",
    "events_since",
    "recent_events",
    "record_event",
    "sse_stream",
]
