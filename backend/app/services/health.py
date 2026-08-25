"""Health probes used by the API layer.

Liveness and readiness answer different questions and must not be conflated:

* **Liveness** - is this process capable of serving? If the answer is no, the
  only remedy is a restart, so a liveness probe must depend on nothing but the
  process itself. Wiring PostgreSQL into liveness is a classic outage
  amplifier: the database blips, every replica fails its liveness probe, the
  orchestrator kills every replica, and now there is no capacity to serve the
  requests that *would* have worked once the database came back.
* **Readiness** - should traffic be routed here *right now*? This one does
  depend on the dependencies, because the remedy is to route elsewhere and wait.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class DatabaseProbe:
    """Result of a single database connectivity check."""

    __slots__ = ("connected", "detail")

    def __init__(self, connected: bool, detail: str | None = None) -> None:
        self.connected = connected
        self.detail = detail


def probe_database(session: Session) -> DatabaseProbe:
    """Issue a trivial query to confirm the PostgreSQL connection is usable.

    Never raises: an unreachable database is reported as a degraded state rather
    than a 500, so orchestrators can distinguish "process dead" from
    "dependency down".
    """
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database health probe failed: %s", exc.__class__.__name__)
        return DatabaseProbe(connected=False, detail=f"{exc.__class__.__name__}: {exc}")
    return DatabaseProbe(connected=True)


@dataclass(frozen=True)
class DependencyStatus:
    """One readiness check: does this dependency work at all?"""

    name: str
    ready: bool
    #: An exception *class* name, never its message. A model loader's message
    #: can name filesystem paths, and this endpoint is unauthenticated.
    detail: str | None = None


@dataclass(frozen=True)
class ReadinessProbe:
    """Whether this replica should be sent traffic."""

    ready: bool
    dependencies: tuple[DependencyStatus, ...]

    @property
    def not_ready(self) -> tuple[str, ...]:
        return tuple(dep.name for dep in self.dependencies if not dep.ready)


def probe_readiness(session: Session) -> ReadinessProbe:
    """Check every dependency a request could need before answering.

    The two models and the policy configuration are checked through the same
    loaders the risk endpoints use, so "ready" means the pipeline would actually
    run - not merely that the process started. All three are cached after first
    load, so a warm replica pays almost nothing per probe.
    """
    from ml.anomaly.predictor import get_anomaly_predictor
    from ml.inference.predictor import get_predictor
    from policy.loader import get_policy

    dependencies: list[DependencyStatus] = []

    probe = probe_database(session)
    dependencies.append(
        DependencyStatus(
            name="database",
            ready=probe.connected,
            # `probe.detail` embeds the driver error, which can echo the DSN.
            detail=None if probe.connected else "unreachable",
        )
    )

    for name, loader in (
        ("fraud_model", get_predictor),
        ("anomaly_model", get_anomaly_predictor),
        ("policy", get_policy),
    ):
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - any load failure means not ready
            logger.warning("Readiness dependency %s unavailable: %s", name, type(exc).__name__)
            dependencies.append(DependencyStatus(name=name, ready=False, detail=type(exc).__name__))
        else:
            dependencies.append(DependencyStatus(name=name, ready=True))

    return ReadinessProbe(
        ready=all(dep.ready for dep in dependencies),
        dependencies=tuple(dependencies),
    )
