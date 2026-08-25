"""Response models for the service health endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DatabaseStatus = Literal["connected", "unavailable"]


class HealthResponse(BaseModel):
    """Liveness payload: the process is up and serving requests."""

    status: Literal["ok"] = "ok"
    service: str = Field(examples=["razorshield-backend"])


class DatabaseHealthResponse(BaseModel):
    """Readiness payload: the service can reach PostgreSQL."""

    status: Literal["ok", "degraded"]
    service: str = Field(examples=["razorshield-backend"])
    database: DatabaseStatus
    detail: str | None = Field(
        default=None,
        description="Error summary when the database is unreachable; null otherwise.",
    )


class ReadinessResponse(BaseModel):
    """Readiness payload: every dependency a request could need.

    Values are the bare words ``ok`` and ``unavailable``. The endpoint is
    unauthenticated, so it deliberately carries no version, path or error text
    that would help someone decide what to attack.
    """

    status: Literal["ok", "unavailable"]
    service: str = Field(examples=["razorshield-backend"])
    ready: bool
    dependencies: dict[str, Literal["ok", "unavailable"]] = Field(
        description="Dependency name to 'ok' or 'unavailable'.",
        examples=[{"database": "ok", "fraud_model": "ok", "anomaly_model": "ok", "policy": "ok"}],
    )
