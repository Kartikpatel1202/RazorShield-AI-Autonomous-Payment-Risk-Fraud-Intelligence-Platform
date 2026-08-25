"""Analyst feedback: recording it, validating it, and aggregating it.

The properties that matter most here are the ones that protect the metrics
downstream: an incoherent outcome/reason pair must never be stored, a machine
decision must never be edited, and an absent label must never be counted as a
negative example.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalystFeedback, ReviewCase, RiskDecision, Transaction
from app.models.enums import (
    FEEDBACK_REASONS_BY_OUTCOME,
    DecisionAction,
    FeedbackOutcome,
    FeedbackReason,
)
from app.services import feedback as feedback_service
from app.services.feedback import FeedbackValidationError
from tests.test_api_decisions import NORMAL, RING, SUSPICIOUS, get_transaction

pytest_plugins: list[str] = []


# --------------------------------------------------------------------------
# The enum contract
# --------------------------------------------------------------------------
def test_only_verdict_outcomes_count_as_ground_truth() -> None:
    """Open outcomes must not be treated as labels."""
    truth = {outcome for outcome in FeedbackOutcome if outcome.is_ground_truth}

    assert truth == {
        FeedbackOutcome.CONFIRMED_FRAUD,
        FeedbackOutcome.LEGITIMATE,
        FeedbackOutcome.FALSE_POSITIVE,
        FeedbackOutcome.FALSE_NEGATIVE,
    }
    assert not FeedbackOutcome.INSUFFICIENT_EVIDENCE.is_ground_truth
    assert not FeedbackOutcome.ESCALATED.is_ground_truth


def test_fraud_indicating_outcomes() -> None:
    fraud = {outcome for outcome in FeedbackOutcome if outcome.indicates_fraud}
    assert fraud == {FeedbackOutcome.CONFIRMED_FRAUD, FeedbackOutcome.FALSE_NEGATIVE}


def test_every_outcome_has_permitted_reasons() -> None:
    assert set(FEEDBACK_REASONS_BY_OUTCOME) == set(FeedbackOutcome)
    for outcome, reasons in FEEDBACK_REASONS_BY_OUTCOME.items():
        assert reasons, outcome


def test_reason_validation_accepts_a_coherent_pair() -> None:
    feedback_service.validate_pair(
        FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.COORDINATED_ACTIVITY
    )


def test_reason_validation_rejects_an_incoherent_pair() -> None:
    """Both values are valid enums and the combination is still nonsense."""
    with pytest.raises(FeedbackValidationError, match="not valid for outcome"):
        feedback_service.validate_pair(
            FeedbackOutcome.LEGITIMATE, FeedbackReason.COORDINATED_ACTIVITY
        )


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------
def test_feedback_is_recorded_against_the_current_decision(
    db_session: Session, decided: int
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    row = feedback_service.record_feedback(
        db_session,
        transaction=transaction,
        outcome=FeedbackOutcome.CONFIRMED_FRAUD,
        reason=FeedbackReason.SUSPICIOUS_DEVICE,
        notes="looked wrong",
    )

    assert row.public_id.startswith("FBK-")
    assert row.risk_decision_id is not None
    decision = db_session.get(RiskDecision, row.risk_decision_id)
    assert decision is not None
    assert decision.transaction_id == transaction.id


def test_recording_feedback_leaves_the_decision_untouched(
    db_session: Session, decided: int
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    decision = db_session.scalar(
        select(RiskDecision).where(RiskDecision.transaction_id == transaction.id)
    )
    assert decision is not None
    before = (str(decision.action), decision.input_digest, list(decision.reason_codes))

    feedback_service.record_feedback(
        db_session,
        transaction=transaction,
        outcome=FeedbackOutcome.FALSE_POSITIVE,
        reason=FeedbackReason.MODEL_FALSE_POSITIVE,
    )
    db_session.refresh(decision)

    assert (str(decision.action), decision.input_digest, list(decision.reason_codes)) == before


def test_a_transaction_may_carry_several_labels(db_session: Session, decided: int) -> None:
    """Feedback is append-only history, like decisions."""
    transaction = get_transaction(db_session, NORMAL)
    for reason in (FeedbackReason.SUSPICIOUS_DEVICE, FeedbackReason.SUSPICIOUS_IP):
        feedback_service.record_feedback(
            db_session,
            transaction=transaction,
            outcome=FeedbackOutcome.CONFIRMED_FRAUD,
            reason=reason,
        )

    count = db_session.scalar(
        select(func.count(AnalystFeedback.id)).where(
            AnalystFeedback.transaction_id == transaction.id
        )
    )
    assert count == 2


# --------------------------------------------------------------------------
# Summary and confusion matrix
# --------------------------------------------------------------------------
def _label(
    db_session: Session, reference: str, outcome: FeedbackOutcome, reason: FeedbackReason
) -> None:
    feedback_service.record_feedback(
        db_session,
        transaction=get_transaction(db_session, reference),
        outcome=outcome,
        reason=reason,
    )


def test_summary_counts_match_direct_sql(db_session: Session, decided: int) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    _label(db_session, RING, FeedbackOutcome.LEGITIMATE, FeedbackReason.KNOWN_CUSTOMER_BEHAVIOR)
    _label(
        db_session,
        SUSPICIOUS,
        FeedbackOutcome.INSUFFICIENT_EVIDENCE,
        FeedbackReason.NEEDS_MORE_INFORMATION,
    )

    summary = feedback_service.summary(db_session)

    assert summary["total_feedback"] == db_session.scalar(select(func.count(AnalystFeedback.id)))
    assert summary["confirmed_fraud"] == 1
    assert summary["legitimate"] == 1
    assert summary["insufficient_evidence"] == 1


def test_open_outcomes_are_excluded_from_ground_truth(db_session: Session, decided: int) -> None:
    """The count that feeds every metric must exclude unsettled questions."""
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    _label(
        db_session,
        RING,
        FeedbackOutcome.INSUFFICIENT_EVIDENCE,
        FeedbackReason.INSUFFICIENT_EVIDENCE,
    )
    _label(
        db_session,
        SUSPICIOUS,
        FeedbackOutcome.ESCALATED,
        FeedbackReason.NEEDS_MORE_INFORMATION,
    )

    summary = feedback_service.summary(db_session)

    assert summary["total_feedback"] == 3
    assert summary["ground_truth_labels"] == 1


def test_summary_reports_labelling_coverage(db_session: Session, decided: int) -> None:
    """A count without its denominator invites the reader to mistake it for the whole."""
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    summary = feedback_service.summary(db_session)

    total = db_session.scalar(select(func.count(Transaction.id)))
    assert summary["total_transactions"] == total
    assert summary["labelled_share_of_transactions"] == pytest.approx(1 / (total or 1))


def test_confusion_matrix_excludes_open_outcomes(db_session: Session, decided: int) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    _label(
        db_session,
        RING,
        FeedbackOutcome.INSUFFICIENT_EVIDENCE,
        FeedbackReason.INSUFFICIENT_EVIDENCE,
    )

    matrix = feedback_service.confusion_matrix(db_session)

    assert matrix["labelled_included"] == 1
    assert matrix["excluded_open_outcomes"] == 1
    assert all(cell["outcome"] != "insufficient_evidence" for cell in matrix["cells"])


def test_confusion_matrix_never_counts_unlabelled_as_negative(
    db_session: Session, decided: int
) -> None:
    """20,000 unreviewed transactions must not become 20,000 true negatives."""
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    matrix = feedback_service.confusion_matrix(db_session)

    quadrants = (
        matrix["true_positive"]
        + matrix["false_positive"]
        + matrix["true_negative"]
        + matrix["false_negative"]
    )
    assert quadrants == 1
    assert quadrants < (db_session.scalar(select(func.count(Transaction.id))) or 0)


def test_confusion_matrix_places_labels_in_the_right_quadrant(
    db_session: Session, decided: int
) -> None:
    transaction = get_transaction(db_session, NORMAL)
    decision = db_session.scalar(
        select(RiskDecision).where(RiskDecision.transaction_id == transaction.id)
    )
    assert decision is not None
    flagged = decision.action != DecisionAction.APPROVE

    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    matrix = feedback_service.confusion_matrix(db_session)

    if flagged:
        assert matrix["true_positive"] == 1
        assert matrix["false_negative"] == 0
    else:
        assert matrix["false_negative"] == 1
        assert matrix["true_positive"] == 0


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------
def test_create_feedback_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    response = client.post(
        "/api/feedback",
        json={
            "transaction_id": RING,
            "outcome": "confirmed_fraud",
            "reason_code": "coordinated_activity",
            "notes": "Shared device and IP confirmed.",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["feedback_id"].startswith("FBK-")
    assert body["outcome"] == "confirmed_fraud"
    assert body["reason_code"] == "coordinated_activity"
    assert body["machine_decision"] in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}


def test_create_feedback_rejects_an_incoherent_pair(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    response = client.post(
        "/api/feedback",
        json={
            "transaction_id": RING,
            "outcome": "legitimate",
            "reason_code": "coordinated_activity",
        },
    )

    assert response.status_code == 422
    assert "not valid for outcome" in response.json()["detail"]


def test_create_feedback_rejects_an_unknown_transaction(client: TestClient) -> None:
    response = client.post(
        "/api/feedback",
        json={
            "transaction_id": "TXN_NOPE",
            "outcome": "legitimate",
            "reason_code": "trusted_merchant",
        },
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {
            "transaction_id": "'; DROP TABLE x;--",
            "outcome": "legitimate",
            "reason_code": "trusted_merchant",
        },
        {"transaction_id": "TXN_A", "outcome": "not_an_outcome", "reason_code": "trusted_merchant"},
        {"transaction_id": "TXN_A", "outcome": "legitimate", "reason_code": "made_up_reason"},
        {"transaction_id": "TXN_A", "outcome": "legitimate"},
        {
            "transaction_id": "TXN_A",
            "outcome": "legitimate",
            "reason_code": "trusted_merchant",
            "analyst_id": 0,
        },
    ],
)
def test_create_feedback_validates_input(client: TestClient, payload: dict[str, object]) -> None:
    assert client.post("/api/feedback", json=payload).status_code == 422


def test_list_feedback_paginates(client: TestClient, db_session: Session, decided: int) -> None:
    for reference in (NORMAL, RING, SUSPICIOUS):
        _label(db_session, reference, FeedbackOutcome.LEGITIMATE, FeedbackReason.TRUSTED_MERCHANT)
    db_session.commit()

    body = client.get("/api/feedback?page=1&page_size=2").json()

    assert len(body["items"]) == 2
    assert body["meta"]["total_items"] == 3
    assert body["meta"]["has_next"] is True


def test_list_feedback_filters_by_outcome(
    client: TestClient, db_session: Session, decided: int
) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    _label(db_session, RING, FeedbackOutcome.LEGITIMATE, FeedbackReason.TRUSTED_MERCHANT)
    db_session.commit()

    body = client.get("/api/feedback?outcome=confirmed_fraud").json()

    assert body["meta"]["total_items"] == 1
    assert body["items"][0]["outcome"] == "confirmed_fraud"


def test_list_feedback_filters_by_transaction(
    client: TestClient, db_session: Session, decided: int
) -> None:
    _label(db_session, RING, FeedbackOutcome.LEGITIMATE, FeedbackReason.TRUSTED_MERCHANT)
    db_session.commit()

    body = client.get(f"/api/feedback?transaction_id={RING}").json()

    assert body["meta"]["total_items"] == 1
    assert body["items"][0]["transaction_id"] == RING


def test_list_feedback_filters_by_date_range(
    client: TestClient, db_session: Session, decided: int
) -> None:
    from datetime import UTC, datetime, timedelta

    _label(db_session, RING, FeedbackOutcome.LEGITIMATE, FeedbackReason.TRUSTED_MERCHANT)
    db_session.commit()
    cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    after = client.get("/api/feedback", params={"created_after": cutoff}).json()
    before = client.get("/api/feedback", params={"created_before": cutoff}).json()

    assert after["meta"]["total_items"] == 1
    assert before["meta"]["total_items"] == 0


@pytest.mark.parametrize(
    "query",
    [
        "outcome=DROP TABLE",
        "reason_code=' OR 1=1",
        "transaction_id=../../etc/passwd",
        "decision_id='; DELETE FROM risk_decisions;--",
        "page_size=100000",
        "page=0",
        "analyst_id=0",
    ],
)
def test_list_feedback_rejects_hostile_parameters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/feedback?{query}").status_code == 422


def test_feedback_summary_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    db_session.commit()

    body = client.get("/api/feedback/summary").json()

    assert body["summary"]["confirmed_fraud"] == 1
    assert body["confusion_matrix"]["labelled_included"] == 1
    assert body["summary"]["total_transactions"] > 1


def test_feedback_endpoints_are_get_only_where_read_only(client: TestClient) -> None:
    assert client.put("/api/feedback", json={}).status_code == 405
    assert client.delete("/api/feedback").status_code == 405
    assert client.post("/api/feedback/summary", json={}).status_code == 405


def test_feedback_responses_leak_nothing_sensitive(
    client: TestClient, db_session: Session, decided: int
) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    db_session.commit()

    for path in ("/api/feedback", "/api/feedback/summary"):
        raw = client.get(path).text.lower()
        for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
            assert secret not in raw, path


# --------------------------------------------------------------------------
# Review integration
# --------------------------------------------------------------------------
def _review_case(db_session: Session, reference: str) -> ReviewCase:
    transaction = get_transaction(db_session, reference)
    case = db_session.scalar(select(ReviewCase).where(ReviewCase.transaction_id == transaction.id))
    assert case is not None, f"no review case for {reference}"
    return case


def test_resolving_a_review_can_record_feedback(
    client: TestClient, db_session: Session, decided: int
) -> None:
    case = _review_case(db_session, RING)
    db_session.commit()

    body = client.post(
        f"/api/reviews/{case.id}/resolve",
        json={
            "resolution": "rejected",
            "reason": "Confirmed ring.",
            "feedback_outcome": "confirmed_fraud",
            "feedback_reason": "coordinated_activity",
            "feedback_notes": "Shared device and IP.",
        },
    ).json()

    assert body["resolution"] == "rejected"
    assert body["feedback_outcome"] == "confirmed_fraud"
    assert body["feedback_reason"] == "coordinated_activity"
    assert body["feedback_id"].startswith("FBK-")
    # The engine's decision is reported unchanged alongside the human's.
    assert body["machine_decision"] in {"REVIEW", "BLOCK"}


def test_resolution_feedback_does_not_alter_the_decision(
    client: TestClient, db_session: Session, decided: int
) -> None:
    case = _review_case(db_session, RING)
    decision_id = case.risk_decision_id
    db_session.commit()

    reference = RING
    before = client.get(f"/api/transactions/{reference}/decisions").json()
    client.post(
        f"/api/reviews/{case.id}/resolve",
        json={
            "resolution": "rejected",
            "feedback_outcome": "confirmed_fraud",
            "feedback_reason": "coordinated_activity",
        },
    )
    after = client.get(f"/api/transactions/{reference}/decisions").json()

    assert before == after
    assert decision_id is not None


def test_resolution_rejects_an_outcome_without_a_reason(
    client: TestClient, db_session: Session, decided: int
) -> None:
    case = _review_case(db_session, RING)
    db_session.commit()

    response = client.post(
        f"/api/reviews/{case.id}/resolve",
        json={"resolution": "rejected", "feedback_outcome": "confirmed_fraud"},
    )
    assert response.status_code == 422


def test_resolution_rejects_a_reason_without_an_outcome(
    client: TestClient, db_session: Session, decided: int
) -> None:
    case = _review_case(db_session, RING)
    db_session.commit()

    response = client.post(
        f"/api/reviews/{case.id}/resolve",
        json={"resolution": "rejected", "feedback_reason": "coordinated_activity"},
    )
    assert response.status_code == 422


def test_resolution_rejects_an_incoherent_feedback_pair(
    client: TestClient, db_session: Session, decided: int
) -> None:
    case = _review_case(db_session, RING)
    db_session.commit()

    response = client.post(
        f"/api/reviews/{case.id}/resolve",
        json={
            "resolution": "approved",
            "feedback_outcome": "legitimate",
            "feedback_reason": "coordinated_activity",
        },
    )
    assert response.status_code == 422


def test_resolution_without_feedback_still_works(
    client: TestClient, db_session: Session, decided: int
) -> None:
    """Feedback is optional - a resolution alone must not require it."""
    case = _review_case(db_session, RING)
    db_session.commit()

    body = client.post(f"/api/reviews/{case.id}/resolve", json={"resolution": "approved"}).json()

    assert body["resolution"] == "approved"
    assert body["feedback_id"] is None
    assert body["feedback_outcome"] is None
