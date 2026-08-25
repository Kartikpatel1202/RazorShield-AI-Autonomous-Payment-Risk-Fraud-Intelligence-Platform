"""The operations surfaces: explorer, transaction detail, audit and policy.

Focus is on the properties that make these endpoints safe to expose: filters
actually filter, pagination actually bounds, sort keys cannot become SQL, and
nothing internal leaks into a response.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, RiskDecision, RiskPrediction, RiskSignal, Transaction
from app.models.enums import DecisionAction, SignalSeverity
from app.services import audit, detail, explorer
from app.services.anomaly import ANOMALY_SIGNAL
from policy.loader import load_policy
from policy.schema import KNOWN_RULE_IDS

NORMAL = "TXN_SCENARIO_A_CURRENT"
RING = "TXN_SCENARIO_C_CURRENT_1"


# --------------------------------------------------------------------------
# Explorer
# --------------------------------------------------------------------------
def test_explorer_returns_one_page_not_the_table(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/transactions/explorer?page=1&page_size=10").json()

    assert len(body["items"]) == 10
    assert body["meta"]["total_items"] == decided
    assert body["meta"]["total_pages"] > 1


def test_explorer_joins_every_risk_column(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    row = client.get("/api/transactions/explorer?page_size=1").json()["items"][0]

    for column in (
        "transaction_id",
        "timestamp",
        "amount",
        "customer_id",
        "merchant_id",
        "fraud_probability",
        "anomaly_score",
        "anomaly_severity",
        "decision",
        "risk_level",
    ):
        assert column in row, column
    assert row["fraud_probability"] is not None
    assert row["decision"] is not None


def test_explorer_shows_transactions_without_risk_data(
    client: TestClient, db_session: Session
) -> None:
    """A transaction with no prediction still appears, with nulls not absence."""
    db_session.commit()
    body = client.get("/api/transactions/explorer?page_size=5").json()

    assert body["meta"]["total_items"] > 0
    assert body["items"][0]["fraud_probability"] is None
    assert body["items"][0]["decision"] is None


@pytest.mark.parametrize("action", ["approve", "step_up", "review", "block"])
def test_explorer_decision_filter_matches_sql(
    client: TestClient, db_session: Session, decided: int, action: str
) -> None:
    db_session.commit()
    body = client.get(f"/api/transactions/explorer?decision={action}&page_size=1").json()

    expected = db_session.scalar(
        select(func.count(RiskDecision.id)).where(RiskDecision.action == DecisionAction(action))
    )
    assert body["meta"]["total_items"] == expected
    for item in body["items"]:
        assert item["decision"] == action.upper()


def test_explorer_severity_filter_matches_sql(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/transactions/explorer?anomaly_severity=CRITICAL&page_size=5").json()

    expected = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.signal_name == ANOMALY_SIGNAL,
            RiskSignal.severity == SignalSeverity.CRITICAL,
        )
    )
    assert body["meta"]["total_items"] == expected
    for item in body["items"]:
        assert item["anomaly_severity"] == "CRITICAL"


def test_explorer_risk_level_filter_uses_policy_bands(
    client: TestClient, db_session: Session, decided: int
) -> None:
    policy = load_policy()
    db_session.commit()
    body = client.get("/api/transactions/explorer?risk_level=CRITICAL&page_size=5").json()

    expected = db_session.scalar(
        select(func.count(RiskPrediction.id)).where(
            RiskPrediction.fraud_probability >= Decimal(str(policy.thresholds.fraud_block))
        )
    )
    assert body["meta"]["total_items"] == expected
    for item in body["items"]:
        assert item["risk_level"] == "CRITICAL"


def test_explorer_probability_range_filter(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get(
        "/api/transactions/explorer?min_probability=0.5&max_probability=0.95&page_size=50"
    ).json()

    for item in body["items"]:
        assert 0.5 <= item["fraud_probability"] <= 0.95


def test_explorer_search_is_a_substring_match(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/transactions/explorer?search=SCENARIO_C").json()

    assert body["meta"]["total_items"] > 0
    for item in body["items"]:
        assert "SCENARIO_C" in item["transaction_id"]


def test_explorer_date_range_filter(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    span = db_session.execute(
        select(
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
        )
    ).one()

    inside = client.get(
        "/api/transactions/explorer", params={"date_from": span[0].isoformat()}
    ).json()
    outside = client.get(
        "/api/transactions/explorer", params={"date_to": span[0].isoformat()}
    ).json()

    assert inside["meta"]["total_items"] > outside["meta"]["total_items"]


@pytest.mark.parametrize("sort_by", ["timestamp", "amount", "fraud_probability", "anomaly_score"])
def test_explorer_sorting_is_applied(
    client: TestClient, db_session: Session, decided: int, sort_by: str
) -> None:
    db_session.commit()
    body = client.get(
        f"/api/transactions/explorer?sort_by={sort_by}&descending=true&page_size=20"
    ).json()

    key = {"timestamp": "timestamp", "amount": "amount"}.get(sort_by, sort_by)
    values = [item[key] for item in body["items"] if item[key] is not None]
    assert values == sorted(values, reverse=True)


def test_explorer_ascending_sort(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get(
        "/api/transactions/explorer?sort_by=amount&descending=false&page_size=20"
    ).json()
    amounts = [item["amount"] for item in body["items"]]

    assert amounts == sorted(amounts)


def test_explorer_paging_does_not_repeat_rows(
    client: TestClient, db_session: Session, decided: int
) -> None:
    """Stable ordering: two consecutive pages must not share a row."""
    db_session.commit()
    first = client.get("/api/transactions/explorer?page=1&page_size=25").json()
    second = client.get("/api/transactions/explorer?page=2&page_size=25").json()

    ids_first = {item["transaction_id"] for item in first["items"]}
    ids_second = {item["transaction_id"] for item in second["items"]}
    assert ids_first.isdisjoint(ids_second)


@pytest.mark.parametrize(
    "query",
    [
        "search='; DROP TABLE transactions;--",
        "sort_by=amount); DELETE FROM transactions",
        "sort_by=__class__",
        "merchant_id=../../etc/passwd",
        "customer_id=' OR 1=1",
        "page_size=100000",
        "page=0",
        "min_probability=5",
        "anomaly_severity=EXTREME",
        "decision=delete_everything",
    ],
)
def test_explorer_rejects_hostile_parameters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/transactions/explorer?{query}").status_code == 422


def test_explorer_response_leaks_nothing_sensitive(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    raw = client.get("/api/transactions/explorer?page_size=20").text.lower()

    for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
        assert secret not in raw


# --------------------------------------------------------------------------
# Transaction detail
# --------------------------------------------------------------------------
def test_detail_carries_every_pipeline_stage(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get(f"/api/transactions/{NORMAL}/detail").json()

    assert body["transaction"]["transaction_id"] == NORMAL
    assert body["signals"]["fraud_probability"] is not None
    assert body["signals"]["fraud_model_version"]
    assert body["signals"]["anomaly_model_version"]
    assert body["decision"]["decision"] in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}
    assert body["decision"]["policy_version"]
    assert len(body["decision"]["input_digest"]) == 64
    assert isinstance(body["audit"], list)


def test_detail_reports_absent_stages_as_null(client: TestClient, db_session: Session) -> None:
    """No investigation and no decision means null, not an invented placeholder."""
    db_session.commit()
    body = client.get(f"/api/transactions/{NORMAL}/detail").json()

    assert body["investigation"] is None
    assert body["decision"] is None
    assert body["signals"]["fraud_probability"] is None


def test_detail_rejects_an_unknown_transaction(client: TestClient) -> None:
    assert client.get("/api/transactions/TXN_NOPE/detail").status_code == 404


def test_detail_counts_the_decision_history(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get(f"/api/transactions/{NORMAL}/detail").json()

    assert body["decision"]["history_count"] >= 1


def test_detail_risk_level_matches_the_policy_band(db_session: Session, decided: int) -> None:
    thresholds = load_policy().thresholds
    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == NORMAL)
    ).one()

    result = detail.build_detail(db_session, transaction)
    probability = result["signals"]["fraud_probability"]

    assert result["signals"]["risk_level"] == explorer.risk_level_for(probability, thresholds)


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
def test_audit_lists_decision_events(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/audit?page_size=10").json()

    assert body["meta"]["total_items"] >= decided
    assert len(body["items"]) == 10
    assert all(entry["event_type"] for entry in body["items"])


def test_audit_surfaces_the_fields_needed_to_explain_a_decision(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/audit?event_type=risk.decision&page_size=1").json()
    entry = body["items"][0]

    assert entry["decision"] in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}
    assert entry["policy_version"]
    assert entry["decision_id"]
    assert entry["event_data"]["matched_rules"]
    assert entry["event_data"]["reason_codes"]


def test_audit_filters_by_event_type(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/audit?event_type=risk.decision&page_size=5").json()

    expected = db_session.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.event_type == "risk.decision")
    )
    assert body["meta"]["total_items"] == expected
    assert all(entry["event_type"] == "risk.decision" for entry in body["items"])


def test_audit_filters_by_transaction(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get(f"/api/audit?transaction_id={NORMAL}").json()

    assert body["meta"]["total_items"] >= 1
    assert all(entry["transaction_id"] == NORMAL for entry in body["items"])


def test_audit_summary_counts_match_sql(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/audit/summary").json()

    for event_type, count in body["counts"].items():
        expected = db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.event_type == event_type)
        )
        assert count == expected, event_type


def test_audit_is_newest_first(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    stamps = [
        entry["created_at"] for entry in client.get("/api/audit?page_size=20").json()["items"]
    ]

    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.parametrize(
    "query", ["event_type=DROP TABLE", "transaction_id=' OR 1=1", "page_size=99999"]
)
def test_audit_rejects_hostile_parameters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/audit?{query}").status_code == 422


def test_audit_service_is_read_only(db_session: Session, decided: int) -> None:
    """Reading the trail must not add to it."""
    before = db_session.scalar(select(func.count(AuditLog.id)))
    audit.page(db_session, audit.statement(), 1, 50)
    audit.event_type_counts(db_session)

    assert db_session.scalar(select(func.count(AuditLog.id))) == before


# --------------------------------------------------------------------------
# Policy viewer
# --------------------------------------------------------------------------
def test_policy_endpoint_exposes_the_active_policy(client: TestClient) -> None:
    body = client.get("/api/policy").json()
    policy = load_policy()

    assert body["policy_version"] == policy.policy_version
    assert body["thresholds"]["fraud_block"] == pytest.approx(policy.thresholds.fraud_block)
    assert body["action_precedence"] == [str(a) for a in policy.actions.precedence]
    assert body["default_action"] == str(policy.actions.default)


def test_policy_endpoint_lists_every_rule(client: TestClient) -> None:
    body = client.get("/api/policy").json()

    assert {rule["rule_id"] for rule in body["rules"]} == KNOWN_RULE_IDS
    for rule in body["rules"]:
        assert rule["action"]
        assert rule["description"], rule["rule_id"]


def test_policy_endpoint_is_read_only(client: TestClient) -> None:
    """Phase 7 exposes no write path, and says so in the payload."""
    assert client.get("/api/policy").json()["editable"] is False
    assert client.post("/api/policy", json={"policy_version": "hacked"}).status_code == 405
    assert client.put("/api/policy", json={}).status_code == 405
    assert client.delete("/api/policy").status_code == 405


def test_policy_endpoint_reports_fail_safes(client: TestClient) -> None:
    fail_safe = client.get("/api/policy").json()["fail_safe"]

    assert fail_safe["missing_supervised_signal"] != "APPROVE"
    assert fail_safe["missing_anomaly_signal"] != "APPROVE"
    assert fail_safe["missing_investigation"] != "APPROVE"
    assert fail_safe["require_investigation_for_block"] is True


# --------------------------------------------------------------------------
# System health
# --------------------------------------------------------------------------
def test_system_health_reports_every_component(client: TestClient) -> None:
    body = client.get("/api/system/health").json()
    names = {component["name"] for component in body["components"]}

    assert names == {
        "backend",
        "database",
        "fraud_model",
        "anomaly_model",
        "investigation_agent",
        "policy_engine",
    }
    assert body["status"] in {"ok", "degraded", "unavailable"}


def test_system_health_reports_the_policy_version(client: TestClient) -> None:
    body = client.get("/api/system/health").json()
    engine = next(c for c in body["components"] if c["name"] == "policy_engine")

    assert engine["status"] == "ok"
    assert engine["version"] == load_policy().policy_version


def test_system_health_leaks_no_internals(client: TestClient) -> None:
    raw = client.get("/api/system/health").text.lower()

    for secret in ("password", "api_key", "postgresql://", "c:\\\\", "/srv/"):
        assert secret not in raw
