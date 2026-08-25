"""The human review queue and its resolutions.

The property under test throughout: an analyst's answer is recorded *next to*
the machine decision, never over it. Every test that resolves a case also
asserts the original decision is unchanged.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalystDecision, AuditLog, ReviewCase
from app.models.enums import ReviewCaseStatus, ReviewResolution
from app.services.decision import decide_and_store
from app.services.review import ReviewCaseError, is_override, resolve_case
from tests.test_api_decisions import (
    RING,
    SUSPICIOUS,
    get_transaction,
    set_anomaly,
    set_investigation,
    set_prediction,
)


def make_review_case(db_session: Session, reference: str = RING) -> tuple[ReviewCase, str]:
    """A transaction decided into the queue. Returns the case and the decision id."""
    transaction = get_transaction(db_session, reference)
    set_prediction(db_session, transaction, 0.20)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction)

    _, decision = decide_and_store(db_session, transaction)
    case = db_session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    assert case is not None
    return case, decision.public_id


def make_blocked_case(db_session: Session, reference: str = SUSPICIOUS) -> ReviewCase:
    transaction = get_transaction(db_session, reference)
    set_prediction(db_session, transaction, 0.99)
    set_anomaly(db_session, transaction, 100, "CRITICAL")
    set_investigation(db_session, transaction)

    _, decision = decide_and_store(db_session, transaction)
    assert str(decision.action) == "block"
    case = db_session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    assert case is not None
    return case


# --------------------------------------------------------------------------
# Opening cases
# --------------------------------------------------------------------------
def test_a_case_records_why_it_was_opened(db_session: Session) -> None:
    case, decision_id = make_review_case(db_session)

    assert case.status is ReviewCaseStatus.OPEN
    assert case.resolution is None
    assert case.reason is not None
    assert "REVIEW" in case.reason
    assert case.risk_decision is not None
    assert case.risk_decision.public_id == decision_id


def test_opening_a_case_writes_an_audit_entry(db_session: Session) -> None:
    case, decision_id = make_review_case(db_session)

    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.transaction_id == case.transaction_id,
            AuditLog.event_type == "review.case_opened",
        )
    )
    assert entry is not None
    assert entry.event_data["decision_id"] == decision_id
    assert entry.event_data["review_case_id"] == case.id


def test_re_deciding_repoints_the_case_rather_than_duplicating_it(db_session: Session) -> None:
    case, first_decision = make_review_case(db_session)
    transaction = case.transaction

    _, second_decision = decide_and_store(db_session, transaction)

    cases = list(
        db_session.scalars(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    )
    assert len(cases) == 1
    assert cases[0].risk_decision is not None
    assert cases[0].risk_decision.public_id == second_decision.public_id
    assert second_decision.public_id != first_decision


# --------------------------------------------------------------------------
# Resolving
# --------------------------------------------------------------------------
def test_resolving_leaves_the_machine_decision_untouched(db_session: Session) -> None:
    case, _ = make_review_case(db_session)
    decision = case.risk_decision
    assert decision is not None
    before = (
        str(decision.action),
        list(decision.matched_rules),
        list(decision.reason_codes),
        decision.explanation,
        decision.input_digest,
    )

    resolve_case(db_session, case, ReviewResolution.APPROVED, analyst_id=None, reason="known good")

    db_session.refresh(decision)
    after = (
        str(decision.action),
        list(decision.matched_rules),
        list(decision.reason_codes),
        decision.explanation,
        decision.input_digest,
    )
    assert before == after


def test_a_resolution_is_recorded_on_the_case_and_the_ledger(db_session: Session) -> None:
    case, _ = make_review_case(db_session)

    resolve_case(db_session, case, ReviewResolution.REJECTED, analyst_id=None, reason="fraud ring")

    assert case.resolution is ReviewResolution.REJECTED
    assert case.resolution_reason == "fraud ring"
    assert case.status is ReviewCaseStatus.RESOLVED
    assert case.resolved_at is not None

    ledger = list(
        db_session.scalars(select(AnalystDecision).where(AnalystDecision.review_case_id == case.id))
    )
    assert len(ledger) == 1
    assert str(ledger[0].decision) == "block"


def test_escalation_keeps_the_case_open(db_session: Session) -> None:
    case, _ = make_review_case(db_session)

    resolve_case(db_session, case, ReviewResolution.ESCALATED, reason="needs a senior view")

    assert case.status is ReviewCaseStatus.ESCALATED
    assert case.resolved_at is None
    assert case.resolution is ReviewResolution.ESCALATED


def test_an_escalated_case_can_still_be_settled(db_session: Session) -> None:
    case, _ = make_review_case(db_session)
    resolve_case(db_session, case, ReviewResolution.ESCALATED)

    resolve_case(db_session, case, ReviewResolution.APPROVED, reason="senior review cleared it")

    assert case.status is ReviewCaseStatus.RESOLVED
    ledger = list(
        db_session.scalars(select(AnalystDecision).where(AnalystDecision.review_case_id == case.id))
    )
    assert len(ledger) == 2  # both steps survive


def test_a_settled_case_cannot_be_resolved_again(db_session: Session) -> None:
    case, _ = make_review_case(db_session)
    resolve_case(db_session, case, ReviewResolution.APPROVED)

    with pytest.raises(ReviewCaseError, match="already resolved"):
        resolve_case(db_session, case, ReviewResolution.REJECTED)


def test_resolution_writes_an_audit_entry_naming_both_outcomes(db_session: Session) -> None:
    # A BLOCK released by an analyst - the engine took a position and the
    # analyst contradicted it, so this genuinely is an override.
    case = make_blocked_case(db_session)

    resolve_case(db_session, case, ReviewResolution.APPROVED, reason="false positive")

    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.transaction_id == case.transaction_id,
            AuditLog.event_type == "review.resolved",
        )
    )
    assert entry is not None
    assert entry.event_data["machine_decision"] == "block"
    assert entry.event_data["resolution"] == "approved"
    assert entry.event_data["overrides_machine_decision"] is True


# --------------------------------------------------------------------------
# Override accounting
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("machine", "resolution", "expected"),
    [
        # The engine took a position and the analyst contradicted it.
        ("block", ReviewResolution.APPROVED, True),
        ("approve", ReviewResolution.REJECTED, True),
        ("step_up", ReviewResolution.REJECTED, True),
        # The engine took a position and the analyst agreed.
        ("block", ReviewResolution.REJECTED, False),
        ("approve", ReviewResolution.APPROVED, False),
        ("step_up", ReviewResolution.APPROVED, False),
        # REVIEW is the engine declining to decide and asking for a human. It
        # makes no claim about approve or block, so nothing the analyst
        # concludes can contradict it. Counting these would put every
        # review-producing rule at a 100% override rate, which measures how
        # often the policy asked for help - not how often it was wrong.
        ("review", ReviewResolution.APPROVED, False),
        ("review", ReviewResolution.REJECTED, False),
        # Escalation settles nothing.
        ("block", ReviewResolution.ESCALATED, False),
        ("approve", ReviewResolution.ESCALATED, False),
        # No decision to contradict.
        (None, ReviewResolution.APPROVED, False),
    ],
)
def test_override_is_judged_consistently(
    machine: str | None, resolution: ReviewResolution, expected: bool
) -> None:
    assert is_override(machine, resolution) is expected


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------
def test_queue_endpoint_lists_open_cases(client: TestClient, db_session: Session) -> None:
    case, decision_id = make_review_case(db_session)
    db_session.commit()

    body = client.get("/api/reviews").json()

    assert body["meta"]["total_items"] >= 1
    entry = next(item for item in body["items"] if item["review_case_id"] == case.id)
    assert entry["status"] == "open"
    assert entry["decision"]["decision_id"] == decision_id
    assert entry["decision"]["decision"] == "REVIEW"
    assert entry["decision"]["matched_rules"]


def test_queue_endpoint_filters_by_status(client: TestClient, db_session: Session) -> None:
    case, _ = make_review_case(db_session)
    db_session.commit()

    open_ids = {
        item["review_case_id"] for item in client.get("/api/reviews?status=open").json()["items"]
    }
    resolved_ids = {
        item["review_case_id"]
        for item in client.get("/api/reviews?status=resolved").json()["items"]
    }

    assert case.id in open_ids
    assert case.id not in resolved_ids


def test_queue_endpoint_filters_by_transaction(client: TestClient, db_session: Session) -> None:
    case, _ = make_review_case(db_session)
    db_session.commit()

    body = client.get(f"/api/reviews?transaction_id={RING}").json()

    assert [item["review_case_id"] for item in body["items"]] == [case.id]
    assert body["items"][0]["transaction_id"] == RING


def test_queue_endpoint_filters_by_machine_decision(
    client: TestClient, db_session: Session
) -> None:
    review_case, _ = make_review_case(db_session)
    blocked_case = make_blocked_case(db_session)
    db_session.commit()

    blocked = {
        item["review_case_id"] for item in client.get("/api/reviews?decision=block").json()["items"]
    }
    reviewed = {
        item["review_case_id"]
        for item in client.get("/api/reviews?decision=review").json()["items"]
    }

    assert blocked_case.id in blocked
    assert review_case.id in reviewed
    assert review_case.id not in blocked


def test_queue_endpoint_filters_by_creation_time(client: TestClient, db_session: Session) -> None:
    from datetime import timedelta

    case, _ = make_review_case(db_session)
    db_session.commit()
    created = case.created_at

    # Passed as a parameter rather than interpolated: the "+00:00" offset must be
    # URL-encoded or the "+" arrives as a space.
    cutoff = (created - timedelta(minutes=1)).isoformat()
    after = client.get("/api/reviews", params={"created_after": cutoff}).json()
    before = client.get("/api/reviews", params={"created_before": cutoff}).json()

    assert case.id in {item["review_case_id"] for item in after["items"]}
    assert case.id not in {item["review_case_id"] for item in before["items"]}


def test_queue_endpoint_paginates(client: TestClient, db_session: Session) -> None:
    make_review_case(db_session, RING)
    make_blocked_case(db_session, SUSPICIOUS)
    db_session.commit()

    body = client.get("/api/reviews?page=1&page_size=1").json()

    assert len(body["items"]) == 1
    assert body["meta"]["page_size"] == 1
    assert body["meta"]["total_items"] >= 2
    assert body["meta"]["has_next"] is True


def test_resolve_endpoint_records_the_analyst_outcome(
    client: TestClient, db_session: Session
) -> None:
    case = make_blocked_case(db_session)
    db_session.commit()

    response = client.post(
        f"/api/reviews/{case.id}/resolve",
        json={"resolution": "approved", "reason": "verified with the customer"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["resolution"] == "approved"
    assert body["status"] == "resolved"
    assert body["machine_decision"] == "BLOCK"
    assert body["overrides_machine_decision"] is True
    assert body["resolution_reason"] == "verified with the customer"


def test_resolve_endpoint_does_not_alter_the_stored_decision(
    client: TestClient, db_session: Session
) -> None:
    case = make_blocked_case(db_session)
    db_session.commit()
    reference = case.transaction.transaction_id
    before = client.get(f"/api/transactions/{reference}/decisions").json()

    client.post(f"/api/reviews/{case.id}/resolve", json={"resolution": "approved"})
    after = client.get(f"/api/transactions/{reference}/decisions").json()

    assert before == after


def test_resolve_endpoint_rejects_a_second_resolution(
    client: TestClient, db_session: Session
) -> None:
    case, _ = make_review_case(db_session)
    db_session.commit()

    first = client.post(f"/api/reviews/{case.id}/resolve", json={"resolution": "approved"})
    second = client.post(f"/api/reviews/{case.id}/resolve", json={"resolution": "rejected"})

    assert first.status_code == 200
    assert second.status_code == 409


def test_resolve_endpoint_rejects_an_unknown_case(client: TestClient) -> None:
    response = client.post("/api/reviews/999999/resolve", json={"resolution": "approved"})
    assert response.status_code == 404


def test_resolve_endpoint_rejects_an_unknown_resolution(
    client: TestClient, db_session: Session
) -> None:
    case, _ = make_review_case(db_session)
    db_session.commit()

    response = client.post(
        f"/api/reviews/{case.id}/resolve", json={"resolution": "delete_everything"}
    )
    assert response.status_code == 422


def test_escalating_through_the_api_keeps_the_case_live(
    client: TestClient, db_session: Session
) -> None:
    case, _ = make_review_case(db_session)
    db_session.commit()

    body = client.post(f"/api/reviews/{case.id}/resolve", json={"resolution": "escalated"}).json()

    assert body["status"] == "escalated"
    assert body["resolved_at"] is None
    assert body["overrides_machine_decision"] is False


def test_queue_response_leaks_nothing_sensitive(client: TestClient, db_session: Session) -> None:
    make_review_case(db_session)
    db_session.commit()

    raw = client.get("/api/reviews").text.lower()

    for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
        assert secret not in raw
