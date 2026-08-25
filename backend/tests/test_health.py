"""Contract tests for the health endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.db.session import get_db


def test_health_returns_the_documented_payload(client) -> None:  # noqa: ANN001
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "razorshield-backend"}


def test_database_health_reports_a_reachable_database(client) -> None:  # noqa: ANN001
    response = client.get("/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["detail"] is None


def test_database_health_degrades_when_the_database_is_unreachable(app, client) -> None:  # noqa: ANN001
    class BrokenSession:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/health/db")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"
    assert body["detail"]


@pytest.mark.parametrize("path", ["/health", "/health/db"])
def test_health_endpoints_are_documented(client, path: str) -> None:  # noqa: ANN001
    schema = client.get("/openapi.json").json()
    assert path in schema["paths"]
