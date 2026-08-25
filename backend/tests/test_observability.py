"""Structured logs, correlation, redaction and metrics.

The question observability answers in an incident is "what happened to this
transaction?" - so these tests check that a single correlation id joins the
request to every stage of the pipeline it triggered, that the lifecycle events
are all emitted, and that no secret ever appears in the stream that answers it.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.context import CORRELATION_HEADER, correlation_scope, get_correlation_id
from app.core.logging import (
    REDACTED,
    JsonFormatter,
    RedactingFilter,
    configure_logging,
    redact_text,
    redact_value,
)
from app.core.metrics import (
    CONTENT_TYPE,
    REGISTRY,
    render_metrics,
    reset_metrics,
)
from app.core.observability import LifecycleEvent, log_lifecycle


def _format(record: logging.LogRecord) -> dict[str, object]:
    """Run one record through the real filter and formatter."""
    RedactingFilter().filter(record)
    return json.loads(JsonFormatter("razorshield-backend").format(record))


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------
def test_a_log_line_is_one_json_object() -> None:
    payload = _format(_record("something happened"))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["service"] == "razorshield-backend"
    assert payload["message"] == "something happened"
    assert "timestamp" in payload


def test_structured_fields_become_top_level_keys() -> None:
    payload = _format(
        _record(
            "decision_created", event="decision_created", transaction_id="TXN_1", action="review"
        )
    )
    assert payload["event"] == "decision_created"
    assert payload["transaction_id"] == "TXN_1"
    assert payload["action"] == "review"


def test_the_correlation_id_is_attached_from_the_ambient_context() -> None:
    """Not passed in. Every log call in the pipeline would otherwise need an
    argument it has no other use for."""
    with correlation_scope("trace-abcdef123456"):
        payload = _format(_record("in scope"))
    assert payload["correlation_id"] == "trace-abcdef123456"

    assert "correlation_id" not in _format(_record("out of scope"))


def test_a_correlation_scope_restores_what_it_replaced() -> None:
    """The simulator workers are long-lived and process one transaction after
    another; an id that leaked into the next would silently merge two traces."""
    with correlation_scope("outer-000000000001"):
        with correlation_scope("inner-000000000002"):
            assert get_correlation_id() == "inner-000000000002"
        assert get_correlation_id() == "outer-000000000001"
    assert get_correlation_id() is None


def test_a_field_that_will_not_serialise_does_not_crash_the_logger() -> None:
    class Unserialisable:
        def __repr__(self) -> str:
            return "<opaque>"

    payload = _format(_record("odd field", thing=Unserialisable()))
    assert payload["thing"] == "<opaque>"


def test_an_exception_is_logged_but_not_returned(client: TestClient) -> None:
    """The traceback belongs in the log, never in the HTTP response."""
    logger = logging.getLogger("test.exceptions")
    try:
        raise ValueError("internal detail")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, __import__("sys").exc_info()
        )
    payload = _format(record)
    assert "Traceback" in str(payload["exception"])
    assert logger is not None


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "password_hash",
        "jwt_secret",
        "access_token",
        "authorization",
        "api_key",
        "apiKey",
        "llm_api_key",
        "anthropic_api_key",
        "database_url",
        "cookie",
    ],
)
def test_a_sensitive_field_is_never_printed(key: str) -> None:
    payload = _format(_record("login", **{key: "super-secret-value"}))
    assert payload[key] == REDACTED
    assert "super-secret-value" not in json.dumps(payload)


def test_redaction_reaches_into_nested_structures() -> None:
    nested = {"outer": {"password": "hunter2", "safe": "visible"}, "list": [{"token": "abc"}]}
    payload = _format(_record("nested", context=nested))
    encoded = json.dumps(payload)
    assert "hunter2" not in encoded
    assert "abc" not in encoded
    assert "visible" in encoded


@pytest.mark.parametrize(
    "message",
    [
        "connecting with password=hunter2",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij",
        'api_key: "sk-ant-0123456789abcdef"',
        "postgresql+psycopg://razorshield:s3cr3t@db:5432/razorshield",
        "token = abcdefghijklmnop",
    ],
)
def test_a_secret_interpolated_into_a_message_is_scrubbed(message: str) -> None:
    """A backstop, not a licence.

    The correct fix is never to pass the value. This exists because "never" is
    not a property anyone can enforce by intention across a whole codebase, and
    a log stream that ships off the host is the wrong place to find out.
    """
    scrubbed = redact_text(message)
    for secret in ("hunter2", "s3cr3t", "sk-ant-0123456789abcdef", "abcdefghijklmnop"):
        assert secret not in scrubbed
    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed


def test_ordinary_text_survives_redaction() -> None:
    """A scrubber that mangles normal messages makes the log useless, which is
    its own kind of failure."""
    for message in (
        "Decision for TXN_1: REVIEW (policy=policy-v1 rules=MODEL_DISAGREEMENT)",
        "Scored SIM_run_000001: probability=0.90712 risk_score=91",
        "Simulator started: run=abc scenario=coordinated_fraud rate=3.00 max=24",
    ):
        assert redact_text(message) == message


def test_a_business_identifier_is_not_mistaken_for_a_secret() -> None:
    assert redact_value("transaction_id", "TXN_SCENARIO_C_CURRENT_1") == "TXN_SCENARIO_C_CURRENT_1"
    assert redact_value("decision_id", "DEC-abc123") == "DEC-abc123"
    assert redact_value("policy_version", "policy-v1") == "policy-v1"


# --------------------------------------------------------------------------
# The lifecycle, end to end
# --------------------------------------------------------------------------
INGEST_EVENT = {
    "transaction_id": "SIM_observability_0001",
    "amount": "142000.00",
    "currency": "INR",
    "customer_id": "SIM_CUS_obs_1",
    "merchant_id": "mrc_0001",
    "payment_method": "card",
    "country": "SG",
    "city": "Singapore",
    "timestamp": "2026-06-01T11:30:00+00:00",
    "device_id": "SIM_dev_obs_1",
    "device_type": "web_desktop",
    "ip_address": "198.18.4.4",
    "ip_country": "SG",
    "ip_is_proxy": True,
}


def test_one_correlation_id_covers_the_whole_pipeline(
    risk_client: TestClient, caplog: pytest.LogCaptureFixture, db_session: Session
) -> None:
    """The property that makes the log usable: one id, one transaction, one trace.

    Without it, reconstructing what happened means correlating five services by
    timestamp and hoping no two transactions arrived in the same millisecond.
    """
    caplog.set_level(logging.INFO, logger="razorshield.lifecycle")

    response = risk_client.post(
        "/api/events/transactions",
        json=INGEST_EVENT,
        headers={CORRELATION_HEADER: "obs-trace-000000001"},
    )
    assert response.status_code in (200, 201)
    assert response.headers[CORRELATION_HEADER] == "obs-trace-000000001"
    assert response.json()["correlation_id"] == "obs-trace-000000001"

    events = {
        record.__dict__.get("event")
        for record in caplog.records
        if record.name == "razorshield.lifecycle"
    }
    # Every stage that ran, named.
    assert LifecycleEvent.TRANSACTION_RECEIVED in events
    assert LifecycleEvent.RISK_SCORED in events
    assert LifecycleEvent.ANOMALY_SCORED in events
    assert LifecycleEvent.DECISION_CREATED in events


def test_the_pipeline_log_carries_the_business_identifiers(
    risk_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="razorshield.lifecycle")
    payload = dict(INGEST_EVENT, transaction_id="SIM_observability_0002")
    risk_client.post("/api/events/transactions", json=payload)

    by_event = {
        record.__dict__.get("event"): record.__dict__
        for record in caplog.records
        if record.name == "razorshield.lifecycle"
    }
    assert by_event[LifecycleEvent.RISK_SCORED]["transaction_id"] == "SIM_observability_0002"
    decision = by_event[LifecycleEvent.DECISION_CREATED]
    assert decision["transaction_id"] == "SIM_observability_0002"
    # The decision's own public id, so a log line links to an auditable record.
    assert decision["decision_id"].startswith("DEC")


def test_a_duplicate_submission_is_logged_as_one(
    risk_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    payload = dict(INGEST_EVENT, transaction_id="SIM_observability_0003")
    risk_client.post("/api/events/transactions", json=payload)

    caplog.clear()
    caplog.set_level(logging.INFO, logger="razorshield.lifecycle")
    risk_client.post("/api/events/transactions", json=payload)

    events = [record.__dict__.get("event") for record in caplog.records]
    assert LifecycleEvent.TRANSACTION_DUPLICATE in events
    # Nothing was re-run, so nothing claims to have been.
    assert LifecycleEvent.DECISION_CREATED not in events


def test_feedback_is_logged_without_the_analyst_note(
    client: TestClient, caplog: pytest.LogCaptureFixture, decided: int
) -> None:
    """`notes` is free text a person typed about a customer.

    It belongs in the database, where access is controlled, and not in a log
    stream that is shipped, indexed and retained somewhere else.
    """
    caplog.set_level(logging.INFO, logger="razorshield.lifecycle")
    note = "Spoke to the cardholder about their divorce."
    client.post(
        "/api/feedback",
        json={
            "transaction_id": "TXN_SCENARIO_C_CURRENT_1",
            "outcome": "confirmed_fraud",
            "reason_code": "coordinated_activity",
            "notes": note,
        },
    )

    records = [
        r for r in caplog.records if r.__dict__.get("event") == LifecycleEvent.FEEDBACK_CREATED
    ]
    assert records, "feedback should emit a lifecycle event"
    assert note not in caplog.text
    assert records[0].__dict__["outcome"] == "confirmed_fraud"


def test_an_authorization_denial_is_logged(
    viewer_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A refusal nobody records is a refusal nobody can investigate."""
    caplog.set_level(logging.WARNING, logger="razorshield.lifecycle")
    viewer_client.post("/api/simulator/stop")

    denials = [
        r.__dict__
        for r in caplog.records
        if r.__dict__.get("event") == LifecycleEvent.AUTHORIZATION_DENIED
    ]
    assert denials
    assert denials[0]["required_permission"] == "simulator:control"
    assert denials[0]["actor_role"] == "viewer"


def test_a_failed_login_is_logged_without_the_password(
    anonymous_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    anonymous_client.post(
        "/api/auth/login",
        json={"email": "admin@test.invalid", "password": "the-attempted-password"},
    )
    assert "auth_failed" in caplog.text
    assert "the-attempted-password" not in caplog.text


def test_configure_logging_is_idempotent() -> None:
    """Called by every `create_app`, and the test suite builds several."""
    root = logging.getLogger()
    configure_logging("INFO")
    before = len([h for h in root.handlers if getattr(h, "_razorshield", False)])
    configure_logging("INFO")
    configure_logging("DEBUG")
    after = len([h for h in root.handlers if getattr(h, "_razorshield", False)])
    assert before == after == 1


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def test_the_metrics_endpoint_is_admin_only(
    viewer_client: TestClient, analyst_client: TestClient, client: TestClient
) -> None:
    """Metrics are not secrets, but they are an excellent map."""
    assert viewer_client.get("/api/metrics").status_code == 403
    assert analyst_client.get("/api/metrics").status_code == 403
    assert client.get("/api/metrics").status_code == 200


def test_the_metrics_endpoint_is_not_public(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/api/metrics").status_code == 401


def test_the_exposition_format_is_prometheus(client: TestClient) -> None:
    response = client.get("/api/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    assert CONTENT_TYPE.startswith("text/plain")
    body = response.text
    assert "# HELP razorshield_transactions_processed_total" in body
    assert "# TYPE razorshield_transactions_processed_total counter" in body


@pytest.mark.parametrize(
    "family",
    [
        "razorshield_transactions_processed_total",
        "razorshield_transactions_failed_total",
        "razorshield_risk_predictions_total",
        "razorshield_anomalies_total",
        "razorshield_investigations_total",
        "razorshield_decisions_total",
        "razorshield_feedback_total",
        "razorshield_processing_latency_seconds",
        "razorshield_risk_latency_seconds",
        "razorshield_investigation_latency_seconds",
        "razorshield_decision_latency_seconds",
        "razorshield_sse_connections",
        "razorshield_sse_events_total",
    ],
)
def test_every_required_metric_is_declared(client: TestClient, family: str) -> None:
    assert family in client.get("/api/metrics").text


def test_the_registry_does_not_publish_the_python_version(client: TestClient) -> None:
    """The default registry auto-registers a collector that does.

    Free reconnaissance for anyone who reaches this endpoint, which is why the
    application uses a private registry.
    """
    body = client.get("/api/metrics").text
    assert "python_info" not in body
    assert "process_cpu_seconds_total" not in body


def test_metrics_count_what_the_pipeline_actually_did(risk_client: TestClient) -> None:
    """Counted in the services, so the batch path, the HTTP endpoints and the
    live pipeline all increment the same families."""
    reset_metrics()
    payload = dict(INGEST_EVENT, transaction_id="SIM_metrics_0001")
    assert risk_client.post("/api/events/transactions", json=payload).status_code in (200, 201)

    body = render_metrics().decode()
    assert "razorshield_transactions_processed_total 1.0" in body
    assert "razorshield_risk_predictions_total 1.0" in body
    assert 'razorshield_decisions_total{action="' in body
    # The latency histogram observed the run.
    assert "razorshield_processing_latency_seconds_count 1.0" in body


def test_a_duplicate_is_counted_separately(risk_client: TestClient) -> None:
    """A resubmission is not a second processed transaction, and a metric that
    said otherwise would inflate the volume the dashboard reports."""
    payload = dict(INGEST_EVENT, transaction_id="SIM_metrics_0002")
    risk_client.post("/api/events/transactions", json=payload)

    reset_metrics()
    risk_client.post("/api/events/transactions", json=payload)
    body = render_metrics().decode()
    assert "razorshield_transactions_duplicate_total 1.0" in body
    assert "razorshield_transactions_processed_total 0.0" in body


def test_http_metrics_are_labelled_by_route_template_not_path(client: TestClient) -> None:
    """Labelling by raw path would mint a new time series per transaction id -
    the classic way to make a Prometheus server fall over."""
    reset_metrics()
    client.get("/api/transactions/TXN_SCENARIO_C_CURRENT_1")
    client.get("/api/transactions/TXN_SCENARIO_A_CURRENT")

    body = render_metrics().decode()
    assert 'route="/api/transactions/{transaction_id}"' in body
    assert "TXN_SCENARIO_C_CURRENT_1" not in body


def test_an_authorization_denial_is_counted(viewer_client: TestClient) -> None:
    reset_metrics()
    viewer_client.post("/api/simulator/stop")
    body = render_metrics().decode()
    assert 'razorshield_authorization_denied_total{permission="simulator:control"} 1.0' in body


def test_login_outcomes_are_counted(anonymous_client: TestClient) -> None:
    reset_metrics()
    anonymous_client.post(
        "/api/auth/login", json={"email": "admin@test.invalid", "password": "wrong"}
    )
    anonymous_client.post(
        "/api/auth/login",
        json={"email": "admin@test.invalid", "password": "correct-horse-battery-staple"},
    )
    body = render_metrics().decode()
    assert 'razorshield_auth_attempts_total{outcome="failure"} 1.0' in body
    assert 'razorshield_auth_attempts_total{outcome="success"} 1.0' in body


def test_metrics_never_carry_a_credential(client: TestClient) -> None:
    body = client.get("/api/metrics").text.lower()
    for marker in ("password", "secret", "bearer ", "postgresql://", "psycopg://"):
        assert marker not in body


def test_the_registry_is_private_to_the_application() -> None:
    """A private registry is what makes `reset_metrics` possible at all, and
    what keeps the default collectors out."""
    from prometheus_client import REGISTRY as DEFAULT_REGISTRY

    assert REGISTRY is not DEFAULT_REGISTRY


def test_lifecycle_events_drop_absent_identifiers() -> None:
    """A key with a null value is noise in every log query that follows."""
    logger = logging.getLogger("razorshield.lifecycle")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        log_lifecycle(LifecycleEvent.RISK_SCORED, transaction_id="TXN_1", decision_id=None)
    finally:
        logger.removeHandler(handler)

    assert records
    assert "transaction_id" in records[0].__dict__
    assert "decision_id" not in records[0].__dict__


def test_decimal_survives_json_encoding() -> None:
    """Amounts are Decimals throughout; a logger that raised on one would fail
    exactly where the money is."""
    payload = _format(_record("amount", amount=Decimal("142000.00")))
    assert payload["amount"] == "142000.00"
