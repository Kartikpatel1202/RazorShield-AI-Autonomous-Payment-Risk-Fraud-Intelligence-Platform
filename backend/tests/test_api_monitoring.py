"""Monitoring: model metrics, drift, policy effectiveness and the funnel.

The assertions here are mostly about restraint - that a metric is *withheld*
when the data cannot support it, that unlabelled rows are excluded, and that a
statistic computed on a biased sample says so.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalystFeedback,
    Base,
    Customer,
    Merchant,
    RiskDecision,
    RiskPrediction,
    Transaction,
)
from app.models.enums import (
    DecisionAction,
    DriftStatus,
    FeedbackOutcome,
    FeedbackReason,
    PaymentMethod,
    RiskLevel,
    TransactionStatus,
)
from app.services import assistant as assistant_service
from app.services import feedback as feedback_service
from app.services import monitoring
from app.services.monitoring_config import (
    MonitoringConfigError,
    load_config,
    parse_config,
)
from policy.loader import load_policy
from tests.test_api_decisions import NORMAL, get_transaction


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def test_monitoring_config_loads() -> None:
    config = load_config()

    assert config.source == "monitoring.yaml"
    assert 0 < config.drift.psi_watch < config.drift.psi_drift
    assert config.metrics.min_labeled_samples >= 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"drift": {"psi_watch": 0.5, "psi_drift": 0.2}},
        {"drift": {"psi_watch": -1}},
        {"drift": {"min_samples": 0}},
        {"drift": {"bins": 1}},
        {"metrics": {"high_override_rate": 0}},
        {"metrics": {"high_override_rate": 2}},
        {"metrics": {"min_labeled_samples": 0}},
    ],
)
def test_invalid_monitoring_config_is_rejected(mutation: dict[str, dict[str, float]]) -> None:
    base = {
        "drift": {
            "psi_watch": 0.1,
            "psi_drift": 0.25,
            "min_samples": 100,
            "bins": 10,
            "baseline_days": 60,
            "current_days": 30,
        },
        "metrics": {
            "min_labeled_samples": 30,
            "high_override_rate": 0.3,
            "min_rule_triggers": 10,
        },
    }
    for section, values in mutation.items():
        base[section] = {**base[section], **values}

    with pytest.raises(MonitoringConfigError):
        parse_config(base)


def test_missing_monitoring_section_is_rejected() -> None:
    with pytest.raises(MonitoringConfigError, match="missing section"):
        parse_config({"drift": {}})


# --------------------------------------------------------------------------
# Model metrics
# --------------------------------------------------------------------------
def _label(
    db_session: Session,
    reference: str,
    outcome: FeedbackOutcome,
    reason: FeedbackReason,
) -> None:
    feedback_service.record_feedback(
        db_session,
        transaction=get_transaction(db_session, reference),
        outcome=outcome,
        reason=reason,
    )


def _label_many(db_session: Session, count: int) -> None:
    """Label enough transactions to clear the reporting floor.

    Half flagged and half approved, so both halves of the confusion matrix are
    populated and the bias warning does not fire.
    """
    rows = db_session.execute(
        select(Transaction.transaction_id, RiskDecision.action)
        .select_from(Transaction)
        .join(RiskDecision, RiskDecision.transaction_id == Transaction.id)
        .limit(count)
    ).all()
    for index, (reference, action) in enumerate(rows):
        flagged = action != DecisionAction.APPROVE
        if index % 2 == 0:
            outcome = FeedbackOutcome.CONFIRMED_FRAUD if flagged else FeedbackOutcome.FALSE_NEGATIVE
            reason = (
                FeedbackReason.CONFIRMED_FRAUD if flagged else FeedbackReason.MODEL_FALSE_NEGATIVE
            )
        else:
            outcome = FeedbackOutcome.FALSE_POSITIVE if flagged else FeedbackOutcome.LEGITIMATE
            reason = (
                FeedbackReason.MODEL_FALSE_POSITIVE
                if flagged
                else FeedbackReason.KNOWN_CUSTOMER_BEHAVIOR
            )
        _label(db_session, reference, outcome, reason)


def test_metrics_withheld_without_enough_labels(db_session: Session, decided: int) -> None:
    """The honest answer, not a placeholder number."""
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)

    metrics = monitoring.model_metrics(db_session)

    assert metrics["sufficient"] is False
    assert "Insufficient labeled data" in metrics["message"]
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None


def test_metrics_reported_once_enough_labels_exist(db_session: Session, decided: int) -> None:
    _label_many(db_session, 60)

    metrics = monitoring.model_metrics(db_session)

    assert metrics["sufficient"] is True
    assert metrics["message"] is None
    assert 0.0 <= metrics["precision"] <= 1.0
    assert 0.0 <= metrics["recall"] <= 1.0


def test_metrics_exclude_unlabelled_transactions(db_session: Session, decided: int) -> None:
    """Unreviewed transactions are not negative examples."""
    _label_many(db_session, 60)
    metrics = monitoring.model_metrics(db_session)

    quadrants = (
        metrics["true_positive"]
        + metrics["false_positive"]
        + metrics["true_negative"]
        + metrics["false_negative"]
    )
    total = db_session.scalar(select(func.count(Transaction.id))) or 0

    assert quadrants == metrics["labelled_samples"]
    assert metrics["unlabelled_transactions"] == total - metrics["total_feedback"]
    assert quadrants < total


def test_metrics_exclude_open_outcomes(db_session: Session, decided: int) -> None:
    _label_many(db_session, 60)
    before = monitoring.model_metrics(db_session)["labelled_samples"]

    _label(
        db_session,
        NORMAL,
        FeedbackOutcome.INSUFFICIENT_EVIDENCE,
        FeedbackReason.INSUFFICIENT_EVIDENCE,
    )
    after = monitoring.model_metrics(db_session)

    assert after["labelled_samples"] == before
    assert after["open_outcome_labels"] >= 1


def test_metrics_warn_when_every_label_came_from_the_queue(
    db_session: Session, decided: int
) -> None:
    """Recall is 1.0 by construction when no approved transaction is labelled."""
    rows = db_session.execute(
        select(Transaction.transaction_id)
        .select_from(Transaction)
        .join(RiskDecision, RiskDecision.transaction_id == Transaction.id)
        .where(RiskDecision.action != DecisionAction.APPROVE)
        .limit(40)
    ).all()
    for (reference,) in rows:
        _label(
            db_session, reference, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD
        )

    metrics = monitoring.model_metrics(db_session)

    if metrics["sufficient"] and metrics["labelled_unflagged"] == 0:
        assert metrics["selection_bias_note"] is not None
        assert "recall" in metrics["selection_bias_note"].lower()


def test_label_coverage_separates_the_three_categories(db_session: Session, decided: int) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    coverage = monitoring.label_coverage(db_session)

    assert coverage["confirmed_labels"] == 1
    assert coverage["unlabelled"] == coverage["total_transactions"] - 1
    assert "never used as ground truth" in coverage["simulated_label_note"]


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------
def test_psi_of_identical_distributions_is_zero() -> None:
    sample = [float(value) for value in range(200)]
    assert monitoring.population_stability_index(sample, sample, 10) == pytest.approx(0.0, abs=1e-9)


def test_psi_grows_when_a_distribution_shifts() -> None:
    baseline = [float(value) for value in range(200)]
    shifted = [float(value) + 500 for value in range(200)]

    small = monitoring.population_stability_index(baseline, [v + 1 for v in baseline], 10)
    large = monitoring.population_stability_index(baseline, shifted, 10)

    assert small is not None and large is not None
    assert large > small


def test_psi_is_none_for_an_empty_sample() -> None:
    assert monitoring.population_stability_index([], [1.0], 10) is None
    assert monitoring.population_stability_index([1.0], [], 10) is None


def test_categorical_psi_detects_a_vanished_category() -> None:
    baseline = {"a": 50, "b": 50}
    current = {"a": 100}

    psi = monitoring.categorical_psi(baseline, current)
    assert psi is not None
    assert psi > 0.25


def test_drift_classification_bands() -> None:
    config = load_config()
    large = config.drift.min_samples + 1

    assert monitoring.classify_drift(0.01, large, config) is DriftStatus.NORMAL
    assert monitoring.classify_drift(0.15, large, config) is DriftStatus.WATCH
    assert monitoring.classify_drift(0.4, large, config) is DriftStatus.DRIFT_DETECTED


def test_drift_refuses_to_classify_a_small_sample() -> None:
    config = load_config()
    assert monitoring.classify_drift(0.9, 1, config) is DriftStatus.INSUFFICIENT_DATA
    assert monitoring.classify_drift(None, 10_000, config) is DriftStatus.INSUFFICIENT_DATA


def test_drift_report_covers_every_monitored_feature(db_session: Session) -> None:
    report = monitoring.drift_report(db_session)
    features = {feature["feature"] for feature in report["features"]}

    assert features == {
        "amount",
        "merchant",
        "device",
        "location",
        "fraud_probability",
        "anomaly_score",
    }


def test_drift_report_states_that_drift_is_not_fraud(db_session: Session) -> None:
    report = monitoring.drift_report(db_session)
    assert "not evidence of fraud" in report["note"]


def test_drift_windows_come_from_stored_timestamps(db_session: Session) -> None:
    report = monitoring.drift_report(db_session)
    latest = db_session.scalar(select(func.max(Transaction.transaction_timestamp)))

    assert report["current_to"] is not None
    assert report["current_to"] == latest
    assert report["baseline_from"] < report["baseline_to"] <= report["current_from"]


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="the PostgreSQL quantile path needs a real PostgreSQL database",
)
def test_numeric_drift_runs_on_postgresql() -> None:
    """The PostgreSQL branch of ``_numeric_drift`` must actually execute.

    Regression test. ``_numeric_drift`` takes a completely different path on
    PostgreSQL - server-side ``percentile_cont`` instead of streaming values into
    Python - and the whole suite runs on SQLite, so that branch was never
    exercised and shipped broken: SQLAlchemy types a ``within_group`` expression
    from its ordering column, so the Numeric result processor was handed the
    returned array and raised ``TypeError: must be real number, not list``.

    This asserts only that the query runs and yields usable numbers on a real
    PostgreSQL connection. Agreement between the two implementations is covered
    separately; what was missing was any coverage of this branch at all.
    """
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            _seed_two_windows(session)
            report = monitoring.drift_report(session)

        numeric = {
            feature["feature"]: feature
            for feature in report["features"]
            if feature["kind"] == "numeric"
        }

        # Only the two features the seed populates. `anomaly_score` has no rows
        # here, and asserting on it would test the fixture rather than the query.
        for name in ("amount", "fraud_probability"):
            feature = numeric[name]
            assert feature["baseline_count"] > 0, name
            assert feature["current_count"] > 0, name
            assert isinstance(feature["psi"], float), name
            assert isinstance(feature["baseline_mean"], float), name
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_two_windows(session: Session) -> None:
    """Minimal transactions and predictions spanning the baseline/current split."""
    merchant = Merchant(
        external_merchant_id="mrc_drift",
        name="Drift Test",
        email="drift@example.test",
        category="retail",
        country="IN",
    )
    customer = Customer(
        merchant=merchant,
        external_customer_id="cus_drift",
        email="cust@example.test",
        account_created_at=datetime.now(UTC) - timedelta(days=400),
        country="IN",
        city="Mumbai",
        historical_risk_level=RiskLevel.LOW,
    )
    session.add_all([merchant, customer])
    session.flush()

    now = datetime.now(UTC)
    for index in range(240):
        # First 120 land in the baseline window, the rest in the current one.
        offset_days = 75 - (index // 2)
        transaction = Transaction(
            transaction_id=f"txn_drift_{index:04d}",
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount=Decimal("100.00") + index,
            currency="INR",
            payment_method=PaymentMethod.CARD,
            status=TransactionStatus.SUCCESSFUL,
            transaction_timestamp=now - timedelta(days=offset_days),
            country="IN",
            city="Mumbai",
            failed_attempts=0,
            is_fraud=False,
        )
        session.add(transaction)
        session.flush()
        session.add(
            RiskPrediction(
                transaction_id=transaction.id,
                fraud_probability=Decimal("0.10000") + Decimal(index) / 1000,
                risk_score=index % 100,
                model_version="xgboost-test",
            )
        )
    session.commit()


# --------------------------------------------------------------------------
# Score windows
# --------------------------------------------------------------------------
def test_score_windows_use_policy_thresholds(db_session: Session) -> None:
    policy = load_policy()
    windows = monitoring.score_windows(db_session)

    assert windows["high_risk_threshold"] == pytest.approx(policy.thresholds.fraud_high)
    assert windows["critical_anomaly_threshold"] == pytest.approx(
        policy.thresholds.anomaly_critical
    )


def test_score_windows_report_both_periods(db_session: Session) -> None:
    windows = monitoring.score_windows(db_session)

    assert windows["baseline"] is not None
    assert windows["current"] is not None
    assert windows["baseline"]["from"] < windows["current"]["from"]


# --------------------------------------------------------------------------
# Policy effectiveness
# --------------------------------------------------------------------------
def test_policy_effectiveness_covers_every_rule(db_session: Session, decided: int) -> None:
    from policy.schema import KNOWN_RULE_IDS

    result = monitoring.policy_effectiveness(db_session)
    assert {rule["rule_id"] for rule in result["rules"]} >= KNOWN_RULE_IDS


def test_policy_trigger_counts_match_the_decisions(db_session: Session, decided: int) -> None:
    result = monitoring.policy_effectiveness(db_session)
    by_id = {rule["rule_id"]: rule for rule in result["rules"]}

    expected: dict[str, int] = {}
    for (matched,) in db_session.execute(select(RiskDecision.matched_rules)).all():
        for rule in matched or []:
            expected[str(rule)] = expected.get(str(rule), 0) + 1

    for rule_id, count in expected.items():
        assert by_id[rule_id]["triggers"] == count, rule_id


def test_override_rate_withheld_below_the_floor(db_session: Session, decided: int) -> None:
    result = monitoring.policy_effectiveness(db_session)

    for rule in result["rules"]:
        if rule["resolved_count"] < result["min_rule_triggers"]:
            assert rule["override_rate"] is None
            assert rule["override_rate_reportable"] is False


def test_policy_effectiveness_explains_what_an_override_is(
    db_session: Session, decided: int
) -> None:
    result = monitoring.policy_effectiveness(db_session)
    assert "REVIEW" in result["override_note"]


# --------------------------------------------------------------------------
# High-risk funnel
# --------------------------------------------------------------------------
def test_funnel_stages_are_in_order(db_session: Session, decided: int) -> None:
    funnel = monitoring.high_risk_funnel(db_session)
    stages = [stage["stage"] for stage in funnel["stages"]]

    assert stages == [
        "HIGH_FRAUD_SCORE",
        "DECIDED",
        "INVESTIGATION_AVAILABLE",
        "SUFFICIENT_CORROBORATION",
        "BLOCK_ELIGIBLE",
        "FINAL_DECISION_BLOCK",
    ]


def test_funnel_top_stage_matches_direct_sql(db_session: Session, decided: int) -> None:
    from decimal import Decimal

    policy = load_policy()
    funnel = monitoring.high_risk_funnel(db_session)
    expected = db_session.scalar(
        select(func.count(RiskPrediction.id)).where(
            RiskPrediction.fraud_probability >= Decimal(str(policy.thresholds.fraud_block))
        )
    )

    assert funnel["stages"][0]["count"] == expected


def test_funnel_narrows_monotonically(db_session: Session, decided: int) -> None:
    """Each stage is a filter, so no stage can exceed the one above it."""
    counts = [stage["count"] for stage in monitoring.high_risk_funnel(db_session)["stages"]]
    assert counts == sorted(counts, reverse=True)


def test_funnel_explains_the_gap(db_session: Session, decided: int) -> None:
    funnel = monitoring.high_risk_funnel(db_session)

    assert "corroboration" in funnel["explanation"].lower()
    assert funnel["min_independent_sources"] >= 1
    assert funnel["block_threshold"] == pytest.approx(load_policy().thresholds.fraud_block)


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------
def test_recommendations_are_analytical_only(db_session: Session, decided: int) -> None:
    """Nothing may be executed, and each item must name its evidence."""
    items = monitoring.recommendations(db_session)

    for item in items:
        assert item["metric_source"].startswith("/api/")
        assert item["action_required"] in {
            "human_review",
            "collect_more_feedback",
            "investigate",
        }


def test_recommendations_flag_insufficient_labels(db_session: Session, decided: int) -> None:
    items = monitoring.recommendations(db_session)
    assert any(item["id"] == "insufficient-labels" for item in items)


def test_recommendations_do_not_change_anything(db_session: Session, decided: int) -> None:
    before = (
        db_session.scalar(select(func.count(RiskDecision.id))),
        db_session.scalar(select(func.count(AnalystFeedback.id))),
    )
    monitoring.recommendations(db_session)
    after = (
        db_session.scalar(select(func.count(RiskDecision.id))),
        db_session.scalar(select(func.count(AnalystFeedback.id))),
    )

    assert before == after


# --------------------------------------------------------------------------
# Assistant
# --------------------------------------------------------------------------
def test_assistant_answers_every_declared_question(db_session: Session, decided: int) -> None:
    for topic in assistant_service.QuestionTopic:
        answer = assistant_service.answer(db_session, topic)
        assert answer.answer
        assert answer.metric_sources
        assert answer.time_window
        assert answer.data_availability


def test_assistant_says_when_data_is_insufficient(db_session: Session, decided: int) -> None:
    answer = assistant_service.answer(db_session, assistant_service.QuestionTopic.MODEL_PERFORMANCE)

    assert answer.sufficient is False
    assert "insufficient" in answer.answer.lower()


def test_assistant_grounds_the_funnel_answer_in_measured_counts(
    db_session: Session, decided: int
) -> None:
    funnel = monitoring.high_risk_funnel(db_session)
    answer = assistant_service.answer(
        db_session, assistant_service.QuestionTopic.HIGH_RISK_NOT_BLOCKED
    )

    top = funnel["stages"][0]["count"]
    blocked = funnel["stages"][-1]["count"]
    assert f"{top:,}" in answer.answer
    assert f"{blocked:,}" in answer.answer


def test_assistant_reports_confirmed_fraud_with_coverage(db_session: Session, decided: int) -> None:
    _label(db_session, NORMAL, FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.CONFIRMED_FRAUD)
    answer = assistant_service.answer(
        db_session, assistant_service.QuestionTopic.CONFIRMED_FRAUD_COUNT
    )

    assert "1 transaction confirmed as fraud" in answer.answer
    assert "unlabelled, not legitimate" in answer.answer


def test_assistant_never_claims_drift_means_fraud(db_session: Session, decided: int) -> None:
    answer = assistant_service.answer(db_session, assistant_service.QuestionTopic.MODEL_DRIFT)
    assert "not evidence of fraud" in answer.answer


# --------------------------------------------------------------------------
# The API surface
# --------------------------------------------------------------------------
def test_monitoring_endpoints_respond(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    for path in (
        "/api/monitoring/models",
        "/api/monitoring/scores",
        "/api/monitoring/drift",
        "/api/monitoring/policy",
        "/api/monitoring/high-risk-funnel",
        "/api/monitoring/recommendations",
        "/api/assistant/questions",
    ):
        assert client.get(path).status_code == 200, path


def test_assistant_answer_endpoint(client: TestClient, db_session: Session, decided: int) -> None:
    db_session.commit()
    body = client.get("/api/assistant/answer?topic=high_risk_not_blocked").json()

    assert body["metric_sources"]
    assert body["time_window"]
    assert "data_availability" in body


def test_assistant_rejects_an_unknown_topic(client: TestClient) -> None:
    assert client.get("/api/assistant/answer?topic=whatever").status_code == 422


@pytest.mark.parametrize("query", ["baseline_days=0", "baseline_days=10000", "current_days=-1"])
def test_monitoring_window_parameters_are_bounded(client: TestClient, query: str) -> None:
    assert client.get(f"/api/monitoring/drift?{query}").status_code == 422


def test_monitoring_endpoints_are_read_only(client: TestClient) -> None:
    for path in ("/api/monitoring/models", "/api/monitoring/drift", "/api/monitoring/policy"):
        assert client.post(path, json={}).status_code == 405, path


def test_monitoring_responses_leak_nothing_sensitive(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    for path in (
        "/api/monitoring/models",
        "/api/monitoring/drift",
        "/api/monitoring/policy",
        "/api/monitoring/high-risk-funnel",
        "/api/monitoring/recommendations",
    ):
        raw = client.get(path).text.lower()
        for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
            assert secret not in raw, path


def test_metrics_endpoint_reports_insufficiency_honestly(
    client: TestClient, db_session: Session, decided: int
) -> None:
    db_session.commit()
    body = client.get("/api/monitoring/models").json()

    assert body["metrics"]["sufficient"] is False
    assert "Insufficient labeled data" in body["metrics"]["message"]
    assert body["metrics"]["precision"] is None


def test_drift_endpoint_accepts_a_bounded_window(client: TestClient, db_session: Session) -> None:
    db_session.commit()
    body = client.get("/api/monitoring/drift?baseline_days=30&current_days=15").json()
    assert len(body["features"]) == 6


def test_window_bounds_are_computed_from_data_not_wall_clock(db_session: Session) -> None:
    """A dataset that ends in the past must still produce a populated window."""
    latest = db_session.scalar(select(func.max(Transaction.transaction_timestamp)))
    assert latest is not None

    report = monitoring.drift_report(db_session, baseline_days=30, current_days=30)
    assert report["current_to"] == latest
    assert any(feature["current_count"] > 0 for feature in report["features"])


def test_future_window_yields_no_data(db_session: Session) -> None:
    future = datetime.now(UTC) + timedelta(days=3650)
    report = monitoring.drift_report(db_session, now=future, baseline_days=1, current_days=1)

    for feature in report["features"]:
        assert feature["current_count"] == 0
        assert feature["status"] == str(DriftStatus.INSUFFICIENT_DATA)
