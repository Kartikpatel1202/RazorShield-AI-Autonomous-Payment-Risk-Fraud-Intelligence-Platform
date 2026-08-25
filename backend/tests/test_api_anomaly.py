"""Tests for ``POST /api/risk/anomaly``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import RiskPrediction as RiskPredictionRow
from app.models import RiskSignal, Transaction
from app.services.anomaly import ANOMALY_SIGNAL, CUSTOMER_DEVIATION_SIGNAL, OWNED_SIGNALS
from ml.anomaly.predictor import reset_anomaly_predictor
from tests.conftest import authorize

ENDPOINT = "/api/risk/anomaly"

FORBIDDEN_IN_RESPONSE = (
    "joblib",
    "/srv",
    "c:\\",
    "postgresql",
    "password",
    "traceback",
    "site-packages",
)


def _detect(client: TestClient, reference: str) -> dict:
    response = client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 200, response.text
    return response.json()


def test_response_has_the_documented_shape(anomaly_client: TestClient) -> None:
    body = _detect(anomaly_client, "TXN_SCENARIO_C_CURRENT_1")

    assert set(body) == {
        "transaction_id",
        "anomaly_score",
        "severity",
        "model_version",
        "threshold",
        "exceeds_threshold",
        "customer_deviation_score",
        "customer_deviation_driver",
        "top_deviations",
    }
    assert body["transaction_id"] == "TXN_SCENARIO_C_CURRENT_1"
    assert body["model_version"] == "isolation-forest-v1"


def test_score_and_severity_are_consistent(anomaly_client: TestClient) -> None:
    body = _detect(anomaly_client, "TXN_SCENARIO_B_CURRENT")

    assert 0 <= body["anomaly_score"] <= 100
    assert body["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert body["exceeds_threshold"] == (body["anomaly_score"] >= body["threshold"])
    assert 0 <= body["customer_deviation_score"] <= 100


def test_deviations_are_reported_with_percentiles(anomaly_client: TestClient) -> None:
    body = _detect(anomaly_client, "TXN_SCENARIO_C_CURRENT_1")

    assert body["top_deviations"]
    for deviation in body["top_deviations"]:
        assert set(deviation) == {"feature", "value", "percentile"}
        assert 0.0 <= deviation["percentile"] <= 100.0


def test_scoring_is_stable_across_calls(anomaly_client: TestClient) -> None:
    first = _detect(anomaly_client, "TXN_SCENARIO_A_CURRENT")
    second = _detect(anomaly_client, "TXN_SCENARIO_A_CURRENT")
    assert first["anomaly_score"] == second["anomaly_score"]


@pytest.mark.parametrize(
    "reference",
    [
        "TXN_SCENARIO_A_CURRENT",
        "TXN_SCENARIO_B_CURRENT",
        "TXN_SCENARIO_C_CURRENT_1",
        "TXN_SCENARIO_C_CURRENT_2",
        "TXN_SCENARIO_C_CURRENT_3",
    ],
)
def test_every_demo_scenario_can_be_scored(anomaly_client: TestClient, reference: str) -> None:
    body = _detect(anomaly_client, reference)
    assert 0 <= body["anomaly_score"] <= 100


def test_the_coordinated_scenario_outranks_the_normal_one(
    anomaly_client: TestClient,
) -> None:
    normal = _detect(anomaly_client, "TXN_SCENARIO_A_CURRENT")
    ring = _detect(anomaly_client, "TXN_SCENARIO_C_CURRENT_1")
    assert ring["anomaly_score"] > normal["anomaly_score"]


# --- persistence ------------------------------------------------------------


def test_signals_are_persisted(anomaly_client: TestClient, db_session: Session) -> None:
    body = _detect(anomaly_client, "TXN_SCENARIO_B_CURRENT")

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_B_CURRENT")
    ).one()
    rows = list(
        db_session.scalars(
            select(RiskSignal).where(
                RiskSignal.transaction_id == transaction.id,
                RiskSignal.signal_name.in_(OWNED_SIGNALS),
            )
        )
    )

    assert {row.signal_name for row in rows} == set(OWNED_SIGNALS)
    anomaly_row = next(row for row in rows if row.signal_name == ANOMALY_SIGNAL)
    assert int(anomaly_row.signal_value) == body["anomaly_score"]
    assert anomaly_row.source == body["model_version"]

    deviation_row = next(row for row in rows if row.signal_name == CUSTOMER_DEVIATION_SIGNAL)
    assert int(deviation_row.signal_value) == body["customer_deviation_score"]


def test_repeated_calls_do_not_accumulate_signals(
    anomaly_client: TestClient, db_session: Session
) -> None:
    for _ in range(3):
        _detect(anomaly_client, "TXN_SCENARIO_C_CURRENT_2")

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "TXN_SCENARIO_C_CURRENT_2")
    ).one()
    count = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.transaction_id == transaction.id,
            RiskSignal.signal_name.in_(OWNED_SIGNALS),
        )
    )
    assert count == len(OWNED_SIGNALS)


def test_the_anomaly_endpoint_does_not_write_a_fraud_prediction(
    anomaly_client: TestClient, db_session: Session
) -> None:
    """The two engines stay independent: separate tables, no cross-writes."""
    _detect(anomaly_client, "TXN_SCENARIO_B_CURRENT")

    predictions = db_session.scalar(select(func.count(RiskPredictionRow.id)))
    assert predictions == 0


# --- validation and errors --------------------------------------------------


def test_unknown_transaction_returns_404(anomaly_client: TestClient) -> None:
    response = anomaly_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH_TRANSACTION"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.parametrize(
    "reference",
    ["", "a" * 65, "'; DROP TABLE risk_signals; --", "../../etc/passwd", "<script>x</script>"],
)
def test_malformed_references_are_rejected(anomaly_client: TestClient, reference: str) -> None:
    response = anomaly_client.post(ENDPOINT, json={"transaction_id": reference})
    assert response.status_code == 422


def test_missing_body_is_rejected(anomaly_client: TestClient) -> None:
    assert anomaly_client.post(ENDPOINT, json={}).status_code == 422


def test_responses_leak_no_infrastructure_detail(anomaly_client: TestClient) -> None:
    for response in (
        anomaly_client.post(ENDPOINT, json={"transaction_id": "TXN_SCENARIO_A_CURRENT"}),
        anomaly_client.post(ENDPOINT, json={"transaction_id": "NO_SUCH_TRANSACTION"}),
        anomaly_client.post(ENDPOINT, json={"transaction_id": "!!!"}),
    ):
        body = response.text.lower()
        for marker in FORBIDDEN_IN_RESPONSE:
            assert marker not in body


def test_a_missing_model_returns_503_without_details(
    app: FastAPI, db_session: Session, tmp_path: Path, auth_users: dict[str, object]
) -> None:
    reset_anomaly_predictor()
    app.dependency_overrides[get_db] = lambda: db_session

    from ml.anomaly import predictor as predictor_module
    from ml.anomaly.paths import ANOMALY_MODEL_PATH

    original = predictor_module.ANOMALY_MODEL_PATH
    predictor_module.ANOMALY_MODEL_PATH = tmp_path / "absent.joblib"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            authorize(client, auth_users["admin"])
            response = client.post(ENDPOINT, json={"transaction_id": "TXN_SCENARIO_A_CURRENT"})
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "anomaly model is not available" in detail.lower()
        assert str(tmp_path) not in detail
        assert "joblib" not in detail.lower()
    finally:
        predictor_module.ANOMALY_MODEL_PATH = original or ANOMALY_MODEL_PATH
        app.dependency_overrides.clear()
        reset_anomaly_predictor()
