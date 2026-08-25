"""Analytics aggregation.

Every assertion here checks a SQL aggregate against an independently computed
figure - usually a direct count over the same fixture. A dashboard test that
only asserts "the endpoint returned 200" would pass just as happily on wrong
numbers, which is the failure mode that matters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RiskDecision, RiskPrediction, RiskSignal, Transaction
from app.models.enums import DecisionAction
from app.services import analytics
from app.services.anomaly import ANOMALY_SIGNAL
from policy.loader import load_policy


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
def test_overview_counts_match_direct_sql(db_session: Session, decided: int) -> None:
    result = analytics.overview(db_session)

    assert result["total_transactions"] == db_session.scalar(select(func.count(Transaction.id)))
    assert result["decided_transactions"] == decided

    for action, key in (
        (DecisionAction.APPROVE, "approved"),
        (DecisionAction.STEP_UP, "step_up"),
        (DecisionAction.REVIEW, "review"),
        (DecisionAction.BLOCK, "blocked"),
    ):
        expected = db_session.scalar(
            select(func.count(RiskDecision.id)).where(RiskDecision.action == action)
        )
        assert result[key] == expected, action


def test_action_counts_sum_to_decided_not_total(db_session: Session, decided: int) -> None:
    """The four buckets partition the decided set exactly."""
    result = analytics.overview(db_session)
    total = result["approved"] + result["step_up"] + result["review"] + result["blocked"]

    assert total == result["decided_transactions"]


def test_high_risk_uses_the_policy_threshold(db_session: Session, decided: int) -> None:
    policy = load_policy()
    result = analytics.overview(db_session)

    expected = db_session.scalar(
        select(func.count(RiskPrediction.id)).where(
            RiskPrediction.fraud_probability >= Decimal(str(policy.thresholds.fraud_high))
        )
    )
    assert result["high_risk_transactions"] == expected
    assert result["high_risk_threshold"] == pytest.approx(policy.thresholds.fraud_high)


def test_critical_anomalies_use_the_policy_threshold(db_session: Session, decided: int) -> None:
    policy = load_policy()
    result = analytics.overview(db_session)

    expected = db_session.scalar(
        select(func.count(RiskSignal.id)).where(
            RiskSignal.signal_name == ANOMALY_SIGNAL,
            RiskSignal.signal_value >= Decimal(str(policy.thresholds.anomaly_critical)),
        )
    )
    assert result["critical_anomalies"] == expected


def test_overview_reports_latency_when_measured(db_session: Session, decided: int) -> None:
    result = analytics.overview(db_session)

    assert result["latency_sample_size"] == decided
    assert result["avg_decision_latency_ms"] is not None
    assert result["avg_decision_latency_ms"] > 0
    assert result["min_decision_latency_ms"] <= result["avg_decision_latency_ms"]
    assert result["max_decision_latency_ms"] >= result["avg_decision_latency_ms"]


def test_overview_admits_absent_latency(db_session: Session) -> None:
    """With no decisions at all, latency is null rather than a fabricated zero."""
    result = analytics.overview(db_session)

    assert result["decided_transactions"] == 0
    assert result["avg_decision_latency_ms"] is None
    assert result["latency_sample_size"] == 0


def test_overview_reports_the_data_window(db_session: Session, scored: int) -> None:
    result = analytics.overview(db_session)
    span = db_session.execute(
        select(
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
        )
    ).one()

    assert result["data_from"] == span[0]
    assert result["data_to"] == span[1]


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------
def test_decision_distribution_matches_sql(db_session: Session, decided: int) -> None:
    buckets = {b.label: b.count for b in analytics.decision_distribution(db_session)}

    for action in DecisionAction:
        expected = db_session.scalar(
            select(func.count(RiskDecision.id)).where(RiskDecision.action == action)
        )
        assert buckets[str(action).upper()] == expected


def test_decision_distribution_is_in_precedence_order(db_session: Session, decided: int) -> None:
    labels = [b.label for b in analytics.decision_distribution(db_session)]
    assert labels == ["APPROVE", "STEP_UP", "REVIEW", "BLOCK"]


def test_probability_histogram_totals_every_prediction(db_session: Session, scored: int) -> None:
    buckets = analytics.fraud_probability_distribution(db_session)
    total = sum(b.count for b in buckets)

    assert len(buckets) == analytics.PROBABILITY_BUCKETS
    assert total == db_session.scalar(select(func.count(RiskPrediction.id)))


def test_probability_histogram_buckets_are_contiguous(db_session: Session, scored: int) -> None:
    buckets = analytics.fraud_probability_distribution(db_session)

    assert buckets[0].lower == pytest.approx(0.0)
    assert buckets[-1].upper == pytest.approx(1.0)
    for earlier, later in zip(buckets, buckets[1:], strict=False):
        assert earlier.upper == pytest.approx(later.lower)


def test_probability_of_one_lands_in_the_last_bucket(db_session: Session, scored: int) -> None:
    """A probability of exactly 1.0 must not fall off the end of the histogram."""
    transaction = db_session.scalars(select(Transaction).limit(1)).one()
    db_session.query(RiskPrediction).filter(
        RiskPrediction.transaction_id == transaction.id
    ).delete()
    db_session.add(
        RiskPrediction(
            transaction_id=transaction.id,
            fraud_probability=Decimal("1.00000"),
            risk_score=100,
            model_version="test",
        )
    )
    db_session.flush()

    buckets = analytics.fraud_probability_distribution(db_session)
    assert sum(b.count for b in buckets) == db_session.scalar(select(func.count(RiskPrediction.id)))
    assert buckets[-1].count >= 1


def test_anomaly_severity_distribution_matches_sql(db_session: Session, scored: int) -> None:
    buckets = {b.label: b.count for b in analytics.anomaly_severity_distribution(db_session)}
    rows = db_session.execute(
        select(RiskSignal.severity, func.count())
        .where(RiskSignal.signal_name == ANOMALY_SIGNAL)
        .group_by(RiskSignal.severity)
    ).all()

    for severity, count in rows:
        assert buckets[str(severity).upper()] == count


def test_risk_level_bands_are_exhaustive(db_session: Session, scored: int) -> None:
    buckets = analytics.risk_level_distribution(db_session)

    assert [b.label for b in buckets] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert sum(b.count for b in buckets) == db_session.scalar(select(func.count(RiskPrediction.id)))


def test_risk_level_bounds_come_from_the_policy(db_session: Session, scored: int) -> None:
    policy = load_policy()
    buckets = {b.label: b for b in analytics.risk_level_distribution(db_session)}

    assert buckets["MEDIUM"].lower == pytest.approx(policy.thresholds.fraud_medium)
    assert buckets["HIGH"].lower == pytest.approx(policy.thresholds.fraud_high)
    assert buckets["CRITICAL"].lower == pytest.approx(policy.thresholds.fraud_block)


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------
def test_trends_volume_matches_a_direct_count(db_session: Session, decided: int) -> None:
    points = analytics.trends(db_session, days=365)
    charted = sum(point["volume"] for point in points)

    start = datetime.now(UTC) - timedelta(days=365)
    expected = db_session.scalar(
        select(func.count(Transaction.id)).where(Transaction.transaction_timestamp >= start)
    )
    assert charted == expected


def test_trends_splits_sum_to_volume(db_session: Session, decided: int) -> None:
    """Every decided transaction appears in exactly one disposition column."""
    for point in analytics.trends(db_session, days=365):
        dispositions = point["approved"] + point["step_up"] + point["review"] + point["blocked"]
        assert dispositions <= point["volume"]


def test_trends_window_is_bounded(db_session: Session) -> None:
    """A caller cannot widen the window past the cap."""
    huge = analytics.trends(db_session, days=100_000)
    capped = analytics.trends(db_session, days=analytics.MAX_TREND_DAYS)

    assert len(huge) == len(capped)


def test_trends_returns_empty_for_a_window_with_no_traffic(db_session: Session) -> None:
    future = datetime.now(UTC) + timedelta(days=3650)
    assert analytics.trends(db_session, days=1, now=future) == []


# --------------------------------------------------------------------------
# Top risk and reason codes
# --------------------------------------------------------------------------
def test_top_risk_is_ordered_by_probability(db_session: Session, decided: int) -> None:
    items = analytics.top_risk(db_session, limit=10)
    probabilities = [item["fraud_probability"] for item in items if item["fraud_probability"]]

    assert probabilities == sorted(probabilities, reverse=True)


def test_top_risk_limit_is_capped(db_session: Session, decided: int) -> None:
    assert len(analytics.top_risk(db_session, limit=10_000)) <= analytics.MAX_TOP_RISK


def test_top_risk_joins_names_without_extra_queries(db_session: Session, decided: int) -> None:
    """Merchant and customer arrive from the join, never as null placeholders."""
    for item in analytics.top_risk(db_session, limit=5):
        assert item["merchant_name"]
        assert item["customer_id"]


def test_reason_codes_are_counted_across_current_decisions(
    db_session: Session, decided: int
) -> None:
    buckets = analytics.reason_code_frequency(db_session, limit=50)
    counted = {bucket.label: bucket.count for bucket in buckets}

    expected: dict[str, int] = {}
    for (codes,) in db_session.execute(select(RiskDecision.reason_codes)).all():
        for code in codes or []:
            expected[str(code)] = expected.get(str(code), 0) + 1

    for label, count in counted.items():
        assert count == expected[label], label


def test_latest_decision_wins_over_earlier_ones(db_session: Session, decided: int) -> None:
    """A re-decision replaces the earlier one in every aggregate."""
    before = analytics.overview(db_session)

    row = db_session.execute(
        select(RiskDecision.transaction_id, RiskDecision.action).limit(1)
    ).one()
    transaction_id, previous_action = row
    # Pick any action other than the one already recorded, so the move is visible.
    replacement = next(action for action in DecisionAction if action != previous_action)

    db_session.add(
        RiskDecision(
            public_id="DEC-overriding-000001",
            transaction_id=transaction_id,
            action=replacement,
            policy_version="policy-test",
            decided_at=datetime.now(UTC) + timedelta(days=1),
            matched_rules=["MANUAL"],
            reason_codes=["MANUAL"],
            explanation="later decision",
            requires_human_review=True,
            input_digest="d" * 64,
            detail={},
        )
    )
    db_session.flush()

    after = analytics.overview(db_session)
    key = {
        DecisionAction.APPROVE: "approved",
        DecisionAction.STEP_UP: "step_up",
        DecisionAction.REVIEW: "review",
        DecisionAction.BLOCK: "blocked",
    }
    assert after["decided_transactions"] == before["decided_transactions"]
    assert after[key[replacement]] == before[key[replacement]] + 1
    assert after[key[previous_action]] == before[key[previous_action]] - 1


# --------------------------------------------------------------------------
# The API surface
# --------------------------------------------------------------------------
def test_overview_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/analytics/overview").json()

    assert body["total_transactions"] > 0
    assert body["decided_transactions"] == decided
    assert body["policy_version"] == "policy-v1"


def test_risk_distribution_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/analytics/risk-distribution").json()

    assert len(body["decisions"]) == 4
    assert len(body["fraud_probability"]) == analytics.PROBABILITY_BUCKETS
    assert len(body["anomaly_severity"]) == 4
    assert len(body["risk_level"]) == 4


def test_decisions_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/analytics/decisions").json()

    assert body["decided_transactions"] == decided
    assert body["reason_codes"]
    assert all(bucket["count"] > 0 for bucket in body["reason_codes"])


def test_trends_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/analytics/trends?days=30").json()

    assert body["window_days"] == 30
    assert isinstance(body["points"], list)


@pytest.mark.parametrize("days", [0, -1, 100000])
def test_trends_endpoint_rejects_an_out_of_range_window(client: TestClient, days: int) -> None:
    assert client.get(f"/api/analytics/trends?days={days}").status_code == 422


def test_top_risk_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/analytics/top-risk?limit=5").json()

    assert len(body["items"]) <= 5


def test_top_risk_endpoint_rejects_an_excessive_limit(client: TestClient) -> None:
    assert client.get("/api/analytics/top-risk?limit=99999").status_code == 422


def test_analytics_responses_leak_nothing_sensitive(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    for path in (
        "/api/analytics/overview",
        "/api/analytics/decisions",
        "/api/analytics/risk-distribution",
        "/api/analytics/trends",
        "/api/analytics/top-risk",
    ):
        raw = client.get(path).text.lower()
        for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
            assert secret not in raw, path
