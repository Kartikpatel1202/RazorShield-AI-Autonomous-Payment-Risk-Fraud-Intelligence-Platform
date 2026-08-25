"""The decision endpoint, its persistence, and the immutability of the record.

Signals are written directly rather than produced by running the models: the
decision engine consumes *stored* signals, so writing them explicitly makes each
test an exact statement about the policy rather than an observation about
whatever the model happened to output that day.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.immutability import ImmutableRecordError
from app.models import (
    AuditLog,
    Investigation,
    ReviewCase,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import InvestigationStatus, SignalSeverity
from app.services.anomaly import ANOMALY_SIGNAL
from app.services.decision import build_context, decide_and_store, load_history, store_decision
from policy.engine import evaluate
from policy.loader import load_policy

NORMAL = "TXN_SCENARIO_A_CURRENT"
SUSPICIOUS = "TXN_SCENARIO_B_CURRENT"
RING = "TXN_SCENARIO_C_CURRENT_1"


# --------------------------------------------------------------------------
# Helpers that place stored signals
# --------------------------------------------------------------------------
def get_transaction(session: Session, reference: str) -> Transaction:
    transaction = session.scalar(select(Transaction).where(Transaction.transaction_id == reference))
    assert transaction is not None, reference
    return transaction


def set_prediction(session: Session, transaction: Transaction, probability: float) -> None:
    session.add(
        RiskPrediction(
            transaction_id=transaction.id,
            fraud_probability=Decimal(str(probability)).quantize(Decimal("0.00001")),
            risk_score=int(round(probability * 100)),
            model_version="xgboost-test",
        )
    )
    session.flush()


def set_anomaly(session: Session, transaction: Transaction, score: int, severity: str) -> None:
    session.add(
        RiskSignal(
            transaction_id=transaction.id,
            signal_name=ANOMALY_SIGNAL,
            signal_value=Decimal(score),
            severity=SignalSeverity(severity.lower()),
            source="isolation-forest-test",
        )
    )
    session.flush()


def set_investigation(
    session: Session,
    transaction: Transaction,
    *,
    status: InvestigationStatus = InvestigationStatus.COMPLETED,
    confidence: float = 0.9,
    high_evidence_tools: int = 2,
    high_findings: int = 2,
    shared_entity: bool = False,
    recommended_action: str = "APPROVE",
) -> Investigation:
    """A stored investigation whose report has the shape Phase 5 writes."""
    evidence: list[dict[str, Any]] = [
        {
            "evidence_id": f"EV-{index + 1:03d}",
            "source_tool": f"tool_{index}",
            "claim": "observed",
            "severity": "HIGH",
            "transaction_id": transaction.transaction_id,
            "observed_before": "2026-01-01T00:00:00Z",
            "details": {"customer_count": 3} if shared_entity and index == 0 else {},
        }
        for index in range(high_evidence_tools)
    ]
    findings = [
        {
            "finding_id": f"F-{index + 1:03d}",
            "title": "concern",
            "severity": "HIGH",
            "explanation": "because",
            "evidence_ids": ["EV-001"],
        }
        for index in range(high_findings)
    ]
    row = Investigation(
        transaction_id=transaction.id,
        public_id=f"INV-{transaction.id:06d}",
        status=status,
        confidence=Decimal(str(confidence)).quantize(Decimal("0.0001")),
        summary="prose the policy must never read",
        report={
            "evidence": evidence,
            "findings": findings,
            "risk_level": "HIGH",
            # Present in the stored document and deliberately unreachable by any rule.
            "recommended_action": recommended_action,
        },
    )
    session.add(row)
    session.flush()
    return row


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------
def test_context_reads_every_stored_signal(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.42)
    set_anomaly(db_session, transaction, 96, "MEDIUM")
    set_investigation(db_session, transaction, shared_entity=True)

    context = build_context(db_session, transaction)

    assert context.supervised.available
    assert context.supervised.probability == pytest.approx(0.42)
    assert context.anomaly.available
    assert context.anomaly.anomaly_score == 96
    assert context.investigation.usable
    assert context.investigation.independent_high_severity_sources == 2
    assert context.investigation.high_severity_findings == 2
    assert context.investigation.shared_entity_observed is True


def test_context_reports_missing_signals_as_unavailable(db_session: Session) -> None:
    context = build_context(db_session, get_transaction(db_session, NORMAL))

    assert context.supervised.available is False
    assert context.anomaly.available is False
    assert context.investigation.available is False


def test_context_carries_no_model_prose(db_session: Session) -> None:
    """The agent's summary and recommendation must not reach the policy layer."""
    transaction = get_transaction(db_session, NORMAL)
    set_investigation(db_session, transaction, recommended_action="BLOCK")

    context = build_context(db_session, transaction)
    fields = vars(context.investigation)

    assert "summary" not in fields
    assert "recommended_action" not in fields
    assert "risk_level" not in fields
    assert "prose" not in repr(context.investigation)


def test_an_injected_recommendation_changes_nothing(db_session: Session) -> None:
    """A compromised agent recommending BLOCK cannot cause a block."""
    policy = load_policy()
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")
    set_investigation(
        db_session, transaction, high_evidence_tools=0, high_findings=0, recommended_action="BLOCK"
    )

    result = evaluate(build_context(db_session, transaction), policy)

    assert str(result.action) == "APPROVE"


def test_an_unusable_investigation_is_not_corroboration(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_investigation(db_session, transaction, status=InvestigationStatus.AGENT_UNAVAILABLE)

    context = build_context(db_session, transaction)

    assert context.investigation.available is True
    assert context.investigation.usable is False


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def test_a_decision_is_persisted_with_its_full_justification(db_session: Session) -> None:
    transaction = get_transaction(db_session, SUSPICIOUS)
    set_prediction(db_session, transaction, 0.99)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction, shared_entity=True)

    result, row = decide_and_store(db_session, transaction)

    assert row.public_id.startswith("DEC-")
    assert str(row.action) == "block"
    assert row.policy_version == result.policy_version
    assert list(row.matched_rules) == list(result.matched_rules)
    assert list(row.reason_codes) == list(result.reason_codes)
    assert row.input_digest == result.input_digest
    assert row.explanation == result.explanation
    assert row.fraud_probability == pytest.approx(Decimal("0.99"))
    assert row.anomaly_score == 100
    assert row.fraud_model_version == "xgboost-test"
    assert row.anomaly_model_version == "isolation-forest-test"
    assert row.investigation_public_id == f"INV-{transaction.id:06d}"


def test_the_detail_column_captures_the_policy_in_force(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    _, row = decide_and_store(db_session, transaction)

    assert row.detail["policy"]["policy_version"] == row.policy_version
    assert "thresholds" in row.detail["policy"]
    assert row.detail["rule_matches"]


def test_an_audit_entry_records_the_decision(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    result, row = decide_and_store(db_session, transaction)

    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.transaction_id == transaction.id, AuditLog.event_type == "risk.decision"
        )
    )
    assert entry is not None
    assert entry.event_data["decision_id"] == row.public_id
    assert entry.event_data["policy_version"] == result.policy_version
    assert entry.event_data["matched_rules"] == list(result.matched_rules)
    assert entry.event_data["reason_codes"] == list(result.reason_codes)
    assert entry.event_data["fraud_model_version"] == "xgboost-test"
    assert entry.event_data["input_digest"] == result.input_digest


def test_deciding_twice_appends_rather_than_replaces(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    _, first = decide_and_store(db_session, transaction)
    _, second = decide_and_store(db_session, transaction)

    history = load_history(db_session, transaction)
    assert len(history) == 2
    assert first.public_id != second.public_id
    assert {row.id for row in history} == {first.id, second.id}


def test_a_stored_decision_cannot_be_modified(db_session: Session) -> None:
    from app.models.enums import DecisionAction

    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")
    _, row = decide_and_store(db_session, transaction)

    row.action = (
        DecisionAction.APPROVE if row.action != DecisionAction.APPROVE else DecisionAction.BLOCK
    )
    with pytest.raises(ImmutableRecordError, match="append-only"):
        db_session.flush()
    db_session.rollback()


def test_a_stored_decision_cannot_be_deleted(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")
    _, row = decide_and_store(db_session, transaction)

    db_session.delete(row)
    with pytest.raises(ImmutableRecordError, match="append-only"):
        db_session.flush()
    db_session.rollback()


def test_repeated_decisions_on_unchanged_inputs_share_a_digest(db_session: Session) -> None:
    """Reproducibility, observed through the database rather than asserted."""
    transaction = get_transaction(db_session, RING)
    set_prediction(db_session, transaction, 0.20)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction)

    digests = {decide_and_store(db_session, transaction)[1].input_digest for _ in range(5)}
    actions = {str(row.action) for row in load_history(db_session, transaction)}

    assert len(digests) == 1
    assert actions == {"review"}


def test_a_review_case_is_opened_for_a_reviewable_decision(db_session: Session) -> None:
    transaction = get_transaction(db_session, RING)
    set_prediction(db_session, transaction, 0.20)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction)

    _, row = decide_and_store(db_session, transaction)

    case = db_session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    assert case is not None
    assert case.risk_decision_id == row.id
    assert str(case.status) == "open"


def test_no_review_case_for_an_approval(db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    decide_and_store(db_session, transaction)

    case = db_session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    assert case is None


def test_store_decision_never_writes_to_the_signal_tables(db_session: Session) -> None:
    """The engine reads signals; it must not revise them."""
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    before = (
        db_session.scalar(select(func.count(RiskPrediction.id))),
        db_session.scalar(select(func.count(RiskSignal.id))),
        db_session.scalar(select(func.count(Investigation.id))),
    )
    policy = load_policy()
    store_decision(
        db_session,
        transaction,
        evaluate(build_context(db_session, transaction), policy),
        policy,
        decided_at=datetime.now(UTC),
    )
    after = (
        db_session.scalar(select(func.count(RiskPrediction.id))),
        db_session.scalar(select(func.count(RiskSignal.id))),
        db_session.scalar(select(func.count(Investigation.id))),
    )

    assert before == after


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------
def test_decision_endpoint_returns_the_full_justification(
    client: TestClient, db_session: Session
) -> None:
    transaction = get_transaction(db_session, RING)
    set_prediction(db_session, transaction, 0.20)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction, shared_entity=True)

    response = client.post("/api/risk/decision", json={"transaction_id": RING})
    assert response.status_code == 200
    body = response.json()

    assert body["decision"] == "REVIEW"
    assert body["transaction_id"] == RING
    assert body["policy_version"] == "policy-v1"
    assert "MODEL_DISAGREEMENT_HIGH_ANOMALY" in body["matched_rules"]
    assert "MODEL_DISAGREEMENT" in body["reason_codes"]
    assert body["requires_human_review"] is True
    assert body["review_case_id"] is not None
    assert body["decision_id"].startswith("DEC-")
    assert len(body["input_digest"]) == 64
    assert body["signals"]["fraud_probability"] == pytest.approx(0.20)
    assert body["signals"]["anomaly_score"] == 100
    assert body["rule_matches"]
    assert all(match["conditions"] for match in body["rule_matches"])


def test_decision_endpoint_approves_a_quiet_transaction(
    client: TestClient, db_session: Session
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.000049)
    set_anomaly(db_session, transaction, 58, "LOW")

    body = client.post("/api/risk/decision", json={"transaction_id": NORMAL}).json()

    assert body["decision"] == "APPROVE"
    assert body["requires_human_review"] is False
    assert body["review_case_id"] is None
    assert body["matched_rules"] == ["LOW_RISK"]


def test_decision_endpoint_blocks_only_with_corroboration(
    client: TestClient, db_session: Session
) -> None:
    transaction = get_transaction(db_session, SUSPICIOUS)
    set_prediction(db_session, transaction, 0.999644)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction, shared_entity=True)

    body = client.post("/api/risk/decision", json={"transaction_id": SUSPICIOUS}).json()

    assert body["decision"] == "BLOCK"
    assert "INDEPENDENT_CORROBORATION" in body["reason_codes"]
    assert body["requires_human_review"] is True


def test_decision_endpoint_rejects_an_unknown_transaction(client: TestClient) -> None:
    response = client.post("/api/risk/decision", json={"transaction_id": "TXN_NOPE"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "reference",
    ["'; DROP TABLE transactions;--", "../../etc/passwd", "TXN OR 1=1", "a" * 65, ""],
)
def test_decision_endpoint_validates_the_transaction_reference(
    client: TestClient, reference: str
) -> None:
    response = client.post("/api/risk/decision", json={"transaction_id": reference})
    assert response.status_code == 422


def test_decision_response_leaks_nothing_sensitive(client: TestClient, db_session: Session) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    raw = client.post("/api/risk/decision", json={"transaction_id": NORMAL}).text.lower()

    for secret in ("password", "api_key", "postgresql://", "c:\\\\", "/srv/", ".joblib"):
        assert secret not in raw


def test_decision_history_endpoint_lists_every_decision(
    client: TestClient, db_session: Session
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    set_prediction(db_session, transaction, 0.001)
    set_anomaly(db_session, transaction, 5, "LOW")

    client.post("/api/risk/decision", json={"transaction_id": NORMAL})
    client.post("/api/risk/decision", json={"transaction_id": NORMAL})

    body = client.get(f"/api/transactions/{NORMAL}/decisions").json()

    assert body["transaction_id"] == NORMAL
    assert len(body["decisions"]) == 2
    assert {d["decision"] for d in body["decisions"]} == {"APPROVE"}
    assert body["decisions"][0]["rule_matches"]


def test_the_history_replays_a_decision_from_the_stored_row(
    client: TestClient, db_session: Session
) -> None:
    """A historical decision reads as it did when made."""
    transaction = get_transaction(db_session, RING)
    set_prediction(db_session, transaction, 0.20)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction)

    live = client.post("/api/risk/decision", json={"transaction_id": RING}).json()
    stored = client.get(f"/api/transactions/{RING}/decisions").json()["decisions"][0]

    assert stored["decision_id"] == live["decision_id"]
    assert stored["decision"] == live["decision"]
    assert stored["matched_rules"] == live["matched_rules"]
    assert stored["deciding_rules"] == live["deciding_rules"]
    assert stored["reason_codes"] == live["reason_codes"]
    assert stored["explanation"] == live["explanation"]
    assert stored["input_digest"] == live["input_digest"]


# --------------------------------------------------------------------------
# Missing signals, end to end
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("with_prediction", "with_anomaly", "expected_rule"),
    [
        (False, True, "MISSING_SUPERVISED_SIGNAL"),
        (True, False, "MISSING_ANOMALY_SIGNAL"),
    ],
)
def test_a_missing_signal_never_approves_through_the_api(
    client: TestClient,
    db_session: Session,
    with_prediction: bool,
    with_anomaly: bool,
    expected_rule: str,
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    if with_prediction:
        set_prediction(db_session, transaction, 0.001)
    if with_anomaly:
        set_anomaly(db_session, transaction, 5, "LOW")

    body = client.post("/api/risk/decision", json={"transaction_id": NORMAL}).json()

    assert body["decision"] != "APPROVE"
    assert expected_rule in body["matched_rules"]
