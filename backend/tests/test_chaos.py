"""Failure paths: what breaks, what the caller sees, and what gets written.

One rule governs all of it: **a failure never becomes an approval.** There is no
code path in which "the fraud model was unavailable" turns into a payment being
let through. Either Phase 6's fail-safe rules see a missing signal and decide
accordingly - which is REVIEW, never APPROVE - or no decision is recorded at all
and the transaction stays undecided until a human or a retry deals with it.

Each test below states the expected status, the expected decision, and what the
audit trail should contain, because those three together are what an operator
needs during an incident.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.models import RiskDecision, Transaction
from app.services import events as event_service
from ml.anomaly.predictor import AnomalyModelNotAvailableError
from ml.inference.predictor import ModelNotAvailableError

INGEST = "/api/events/transactions"

BASE_EVENT = {
    "amount": "142000.00",
    "currency": "INR",
    "customer_id": "SIM_CUS_chaos_1",
    "merchant_id": "mrc_0001",
    "payment_method": "card",
    "country": "SG",
    "city": "Singapore",
    "timestamp": "2026-06-01T11:45:00+00:00",
    "device_id": "SIM_dev_chaos_1",
    "device_type": "web_desktop",
    "ip_address": "198.18.7.7",
    "ip_country": "SG",
    "ip_is_proxy": True,
}


def event(reference: str, **overrides: object) -> dict[str, object]:
    return {"transaction_id": reference, **BASE_EVENT, **overrides}


def _decision_for(session: Session, reference: str) -> RiskDecision | None:
    transaction = session.scalar(select(Transaction).where(Transaction.transaction_id == reference))
    if transaction is None:
        return None
    return session.scalar(select(RiskDecision).where(RiskDecision.transaction_id == transaction.id))


# --------------------------------------------------------------------------
# The fraud model
# --------------------------------------------------------------------------
def test_a_missing_fraud_model_never_produces_an_approval(
    risk_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 200 with an error, no decision, a `processing_failed` event.

    The pipeline reports the failure and stops. It does *not* proceed to Phase 6
    with a missing supervised signal and let the fail-safe produce a REVIEW,
    because a scoring failure is an infrastructure problem, not evidence about
    the payment.
    """
    from app.services import risk as risk_service

    def explode(*args: object, **kwargs: object) -> None:
        raise ModelNotAvailableError("model artifact missing from /srv/models/xgboost.joblib")

    monkeypatch.setattr(risk_service, "predict_and_store", explode)

    reference = "SIM_chaos_no_fraud_model"
    response = risk_client.post(INGEST, json=event(reference))

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["decision"] is None
    assert body["failed_stage"] == "risk_scoring"

    # The class name identifies what broke; the message, which names the
    # artifact path, stays in the log.
    assert body["error"] == "ModelNotAvailableError"
    assert "/srv/models" not in response.text
    assert "joblib" not in response.text
    assert _decision_for(db_session, reference) is None


def test_the_risk_endpoint_degrades_to_503_not_500(
    risk_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 503, and a message that does not name a filesystem path."""
    from ml.inference import predictor as predictor_module

    def explode(*args: object, **kwargs: object) -> None:
        raise ModelNotAvailableError("no artifact at C:/models/xgboost.joblib")

    monkeypatch.setattr(predictor_module, "get_predictor", explode)
    monkeypatch.setattr("app.services.risk.get_predictor", explode)

    response = risk_client.post(
        "/api/risk/predict", json={"transaction_id": "TXN_SCENARIO_C_CURRENT_1"}
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "not available" in detail.lower()
    assert "joblib" not in detail.lower()
    assert "c:/models" not in detail.lower()


# --------------------------------------------------------------------------
# The anomaly model
# --------------------------------------------------------------------------
def test_a_missing_anomaly_model_never_produces_an_approval(
    risk_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 200 with an error, no decision, failure labelled by stage."""
    from app.services import anomaly as anomaly_service

    def explode(*args: object, **kwargs: object) -> None:
        raise AnomalyModelNotAvailableError("forest artifact missing")

    monkeypatch.setattr(anomaly_service, "score_and_store", explode)

    reference = "SIM_chaos_no_anomaly_model"
    body = risk_client.post(INGEST, json=event(reference)).json()

    assert body["accepted"] is False
    assert body["decision"] is None
    assert body["failed_stage"] == "anomaly_detection"
    assert _decision_for(db_session, reference) is None


# --------------------------------------------------------------------------
# The investigation agent
# --------------------------------------------------------------------------
def test_an_investigation_failure_stops_the_pipeline_rather_than_approving(
    risk_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 200 with an error, no decision, stage `investigation`.

    Worth stating plainly: an investigation that could not run is *not* the same
    as an investigation that found nothing. Treating it as the latter is exactly
    how a corroboration requirement gets quietly satisfied by an outage.
    """
    from app.services import investigation as investigation_service

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("agent unreachable")

    monkeypatch.setattr(investigation_service, "run_investigation", explode)

    reference = "SIM_chaos_no_agent"
    body = risk_client.post(INGEST, json=event(reference)).json()

    assert body["accepted"] is False
    assert body["failed_stage"] == "investigation"
    assert body["decision"] is None
    assert _decision_for(db_session, reference) is None


def test_a_missing_investigation_is_not_an_approval(
    client: TestClient, db_session: Session, scored: int
) -> None:
    """The Phase 6 fail-safe, restated as a Phase 10 claim.

    When a transaction the policy *wanted* investigated has no investigation,
    the missing-investigation rule fires. Its configured action is REVIEW, and
    a BLOCK is withheld rather than issued on uncorroborated evidence - but the
    one thing it is never allowed to be is APPROVE.
    """
    from policy.loader import get_policy

    policy = get_policy()
    assert str(policy.fail_safe.missing_investigation).upper() != "APPROVE"
    assert str(policy.fail_safe.missing_supervised_signal).upper() != "APPROVE"
    assert str(policy.fail_safe.missing_anomaly_signal).upper() != "APPROVE"


# --------------------------------------------------------------------------
# The policy configuration
# --------------------------------------------------------------------------
def test_an_unloadable_policy_stops_the_pipeline(
    risk_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 200 with an error, no decision, stage `policy_load`.

    There is no default policy to fall back on. A hardcoded fallback would mean
    a broken configuration file silently changes how every payment is decided,
    which is worse than deciding nothing.
    """
    from policy.schema import PolicyValidationError

    def explode(*args: object, **kwargs: object) -> None:
        raise PolicyValidationError("thresholds.fraud_block must be <= 1.0")

    monkeypatch.setattr("app.services.ingest.get_policy", explode)

    reference = "SIM_chaos_bad_policy"
    body = risk_client.post(INGEST, json=event(reference)).json()

    assert body["accepted"] is False
    assert body["failed_stage"] == "policy_load"
    assert _decision_for(db_session, reference) is None


def test_the_policy_endpoint_reports_a_bad_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from policy.schema import PolicyValidationError

    def explode(*args: object, **kwargs: object) -> None:
        raise PolicyValidationError("bad threshold")

    monkeypatch.setattr("app.api.routes.operations.get_policy", explode)
    response = client.get("/api/policy")
    assert response.status_code in (500, 503)
    assert "traceback" not in response.text.lower()


# --------------------------------------------------------------------------
# The database
# --------------------------------------------------------------------------
def test_an_unreachable_database_is_reported_as_degraded_not_dead(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: `/health/db` answers 200 with `degraded`.

    A 500 here would be indistinguishable from the process being broken, which
    is the distinction the endpoint exists to draw.
    """
    from app.services import health as health_service

    def explode(session: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(health_service, "probe_database", explode, raising=False)
    monkeypatch.setattr(
        "app.api.routes.health.probe_database",
        lambda session: health_service.DatabaseProbe(connected=False, detail="refused"),
    )

    response = anonymous_client.get("/health/db")
    assert response.status_code == 200
    assert response.json()["database"] == "unavailable"


def test_liveness_does_not_touch_the_database(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outage amplifier this avoids: database blips, every replica fails
    liveness, the orchestrator kills them all, and now there is no capacity to
    serve the requests that would have worked."""
    monkeypatch.setattr(
        "app.api.routes.health.probe_database",
        lambda session: (_ for _ in ()).throw(AssertionError("liveness touched the database")),
    )
    assert anonymous_client.get("/health/live").status_code == 200
    assert anonymous_client.get("/health").status_code == 200


def test_readiness_returns_503_when_a_dependency_is_down(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 body saying `ready: false` would keep traffic arriving; the load
    balancer acts on the status code."""
    from app.services.health import DependencyStatus, ReadinessProbe

    monkeypatch.setattr(
        "app.api.routes.health.probe_readiness",
        lambda session: ReadinessProbe(
            ready=False,
            dependencies=(
                DependencyStatus("database", True),
                DependencyStatus("fraud_model", False, "ModelNotAvailableError"),
            ),
        ),
    )
    response = anonymous_client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["dependencies"]["fraud_model"] == "unavailable"


def test_readiness_reveals_nothing_useful_to_a_prober(anonymous_client: TestClient) -> None:
    """Unauthenticated, so it carries no version, path or error text."""
    body = anonymous_client.get("/health/ready").text.lower()
    for marker in ("joblib", "traceback", "postgresql://", "c:\\", "/srv", "psycopg"):
        assert marker not in body


# --------------------------------------------------------------------------
# The event broker
# --------------------------------------------------------------------------
def test_a_full_broker_refuses_a_new_stream_rather_than_lying(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected: 503 with Retry-After.

    Accepting a stream the broker cannot feed would leave the console showing a
    LIVE badge over a dead feed - the worst outcome available, because it looks
    like everything is fine.
    """

    async def refuse() -> None:
        raise event_service.TooManySubscribersError("broker is at its limit of 64 streams")

    monkeypatch.setattr(event_service.broker, "subscribe", refuse)
    response = client.get("/api/events/stream")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_the_subscriber_cap_is_enforced() -> None:
    """Each subscriber holds a 64-slot queue, so an uncapped count is an
    unbounded amount of memory an authenticated client could claim."""

    async def scenario() -> None:
        broker = event_service.InMemoryEventBroker(max_subscribers=3)
        for _ in range(3):
            await broker.subscribe()
        with pytest.raises(event_service.TooManySubscribersError):
            await broker.subscribe()

    asyncio.run(scenario())


def test_a_slow_client_is_dropped_and_the_publisher_is_not_blocked() -> None:
    """The pipeline must never wait on a browser.

    The durable copy is already in `risk_events`, so a dropped client loses
    nothing it cannot resume from its last event id.
    """

    async def scenario() -> None:
        broker = event_service.InMemoryEventBroker()
        await broker.subscribe()  # never drained
        for index in range(event_service.CLIENT_QUEUE_SIZE + 5):
            broker.publish({"sequence": index, "event_type": "risk_scored"})
        assert broker.dropped_deliveries == 5

    asyncio.run(scenario())


def test_publishing_to_nobody_is_harmless() -> None:
    broker = event_service.InMemoryEventBroker()
    broker.publish({"sequence": 1, "event_type": "risk_scored"})
    assert broker.subscriber_count == 0


# --------------------------------------------------------------------------
# Bad requests and replay
# --------------------------------------------------------------------------
def test_a_duplicate_submission_creates_no_second_anything(
    risk_client: TestClient, db_session: Session
) -> None:
    """Expected: 200, `duplicate: true`, and exactly one of every record.

    The reason this matters is not tidiness. Decisions are append-only history;
    two decisions for one submitted event would be a permanent, unexplainable
    artefact in the audit trail.
    """
    from app.models import Investigation, RiskPrediction, RiskSignal

    reference = "SIM_chaos_duplicate"
    first = risk_client.post(INGEST, json=event(reference)).json()
    assert first["duplicate"] is False

    second = risk_client.post(INGEST, json=event(reference)).json()
    assert second["duplicate"] is True
    assert second["decision"] == first["decision"]
    assert second["decision_id"] == first["decision_id"]

    transaction = db_session.scalar(
        select(Transaction).where(Transaction.transaction_id == reference)
    )
    assert transaction is not None
    for model in (RiskPrediction, RiskDecision, Investigation):
        count = db_session.scalar(
            select(func.count()).select_from(model).where(model.transaction_id == transaction.id)
        )
        assert count <= 1, f"{model.__name__} was created twice"

    signals = db_session.scalar(
        select(func.count())
        .select_from(RiskSignal)
        .where(RiskSignal.transaction_id == transaction.id)
    )
    # Two signals per transaction: the anomaly score and the customer deviation.
    assert signals <= 2


def test_a_lost_insert_race_returns_the_winners_decision(
    risk_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the unique constraint exists for, made deterministic.

    Two submitters send the same reference at the same moment. Both see no
    existing row, both build a transaction, one commits first and the other's
    INSERT hits the unique index. The loser must not raise, and must not create
    a second decision - it returns the winner's result, so both callers get the
    same answer.

    Driving this with real threads would test SQLite's locking and the test
    client's threading rather than the recovery path, so the collision is
    injected instead: the row is created behind the caller's back, exactly as a
    concurrent commit would.
    """
    from sqlalchemy.exc import IntegrityError

    from app.services import ingest as ingest_service

    reference = "SIM_chaos_race"
    # The "winner": a complete run that commits first.
    winner = risk_client.post(INGEST, json=event(reference)).json()
    assert winner["duplicate"] is False
    assert winner["decision_id"]

    # The "loser": force the pre-check to miss, so the code takes the insert
    # path and collides on the unique index just as a real racer would.
    original_existing = ingest_service._existing
    calls = {"n": 0}

    def blind_first_look(session: Session, ref: str) -> Transaction | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original_existing(session, ref)

    monkeypatch.setattr(ingest_service, "_existing", blind_first_look)

    loser = risk_client.post(INGEST, json=event(reference)).json()
    assert loser["duplicate"] is True
    assert loser["decision_id"] == winner["decision_id"]

    transaction = db_session.scalar(
        select(Transaction).where(Transaction.transaction_id == reference)
    )
    assert transaction is not None
    count = db_session.scalar(
        select(func.count())
        .select_from(RiskDecision)
        .where(RiskDecision.transaction_id == transaction.id)
    )
    assert count == 1
    assert IntegrityError is not None  # documents what the code caught


def test_a_malformed_payload_is_refused_without_touching_the_pipeline(
    risk_client: TestClient, db_session: Session
) -> None:
    """Expected: 422, no transaction, no decision, no event."""
    before = db_session.scalar(select(func.count()).select_from(Transaction))

    response = risk_client.post(INGEST, json={"transaction_id": "SIM_chaos_malformed"})
    assert response.status_code == 422

    after = db_session.scalar(select(func.count()).select_from(Transaction))
    assert after == before


def test_an_unknown_merchant_is_a_client_error_not_a_pipeline_failure(
    risk_client: TestClient,
) -> None:
    """422, not a `processing_failed` event.

    The distinction matters operationally: a pipeline-failure metric that counts
    other people's typos is a metric nobody will page on.
    """
    response = risk_client.post(
        INGEST, json=event("SIM_chaos_bad_merchant", merchant_id="mrc_nonexistent")
    )
    assert response.status_code == 422
    assert "merchant" in response.text.lower()


def test_an_expired_token_is_refused(anonymous_client: TestClient, auth_users: dict) -> None:
    """Expected: 401, generic body, no partial work."""
    expired = create_access_token(
        user_id=auth_users["admin"].id,  # type: ignore[attr-defined]
        email="admin@test.invalid",
        role="admin",
        now=datetime.now(UTC) - timedelta(hours=3),
        ttl=timedelta(minutes=5),
    ).token

    response = anonymous_client.post(
        INGEST, json=event("SIM_chaos_expired"), headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_exceeding_the_rate_limit_writes_nothing(
    rate_limited_client: TestClient, db_session: Session
) -> None:
    """Expected: 429 before the handler runs, so no work is started.

    Checked as a dependency rather than inside the endpoint precisely so that a
    refused request costs a counter increment and nothing else.
    """
    before = db_session.scalar(select(func.count()).select_from(Transaction))
    for _ in range(31):
        rate_limited_client.post("/api/simulator/stop")

    refused = rate_limited_client.post("/api/simulator/stop")
    assert refused.status_code == 429

    after = db_session.scalar(select(func.count()).select_from(Transaction))
    assert after == before


def test_an_unhandled_error_returns_a_flat_message(
    app, db_session: Session, auth_users: dict, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """No stack trace, no SQL, no module path on the wire.

    The traceback goes to the log, where it is useful to an operator and not
    readable by whoever triggered it. A dedicated client is built here with
    ``raise_server_exceptions=False`` so the test sees the handler's response
    rather than the exception re-raised into the test process.
    """
    from app.db.session import get_db
    from tests.conftest import authorize

    monkeypatch.setattr(
        "app.services.analytics.overview",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("psycopg.errors.UndefinedTable: relation transactions does not exist")
        ),
    )

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            authorize(client, auth_users["admin"])
            response = client.get("/api/analytics/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "psycopg" not in response.text
    assert "relation" not in response.text
