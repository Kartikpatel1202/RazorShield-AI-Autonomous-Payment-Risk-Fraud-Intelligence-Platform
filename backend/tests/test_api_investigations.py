"""Investigation API and persistence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Investigation, RiskPrediction, RiskSignal, Transaction
from app.models.enums import ActorType

ENDPOINT = "/api/investigations"

NORMAL = "TXN_SCENARIO_A_CURRENT"
SUSPICIOUS = "TXN_SCENARIO_B_CURRENT"
RING = "TXN_SCENARIO_C_CURRENT_1"

FORBIDDEN_IN_RESPONSE = (
    "api_key",
    "password",
    "postgresql://",
    "c:\\",
    "/srv/",
    "you are the razorshield",
    "untrusted_data",
    "traceback",
)


def _investigate(client: TestClient, reference: str) -> dict:
    response = client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 200, response.text
    return response.json()


# --- shape ------------------------------------------------------------------


def test_response_has_the_documented_shape(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)

    assert set(body) == {
        "investigation_id",
        "transaction_id",
        "status",
        "risk_level",
        "confidence",
        "confidence_basis",
        "summary",
        "findings",
        "evidence",
        "recommended_action",
        "tools_used",
        "tool_calls",
        "model_versions",
        "llm",
        "iteration_count",
        "agent_is_mock",
        "started_at",
        "completed_at",
    }


def test_the_response_identifies_the_transaction_and_investigation(
    agent_client: TestClient,
) -> None:
    body = _investigate(agent_client, RING)
    assert body["transaction_id"] == RING
    assert body["investigation_id"].startswith("INV-")


def test_the_response_reports_the_recommendation_not_a_decision(
    agent_client: TestClient,
) -> None:
    """The field is advice. Naming it 'decision' would misrepresent the system."""
    body = _investigate(agent_client, RING)
    assert "decision" not in body
    assert body["recommended_action"] in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}


def test_a_mock_backed_investigation_says_so(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    assert body["agent_is_mock"] is True
    assert body["llm"]["is_mock"] is True


def test_the_response_carries_both_model_versions(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    assert "fraud_model" in body["model_versions"]
    assert "anomaly_model" in body["model_versions"]


def test_confidence_is_reported_with_its_basis(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["confidence_basis"]["independent_sources"] >= 1


def test_findings_cite_returned_evidence(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    known = {item["evidence_id"] for item in body["evidence"]}

    assert body["findings"]
    for finding in body["findings"]:
        assert finding["evidence_ids"]
        assert set(finding["evidence_ids"]) <= known


def test_the_tool_trace_is_returned(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    assert body["tools_used"]
    assert body["tool_calls"]
    for call in body["tool_calls"]:
        assert call["tool"] in body["tools_used"]
        assert call["latency_ms"] >= 0


# --- scenarios --------------------------------------------------------------


@pytest.mark.parametrize("reference", [NORMAL, SUSPICIOUS, RING])
def test_every_demo_scenario_can_be_investigated(agent_client: TestClient, reference: str) -> None:
    body = _investigate(agent_client, reference)
    assert body["status"] in {"completed", "insufficient_evidence"}
    assert body["evidence"]


def test_the_normal_scenario_is_not_escalated(agent_client: TestClient) -> None:
    body = _investigate(agent_client, NORMAL)
    assert body["risk_level"] in {"LOW", "MEDIUM"}


def test_the_coordinated_scenario_is_escalated(agent_client: TestClient) -> None:
    body = _investigate(agent_client, RING)
    assert body["risk_level"] in {"HIGH", "CRITICAL"}
    assert body["recommended_action"] in {"REVIEW", "BLOCK"}


# --- persistence ------------------------------------------------------------


def test_the_investigation_is_persisted(agent_client: TestClient, db_session: Session) -> None:
    body = _investigate(agent_client, RING)

    row = db_session.scalars(
        select(Investigation).where(Investigation.public_id == body["investigation_id"])
    ).one()

    assert row.status == body["status"]
    assert float(row.confidence) == pytest.approx(body["confidence"], abs=1e-4)
    assert row.iteration_count == body["iteration_count"]
    assert row.agent_is_mock is True
    assert row.report is not None
    assert len(row.report["evidence"]) == len(body["evidence"])


def test_reinvestigating_replaces_rather_than_accumulates(
    agent_client: TestClient, db_session: Session
) -> None:
    """The table holds the latest investigation per transaction."""
    for _ in range(3):
        _investigate(agent_client, RING)

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == RING)
    ).one()
    count = db_session.scalar(
        select(func.count(Investigation.id)).where(Investigation.transaction_id == transaction.id)
    )
    assert count == 1


def test_an_audit_entry_is_written(agent_client: TestClient, db_session: Session) -> None:
    body = _investigate(agent_client, RING)

    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "investigation.completed")
    ).first()

    assert entry is not None
    assert entry.actor_type is ActorType.AGENT
    assert entry.event_data is not None
    assert entry.event_data["investigation_id"] == body["investigation_id"]
    assert entry.event_data["llm_is_mock"] is True


def test_the_audit_entry_carries_no_evidence_text(
    agent_client: TestClient, db_session: Session
) -> None:
    _investigate(agent_client, RING)
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.event_type == "investigation.completed")
    ).one()

    rendered = repr(entry.event_data).lower()
    for marker in ("you are", "untrusted", "api_key", "claim"):
        assert marker not in rendered


def test_investigating_does_not_disturb_the_model_signals(
    agent_client: TestClient, db_session: Session
) -> None:
    """The agent reads the two signals; it must not revise them."""
    predictions_before = db_session.scalar(select(func.count(RiskPrediction.id)))
    signals_before = db_session.scalar(select(func.count(RiskSignal.id)))

    _investigate(agent_client, RING)

    assert db_session.scalar(select(func.count(RiskPrediction.id))) == predictions_before
    assert db_session.scalar(select(func.count(RiskSignal.id))) == signals_before


# --- read endpoints ---------------------------------------------------------


def test_an_investigation_can_be_fetched_by_id(agent_client: TestClient) -> None:
    created = _investigate(agent_client, SUSPICIOUS)

    response = agent_client.get(f"{ENDPOINT}/{created['investigation_id']}")
    assert response.status_code == 200

    fetched = response.json()
    assert fetched["investigation_id"] == created["investigation_id"]
    assert fetched["transaction_id"] == SUSPICIOUS
    assert len(fetched["findings"]) == len(created["findings"])
    assert len(fetched["evidence"]) == len(created["evidence"])


def test_the_latest_investigation_for_a_transaction_is_returned(
    agent_client: TestClient,
) -> None:
    created = _investigate(agent_client, RING)

    response = agent_client.get(f"/api/transactions/{RING}/investigation")
    assert response.status_code == 200
    assert response.json()["investigation_id"] == created["investigation_id"]


def test_a_transaction_without_an_investigation_returns_404(
    agent_client: TestClient,
) -> None:
    response = agent_client.get("/api/transactions/txn_00000001/investigation")
    assert response.status_code == 404


# --- validation and errors --------------------------------------------------


def test_unknown_transaction_returns_404(agent_client: TestClient) -> None:
    response = agent_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH_TRANSACTION"})
    assert response.status_code == 404


def test_unknown_investigation_returns_404(agent_client: TestClient) -> None:
    assert agent_client.get(f"{ENDPOINT}/INV-DOESNOTEXIST").status_code == 404


@pytest.mark.parametrize(
    "reference",
    ["", "a" * 65, "'; DROP TABLE investigations; --", "../../etc/passwd", "<script>x</script>"],
)
def test_malformed_references_are_rejected(agent_client: TestClient, reference: str) -> None:
    response = agent_client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 422


def test_missing_body_is_rejected(agent_client: TestClient) -> None:
    assert agent_client.post(ENDPOINT, json={}).status_code == 422


def test_responses_leak_no_prompts_or_credentials(agent_client: TestClient) -> None:
    for response in (
        agent_client.post(ENDPOINT, json={"transaction_id": RING}),
        agent_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH"}),
        agent_client.get(f"{ENDPOINT}/INV-NOPE"),
    ):
        body = response.text.lower()
        for marker in FORBIDDEN_IN_RESPONSE:
            assert marker not in body
