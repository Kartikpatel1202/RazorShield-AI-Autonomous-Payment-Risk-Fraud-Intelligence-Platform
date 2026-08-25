"""Liveness and readiness endpoints.

Unauthenticated by design. An orchestrator's probe has no credential to present,
and gating liveness behind a token means a token problem presents as a dead
process. Nothing here returns anything an anonymous caller could not learn by
watching whether the service answers at all: no versions, no paths, no error
messages, no counts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.health import (
    DatabaseHealthResponse,
    HealthResponse,
    ReadinessResponse,
)
from app.services.health import probe_database, probe_readiness

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service liveness")
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return ``ok`` whenever the API process is able to serve requests."""
    return HealthResponse(status="ok", service=settings.service_name)


@router.get("/health/live", response_model=HealthResponse, summary="Liveness probe")
def health_live(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Answer ``ok`` if this process can run code and route a request.

    Touches no dependency - not the database, not a model, not the filesystem.
    A failing liveness probe means "restart me", and restarting a healthy
    process because PostgreSQL is briefly unavailable turns a dependency blip
    into an outage of everything. Readiness is the probe that considers
    dependencies.
    """
    return HealthResponse(status="ok", service=settings.service_name)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
def health_ready(
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """Report whether this replica should receive traffic.

    Checks the database, both models and the policy configuration. Returns 503
    when any of them is down, because a load balancer acts on the status code -
    a 200 body saying ``ready: false`` would keep traffic arriving.
    """
    probe = probe_readiness(session)
    if not probe.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ok" if probe.ready else "unavailable",
        service=settings.service_name,
        ready=probe.ready,
        dependencies={
            dep.name: ("ok" if dep.ready else "unavailable") for dep in probe.dependencies
        },
    )


@router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    summary="Database connectivity",
)
def health_database(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DatabaseHealthResponse:
    """Verify the FastAPI -> SQLAlchemy -> PostgreSQL path end to end."""
    probe = probe_database(session)
    if probe.connected:
        return DatabaseHealthResponse(
            status="ok", service=settings.service_name, database="connected"
        )

    # Connection errors can echo back the DSN, so the detail is only surfaced
    # outside production.
    detail = probe.detail if settings.environment != "production" else "database unreachable"
    return DatabaseHealthResponse(
        status="degraded",
        service=settings.service_name,
        database="unavailable",
        detail=detail,
    )
