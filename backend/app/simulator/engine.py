"""The simulator: a producer, a bounded queue, and worker threads.

Shape of the thing:

    generator task  ->  asyncio.Queue(maxsize=N)  ->  worker tasks  ->  pipeline

The queue is bounded and the producer *awaits* space on it. That is the whole
backpressure story: when the pipeline is slower than the requested rate, the
producer stops producing rather than growing a list in memory until the process
dies. Nothing is dropped, because nothing is accepted that cannot be queued -
and the observed rate falling below the requested one is itself the signal that
the backend is saturated, exposed as `queue_depth` and `observed_tps`.

The pipeline is synchronous SQLAlchemy, so workers hand each event to a thread
via ``anyio.to_thread``. Each worker owns its own ``Session``: sharing one
across threads is unsafe, and sharing one across events would mean a single
failure poisoning the next transaction's unit of work.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio

from app.db.session import SessionLocal
from app.models.enums import SimulatorScenario, SimulatorState
from app.services import ingest as ingest_service
from app.simulator.scenarios import ScenarioGenerator

logger = logging.getLogger(__name__)

#: Hard ceilings. A simulator that can be asked for 10,000 transactions per
#: second is a denial-of-service endpoint with a friendly name.
MAX_RATE = 50.0
MIN_RATE = 0.1
MAX_TRANSACTIONS = 5_000
DEFAULT_TRANSACTIONS = 50
DEFAULT_RATE = 2.0

#: Queue depth. Deep enough to absorb a slow investigation without stalling the
#: producer; shallow enough that a stall is visible within seconds.
QUEUE_SIZE = 32

#: Concurrent pipeline workers. The pipeline is database- and model-bound, and
#: past three the connection pool becomes the constraint rather than the CPU.
WORKER_COUNT = 3

#: Window for the observed-rate calculation.
RATE_WINDOW_SECONDS = 10.0


@dataclass
class SimulatorConfig:
    """A validated run request."""

    scenario: SimulatorScenario = SimulatorScenario.NORMAL
    transactions_per_second: float = DEFAULT_RATE
    max_transactions: int = DEFAULT_TRANSACTIONS
    seed: int = 42
    merchant_id: str | None = None

    def validated(self) -> SimulatorConfig:
        if not MIN_RATE <= self.transactions_per_second <= MAX_RATE:
            raise ValueError(f"transactions_per_second must be between {MIN_RATE} and {MAX_RATE}")
        if not 1 <= self.max_transactions <= MAX_TRANSACTIONS:
            raise ValueError(f"max_transactions must be between 1 and {MAX_TRANSACTIONS}")
        return self


@dataclass
class SimulatorMetrics:
    """Counters for one run. Every value is observed, none is estimated."""

    started_at: datetime | None = None
    stopped_at: datetime | None = None
    generated: int = 0
    processed: int = 0
    duplicates: int = 0
    failed: int = 0
    approve: int = 0
    step_up: int = 0
    review: int = 0
    block: int = 0
    high_risk: int = 0
    investigations: int = 0
    #: Completion timestamps, for the observed-rate window.
    completions: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))

    def record(self, result: ingest_service.IngestResult) -> None:
        self.processed += 1
        self.completions.append(time.monotonic())
        self.latencies_ms.append(result.total_ms)

        if result.error is not None:
            self.failed += 1
            return
        if result.duplicate:
            self.duplicates += 1
            return
        if result.investigated:
            self.investigations += 1

        action = (result.decision or "").upper()
        if action == "APPROVE":
            self.approve += 1
        elif action == "STEP_UP":
            self.step_up += 1
        elif action == "REVIEW":
            self.review += 1
        elif action == "BLOCK":
            self.block += 1
        if result.requires_human_review:
            self.high_risk += 1

    def observed_tps(self) -> float:
        """Completions per second over the recent window, not since start.

        A run that was paused for a minute would show a misleadingly low
        average; this reports what the pipeline is doing now.
        """
        if not self.completions:
            return 0.0
        cutoff = time.monotonic() - RATE_WINDOW_SECONDS
        recent = [stamp for stamp in self.completions if stamp >= cutoff]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        return (len(recent) - 1) / span if span > 0 else 0.0

    def latency_p50(self) -> float | None:
        return self._percentile(0.50)

    def latency_p95(self) -> float | None:
        return self._percentile(0.95)

    def _percentile(self, fraction: float) -> float | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = max(0, min(len(ordered) - 1, int(fraction * len(ordered)) - 1))
        return ordered[index]


class SimulatorEngine:
    """Lifecycle and orchestration for one simulator run at a time."""

    def __init__(self) -> None:
        self._state = SimulatorState.IDLE
        self._config: SimulatorConfig | None = None
        self._metrics = SimulatorMetrics()
        self._queue: asyncio.Queue[ingest_service.TransactionEvent] | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._resume = asyncio.Event()
        self._resume.set()
        self._lock = asyncio.Lock()
        self._run_id: str | None = None
        self._last_results: deque[dict[str, Any]] = deque(maxlen=25)

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> SimulatorState:
        return self._state

    @property
    def metrics(self) -> SimulatorMetrics:
        return self._metrics

    def status(self) -> dict[str, Any]:
        config = self._config
        queue_depth = self._queue.qsize() if self._queue is not None else 0
        uptime = None
        if self._metrics.started_at is not None:
            end = self._metrics.stopped_at or datetime.now(UTC)
            uptime = (end - self._metrics.started_at).total_seconds()

        return {
            "state": str(self._state),
            "run_id": self._run_id,
            "scenario": str(config.scenario) if config else None,
            "transactions_per_second": config.transactions_per_second if config else None,
            "max_transactions": config.max_transactions if config else None,
            "seed": config.seed if config else None,
            "generated": self._metrics.generated,
            "processed": self._metrics.processed,
            "duplicates": self._metrics.duplicates,
            "failed": self._metrics.failed,
            "queue_depth": queue_depth,
            "queue_capacity": QUEUE_SIZE,
            "observed_tps": round(self._metrics.observed_tps(), 3),
            "latency_p50_ms": self._metrics.latency_p50(),
            "latency_p95_ms": self._metrics.latency_p95(),
            "started_at": self._metrics.started_at,
            "stopped_at": self._metrics.stopped_at,
            "uptime_seconds": uptime,
            "decisions": {
                "approve": self._metrics.approve,
                "step_up": self._metrics.step_up,
                "review": self._metrics.review,
                "block": self._metrics.block,
            },
            "investigations": self._metrics.investigations,
            "recent": list(self._last_results),
        }

    # -- control ----------------------------------------------------------
    async def start(self, config: SimulatorConfig, *, merchant_id: str) -> dict[str, Any]:
        async with self._lock:
            if self._state in (SimulatorState.RUNNING, SimulatorState.PAUSED):
                raise RuntimeError("simulator is already running; stop it first")

            self._config = config.validated()
            self._run_id = uuid.uuid4().hex[:8]
            self._metrics = SimulatorMetrics(started_at=datetime.now(UTC))
            self._queue = asyncio.Queue(maxsize=QUEUE_SIZE)
            self._last_results.clear()
            self._resume.set()
            self._state = SimulatorState.RUNNING

            generator = ScenarioGenerator(
                self._config.scenario,
                seed=self._config.seed,
                merchant_id=merchant_id,
                run_id=self._run_id,
            )
            self._tasks = [asyncio.create_task(self._produce(generator))]
            self._tasks += [asyncio.create_task(self._consume()) for _ in range(WORKER_COUNT)]
            logger.info(
                "Simulator started: run=%s scenario=%s rate=%.2f max=%d",
                self._run_id,
                self._config.scenario,
                self._config.transactions_per_second,
                self._config.max_transactions,
            )
            return self.status()

    async def pause(self) -> dict[str, Any]:
        if self._state is not SimulatorState.RUNNING:
            raise RuntimeError("simulator is not running")
        self._resume.clear()
        self._state = SimulatorState.PAUSED
        return self.status()

    async def resume(self) -> dict[str, Any]:
        if self._state is not SimulatorState.PAUSED:
            raise RuntimeError("simulator is not paused")
        self._resume.set()
        self._state = SimulatorState.RUNNING
        return self.status()

    async def stop(self) -> dict[str, Any]:
        """Cancel cleanly and wait for the workers to finish.

        The resume event is set first: a paused producer is blocked on it and
        would otherwise never observe the cancellation.
        """
        async with self._lock:
            if self._state is SimulatorState.IDLE:
                return self.status()

            self._state = SimulatorState.STOPPING
            self._resume.set()
            for task in self._tasks:
                task.cancel()
            for task in self._tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._tasks.clear()
            self._metrics.stopped_at = datetime.now(UTC)
            self._state = SimulatorState.IDLE
            logger.info("Simulator stopped: run=%s", self._run_id)
            return self.status()

    async def reset(self) -> dict[str, Any]:
        await self.stop()
        self._metrics = SimulatorMetrics()
        self._config = None
        self._run_id = None
        self._last_results.clear()
        return self.status()

    # -- internals --------------------------------------------------------
    async def _produce(self, generator: ScenarioGenerator) -> None:
        """Emit events at the configured rate until the cap is reached."""
        assert self._config is not None
        assert self._queue is not None
        interval = 1.0 / self._config.transactions_per_second

        try:
            while self._metrics.generated < self._config.max_transactions:
                await self._resume.wait()
                event = generator.next_event()
                # Blocks when the queue is full. This is the backpressure: the
                # producer slows to whatever the pipeline can absorb.
                await self._queue.put(event)
                self._metrics.generated += 1
                await asyncio.sleep(interval)

            # Producer is done; drain what is queued, then retire.
            await self._queue.join()
            if self._state is SimulatorState.RUNNING:
                asyncio.create_task(self.stop())  # noqa: RUF006
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Simulator producer failed")
            raise

    async def _consume(self) -> None:
        """Pull events and run each through the pipeline in a worker thread."""
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                result = await anyio.to_thread.run_sync(self._process, event)
                self._metrics.record(result)
                self._last_results.appendleft(
                    {
                        "transaction_id": result.reference,
                        "decision": result.decision,
                        "fraud_probability": result.fraud_probability,
                        "anomaly_score": result.anomaly_score,
                        "investigated": result.investigated,
                        "duplicate": result.duplicate,
                        "error": result.error,
                        "total_ms": round(result.total_ms, 2),
                    }
                )
            except asyncio.CancelledError:
                self._queue.task_done()
                raise
            except Exception:
                logger.exception("Simulator worker failed on %s", event.transaction_id)
                self._metrics.failed += 1
            finally:
                self._queue.task_done()

    @staticmethod
    def _process(event: ingest_service.TransactionEvent) -> ingest_service.IngestResult:
        """Run one event. Its own session, closed whatever happens."""
        with SessionLocal() as session:
            return ingest_service.ingest(session, event)


#: One engine per process, matching the one broker.
engine = SimulatorEngine()


__all__ = [
    "DEFAULT_RATE",
    "DEFAULT_TRANSACTIONS",
    "MAX_RATE",
    "MAX_TRANSACTIONS",
    "MIN_RATE",
    "QUEUE_SIZE",
    "SimulatorConfig",
    "SimulatorEngine",
    "SimulatorMetrics",
    "engine",
]
