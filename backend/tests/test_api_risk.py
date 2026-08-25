"""Tests for ``POST /api/risk/predict``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import RiskPrediction as RiskPredictionRow
from app.models import Transaction
from ml.inference.predictor import reset_predictor, to_risk_score
from tests.conftest import authorize

ENDPOINT = "/api/risk/predict"

# Anything that would leak infrastructure detail through the API.
FORBIDDEN_IN_RESPONSE = (
    "joblib",
    "/srv",
    "c:\\",
    "postgresql",
    "password",
    "traceback",
    "site-packages",
)


def _predict(client: TestClient, reference: str) -> dict:
    response = client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 200, response.text
    return response.json()


def test_prediction_response_has_the_documented_shape(risk_client: TestClient) -> None:
    body = _predict(risk_client, "TXN_SCENARIO_B_CURRENT")

    assert set(body) == {
        "transaction_id",
        "fraud_probability",
        "risk_score",
        "model_version",
        "threshold",
        "exceeds_threshold",
        "created_at",
    }
    assert body["transaction_id"] == "TXN_SCENARIO_B_CURRENT"
    assert body["model_version"] == "xgboost-test"


def test_probability_and_score_are_consistent(risk_client: TestClient) -> None:
    body = _predict(risk_client, "TXN_SCENARIO_B_CURRENT")

    assert 0.0 <= body["fraud_probability"] <= 1.0
    assert 0 <= body["risk_score"] <= 100
    assert body["risk_score"] == to_risk_score(body["fraud_probability"])
    assert body["exceeds_threshold"] == (body["fraud_probability"] >= body["threshold"])


def test_prediction_is_stable_across_calls(risk_client: TestClient) -> None:
    first = _predict(risk_client, "TXN_SCENARIO_A_CURRENT")
    second = _predict(risk_client, "TXN_SCENARIO_A_CURRENT")
    assert first["fraud_probability"] == second["fraud_probability"]


@pytest.mark.parametrize(
    "reference",
    ["TXN_SCENARIO_A_CURRENT", "TXN_SCENARIO_B_CURRENT", "TXN_SCENARIO_C_CURRENT_1"],
)
def test_every_demo_scenario_can_be_scored(risk_client: TestClient, reference: str) -> None:
    body = _predict(risk_client, reference)
    assert 0.0 <= body["fraud_probability"] <= 1.0


def test_the_suspicious_scenario_outranks_the_normal_one(risk_client: TestClient) -> None:
    normal = _predict(risk_client, "TXN_SCENARIO_A_CURRENT")
    suspicious = _predict(risk_client, "TXN_SCENARIO_B_CURRENT")
    assert suspicious["fraud_probability"] > normal["fraud_probability"]


# --- persistence ------------------------------------------------------------


def test_prediction_is_written_to_risk_predictions(
    risk_client: TestClient, db_session: Session
) -> None:
    body = _predict(risk_client, "TXN_SCENARIO_B_CURRENT")

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_B_CURRENT")
    ).one()
    row = db_session.scalars(
        select(RiskPredictionRow).where(RiskPredictionRow.transaction_id == transaction.id)
    ).one()

    assert row.risk_score == body["risk_score"]
    assert row.model_version == body["model_version"]


def test_repeated_predictions_do_not_duplicate_rows(
    risk_client: TestClient, db_session: Session
) -> None:
    for _ in range(3):
        _predict(risk_client, "TXN_SCENARIO_C_CURRENT_2")

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_C_CURRENT_2")
    ).one()
    count = db_session.scalar(
        select(func.count(RiskPredictionRow.id)).where(
            RiskPredictionRow.transaction_id == transaction.id
        )
    )
    assert count == 1


# --- input validation and errors -------------------------------------------


def test_unknown_transaction_returns_404(risk_client: TestClient) -> None:
    response = risk_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH_TRANSACTION"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.parametrize(
    "reference",
    ["", "a" * 65, "'; DROP TABLE transactions; --", "../../etc/passwd", "<script>x</script>"],
)
def test_malformed_references_are_rejected(risk_client: TestClient, reference: str) -> None:
    """Hostile input is refused by validation before it reaches a query."""
    response = risk_client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 422


def test_missing_body_is_rejected(risk_client: TestClient) -> None:
    assert risk_client.post(ENDPOINT, json={}).status_code == 422


def test_error_responses_leak_no_infrastructure_detail(risk_client: TestClient) -> None:
    for response in (
        risk_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH_TRANSACTION"}),
        risk_client.post(ENDPOINT, json={"transaction_id": "!!!"}),
    ):
        body = response.text.lower()
        for marker in FORBIDDEN_IN_RESPONSE:
            assert marker not in body


def test_success_response_leaks_no_infrastructure_detail(risk_client: TestClient) -> None:
    response = risk_client.post(ENDPOINT, json={"transaction_id": "TXN_SCENARIO_A_CURRENT"})
    body = response.text.lower()
    for marker in FORBIDDEN_IN_RESPONSE:
        assert marker not in body


def test_a_missing_model_returns_503_without_details(
    app: FastAPI, db_session: Session, tmp_path: Path, auth_users: dict[str, object]
) -> None:
    """A missing artifact must degrade cleanly, not 500 with a filesystem path."""
    reset_predictor()
    app.dependency_overrides[get_db] = lambda: db_session

    from ml.config import MODEL_PATH
    from ml.inference import predictor as predictor_module

    original = predictor_module.MODEL_PATH
    predictor_module.MODEL_PATH = tmp_path / "absent.joblib"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            authorize(client, auth_users["admin"])
            response = client.post(ENDPOINT, json={"transaction_id": "TXN_SCENARIO_A_CURRENT"})
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "risk model is not available" in detail.lower()
        assert str(tmp_path) not in detail
        assert "joblib" not in detail.lower()
    finally:
        predictor_module.MODEL_PATH = original or MODEL_PATH
        app.dependency_overrides.clear()
        reset_predictor()
