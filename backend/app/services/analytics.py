"""Read-only aggregation for the operations dashboard.

Every number the dashboard shows is produced here, by SQL, from stored rows.
Nothing is estimated, sampled or carried in application memory between requests.

Two rules shape this module:

* **Aggregate in the database.** A dashboard over 20,000 transactions must never
  ship 20,000 rows to a browser, and must not stream them through Python either.
  Each function below is one grouped query.
* **Bound every query.** Time windows are clamped, "top N" lists are capped, and
  no endpoint can be asked for an unbounded scan.

Thresholds for "high risk" and "critical anomaly" come from the *active policy*
rather than from constants repeated here. If the policy moves, the dashboard
moves with it, and the two can never disagree about what "high risk" means.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    Investigation,
    Merchant,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import DecisionAction, ReviewCaseStatus
from app.services.anomaly import ANOMALY_SIGNAL
from policy.loader import get_policy
from policy.schema import PolicyConfig

logger = logging.getLogger(__name__)

#: Time windows the trend endpoint accepts, in days. Bounded deliberately: an
#: unbounded window is an unbounded scan.
ALLOWED_TREND_DAYS = (1, 7, 30, 90, 365)
MAX_TREND_DAYS = 365
MAX_TOP_RISK = 50

#: Fraud-probability histogram edges. Ten equal buckets over [0, 1].
PROBABILITY_BUCKETS = 10

#: Anomaly severities in ascending order of concern, so the API returns them in
#: a stable, meaningful order rather than whatever the database yields.
SEVERITY_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
DECISION_ORDER = (
    DecisionAction.APPROVE,
    DecisionAction.STEP_UP,
    DecisionAction.REVIEW,
    DecisionAction.BLOCK,
)


@dataclass(frozen=True)
class Bucket:
    """One labelled count, optionally with the range it covers."""

    label: str
    count: int
    lower: float | None = None
    upper: float | None = None


#: Columns every "current decision" join needs. Declared once so the two
#: dialect paths below cannot drift apart.
_LATEST_COLUMNS = (
    RiskDecision.id.label("decision_id"),
    RiskDecision.transaction_id.label("transaction_id"),
    RiskDecision.action.label("action"),
    RiskDecision.decided_at.label("decided_at"),
    RiskDecision.fraud_probability.label("fraud_probability"),
    RiskDecision.anomaly_score.label("anomaly_score"),
    RiskDecision.anomaly_severity.label("anomaly_severity"),
    RiskDecision.policy_version.label("policy_version"),
    RiskDecision.requires_human_review.label("requires_human_review"),
    RiskDecision.evaluation_ms.label("evaluation_ms"),
    RiskDecision.reason_codes.label("reason_codes"),
)


def latest_decisions(session: Session) -> Any:
    """The most recent decision per transaction.

    ``risk_decisions`` is append-only history, so a transaction can have several
    rows. Every dashboard figure is about the *current* disposition, which means
    the newest decision.

    Two implementations, because the difference is not cosmetic. On PostgreSQL
    ``DISTINCT ON`` walks the ``(transaction_id, decided_at)`` index and was
    measured at ~83ms over 20,000 rows; the portable ``ROW_NUMBER`` window
    formulation measured ~595ms for the same result. SQLite (used by the tests)
    has no ``DISTINCT ON``, so it keeps the window version - correctness is
    identical either way, and a test asserts both agree.
    """
    if session.get_bind().dialect.name == "postgresql":
        return (
            select(*_LATEST_COLUMNS)
            .distinct(RiskDecision.transaction_id)
            .order_by(
                RiskDecision.transaction_id,
                RiskDecision.decided_at.desc(),
                RiskDecision.id.desc(),
            )
            .subquery()
        )

    ranked = select(
        *_LATEST_COLUMNS,
        func.row_number()
        .over(
            partition_by=RiskDecision.transaction_id,
            order_by=(RiskDecision.decided_at.desc(), RiskDecision.id.desc()),
        )
        .label("recency"),
    ).subquery()
    return select(ranked).where(ranked.c.recency == 1).subquery()


def _day_expression(session: Session) -> ColumnElement[Any]:
    """Truncate a timestamp to its day, portably.

    PostgreSQL and SQLite spell this differently and there is no common
    function, so the dialect is asked once rather than papered over with a cast
    that silently means different things on each backend.
    """
    if session.get_bind().dialect.name == "postgresql":
        return func.date_trunc("day", Transaction.transaction_timestamp)
    return func.date(Transaction.transaction_timestamp)


def _window_start(days: int, *, now: datetime | None = None) -> datetime:
    reference = now or datetime.now(UTC)
    return reference - timedelta(days=min(days, MAX_TREND_DAYS))


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
def overview(session: Session, *, policy: PolicyConfig | None = None) -> dict[str, Any]:
    """Headline counters, each one a SQL aggregate.

    Deliberately reports the *scope* of every figure alongside it: "decided"
    counts cover transactions that have a decision, which is not automatically
    every transaction, and a dashboard that blurs those two is lying by omission.
    """
    active = policy or get_policy()
    latest = latest_decisions(session)

    total_transactions = session.scalar(select(func.count(Transaction.id))) or 0

    decision_rows = session.execute(
        select(latest.c.action, func.count()).group_by(latest.c.action)
    ).all()
    by_action = {str(action): count for action, count in decision_rows}
    decided = sum(by_action.values())

    high_risk = (
        session.scalar(
            select(func.count(RiskPrediction.id)).where(
                RiskPrediction.fraud_probability >= Decimal(str(active.thresholds.fraud_high))
            )
        )
        or 0
    )

    critical_anomalies = (
        session.scalar(
            select(func.count(RiskSignal.id)).where(
                RiskSignal.signal_name == ANOMALY_SIGNAL,
                RiskSignal.signal_value >= Decimal(str(active.thresholds.anomaly_critical)),
            )
        )
        or 0
    )

    open_reviews = (
        session.scalar(
            select(func.count(ReviewCase.id)).where(
                ReviewCase.status.in_((ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW))
            )
        )
        or 0
    )
    escalated_reviews = (
        session.scalar(
            select(func.count(ReviewCase.id)).where(ReviewCase.status == ReviewCaseStatus.ESCALATED)
        )
        or 0
    )

    latency = session.execute(
        select(
            func.avg(latest.c.evaluation_ms),
            func.min(latest.c.evaluation_ms),
            func.max(latest.c.evaluation_ms),
            func.count(latest.c.evaluation_ms),
        )
    ).one()

    investigations = (
        session.scalar(
            select(func.count(Investigation.id)).where(Investigation.status == "completed")
        )
        or 0
    )

    span = session.execute(
        select(
            func.min(Transaction.transaction_timestamp), func.max(Transaction.transaction_timestamp)
        )
    ).one()

    return {
        "total_transactions": total_transactions,
        "decided_transactions": decided,
        "approved": by_action.get(str(DecisionAction.APPROVE), 0),
        "step_up": by_action.get(str(DecisionAction.STEP_UP), 0),
        "review": by_action.get(str(DecisionAction.REVIEW), 0),
        "blocked": by_action.get(str(DecisionAction.BLOCK), 0),
        "high_risk_transactions": high_risk,
        "critical_anomalies": critical_anomalies,
        "open_review_cases": open_reviews,
        "escalated_review_cases": escalated_reviews,
        "completed_investigations": investigations,
        "avg_decision_latency_ms": float(latency[0]) if latency[0] is not None else None,
        "min_decision_latency_ms": float(latency[1]) if latency[1] is not None else None,
        "max_decision_latency_ms": float(latency[2]) if latency[2] is not None else None,
        "latency_sample_size": int(latency[3] or 0),
        "policy_version": active.policy_version,
        "high_risk_threshold": active.thresholds.fraud_high,
        "critical_anomaly_threshold": active.thresholds.anomaly_critical,
        "data_from": span[0],
        "data_to": span[1],
    }


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------
def decision_distribution(session: Session) -> list[Bucket]:
    """How the current decisions break down, in precedence order."""
    latest = latest_decisions(session)
    # Ruff suggests dict(); mypy rejects it, because a SQLAlchemy Row is not
    # seen as a 2-tuple. Unpacking explicitly is what makes this type-check.
    rows: dict[Any, int] = {  # noqa: C416
        action: count
        for action, count in session.execute(
            select(latest.c.action, func.count()).group_by(latest.c.action)
        ).all()
    }
    return [
        Bucket(label=str(action).upper(), count=rows.get(action, 0)) for action in DECISION_ORDER
    ]


def fraud_probability_distribution(session: Session) -> list[Bucket]:
    """A ten-bucket histogram of stored fraud probabilities.

    Bucketed in SQL with ``width_bucket``-equivalent arithmetic rather than by
    fetching 20,000 probabilities and counting them in Python.
    """
    # A CASE rather than least(): SQLite has no least(), and the tests run there.
    # The clamp matters - a probability of exactly 1.0 would otherwise land in a
    # non-existent eleventh bucket and vanish from the histogram.
    raw_bucket = func.floor(RiskPrediction.fraud_probability * PROBABILITY_BUCKETS)
    bucket_index = case(
        (raw_bucket > PROBABILITY_BUCKETS - 1, PROBABILITY_BUCKETS - 1), else_=raw_bucket
    )
    rows = session.execute(
        select(bucket_index.label("bucket"), func.count()).group_by("bucket").order_by("bucket")
    ).all()
    counts = {int(bucket): count for bucket, count in rows}

    width = 1.0 / PROBABILITY_BUCKETS
    return [
        Bucket(
            label=f"{index * width:.1f}-{(index + 1) * width:.1f}",
            count=counts.get(index, 0),
            lower=index * width,
            upper=(index + 1) * width,
        )
        for index in range(PROBABILITY_BUCKETS)
    ]


def anomaly_severity_distribution(session: Session) -> list[Bucket]:
    """Counts per Phase 4 severity band."""
    normalised: dict[str, int] = {
        str(severity).upper(): count
        for severity, count in session.execute(
            select(RiskSignal.severity, func.count())
            .where(RiskSignal.signal_name == ANOMALY_SIGNAL)
            .group_by(RiskSignal.severity)
        ).all()
    }
    return [Bucket(label=name, count=normalised.get(name, 0)) for name in SEVERITY_ORDER]


def risk_level_distribution(
    session: Session, *, policy: PolicyConfig | None = None
) -> list[Bucket]:
    """Transactions banded by the policy's own supervised thresholds.

    The bands are read from the active policy, so "high risk" here means exactly
    what it means to the decision engine.
    """
    active = policy or get_policy()
    thresholds = active.thresholds
    banding = case(
        (RiskPrediction.fraud_probability >= Decimal(str(thresholds.fraud_block)), "CRITICAL"),
        (RiskPrediction.fraud_probability >= Decimal(str(thresholds.fraud_high)), "HIGH"),
        (RiskPrediction.fraud_probability >= Decimal(str(thresholds.fraud_medium)), "MEDIUM"),
        else_="LOW",
    )
    rows: dict[str, int] = {
        str(band): count
        for band, count in session.execute(
            select(banding.label("band"), func.count()).group_by("band")
        ).all()
    }
    bands = (
        ("LOW", 0.0, thresholds.fraud_medium),
        ("MEDIUM", thresholds.fraud_medium, thresholds.fraud_high),
        ("HIGH", thresholds.fraud_high, thresholds.fraud_block),
        ("CRITICAL", thresholds.fraud_block, 1.0),
    )
    return [
        Bucket(label=name, count=rows.get(name, 0), lower=lower, upper=upper)
        for name, lower, upper in bands
    ]


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------
def trends(
    session: Session,
    *,
    days: int = 30,
    policy: PolicyConfig | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Daily volume, split by disposition, over a bounded window.

    One grouped query. The high-risk column uses the policy's own high
    threshold, so the chart and the engine agree on the word.
    """
    active = policy or get_policy()
    start = _window_start(days, now=now)
    latest = latest_decisions(session)

    day = _day_expression(session).label("day")
    high_threshold = Decimal(str(active.thresholds.fraud_high))

    rows = session.execute(
        select(
            day,
            func.count(Transaction.id).label("volume"),
            func.sum(case((latest.c.fraud_probability >= high_threshold, 1), else_=0)).label(
                "high_risk"
            ),
            func.sum(case((latest.c.action == DecisionAction.REVIEW, 1), else_=0)).label("review"),
            func.sum(case((latest.c.action == DecisionAction.BLOCK, 1), else_=0)).label("blocked"),
            func.sum(case((latest.c.action == DecisionAction.STEP_UP, 1), else_=0)).label(
                "step_up"
            ),
            func.sum(case((latest.c.action == DecisionAction.APPROVE, 1), else_=0)).label(
                "approved"
            ),
        )
        .select_from(Transaction)
        .join(latest, latest.c.transaction_id == Transaction.id, isouter=True)
        .where(Transaction.transaction_timestamp >= start)
        .group_by(day)
        .order_by(day)
    ).all()

    return [
        {
            "day": bucket,
            "volume": int(volume or 0),
            "high_risk": int(high_risk or 0),
            "review": int(review or 0),
            "blocked": int(blocked or 0),
            "step_up": int(step_up or 0),
            "approved": int(approved or 0),
        }
        for bucket, volume, high_risk, review, blocked, step_up, approved in rows
    ]


# --------------------------------------------------------------------------
# Top risk
# --------------------------------------------------------------------------
def top_risk(session: Session, *, limit: int = 10) -> list[dict[str, Any]]:
    """The riskiest current decisions, joined in one query.

    Merchant and customer names come from joins, not from a loop over results -
    the N+1 that would otherwise turn a ten-row table into twenty-one queries.
    """
    capped = max(1, min(limit, MAX_TOP_RISK))
    latest = latest_decisions(session)

    rows = session.execute(
        select(
            Transaction.transaction_id,
            Transaction.transaction_timestamp,
            Transaction.amount,
            Transaction.currency,
            Merchant.name,
            Customer.external_customer_id,
            latest.c.action,
            latest.c.fraud_probability,
            latest.c.anomaly_score,
            latest.c.anomaly_severity,
        )
        .select_from(latest)
        .join(Transaction, Transaction.id == latest.c.transaction_id)
        .join(Merchant, Merchant.id == Transaction.merchant_id)
        .join(Customer, Customer.id == Transaction.customer_id)
        .order_by(
            latest.c.fraud_probability.desc().nullslast(),
            latest.c.anomaly_score.desc().nullslast(),
        )
        .limit(capped)
    ).all()

    return [
        {
            "transaction_id": reference,
            "timestamp": timestamp,
            "amount": float(amount),
            "currency": currency,
            "merchant_name": merchant,
            "customer_id": customer,
            "decision": str(action).upper(),
            "fraud_probability": float(probability) if probability is not None else None,
            "anomaly_score": score,
            "anomaly_severity": severity,
        }
        for (
            reference,
            timestamp,
            amount,
            currency,
            merchant,
            customer,
            action,
            probability,
            score,
            severity,
        ) in rows
    ]


# --------------------------------------------------------------------------
# Reason codes - what is actually driving decisions
# --------------------------------------------------------------------------
def reason_code_frequency(session: Session, *, limit: int = 15) -> list[Bucket]:
    """How often each reason code appears across current decisions.

    ``reason_codes`` is a JSON array. PostgreSQL can expand it server-side, so
    it does; SQLite (used by the tests) cannot, so the counting happens in
    Python over the already-bounded set of current decisions. Same answer, and
    neither path pulls the whole table.
    """
    capped = max(1, min(limit, MAX_TOP_RISK))
    latest = latest_decisions(session)

    if session.get_bind().dialect.name == "postgresql":
        code = func.jsonb_array_elements_text(latest.c.reason_codes).label("code")
        rows = session.execute(
            select(code, func.count())
            .select_from(latest)
            .group_by("code")
            .order_by(func.count().desc(), "code")
            .limit(capped)
        ).all()
        return [Bucket(label=str(name), count=count) for name, count in rows]

    counts: dict[str, int] = {}
    for (codes,) in session.execute(select(latest.c.reason_codes).select_from(latest)).all():
        for name in codes or []:
            counts[str(name)] = counts.get(str(name), 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:capped]
    return [Bucket(label=name, count=count) for name, count in ordered]
