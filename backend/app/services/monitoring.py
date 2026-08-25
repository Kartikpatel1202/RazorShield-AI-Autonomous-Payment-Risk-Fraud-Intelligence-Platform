"""Measuring where the risk system is succeeding and where it is failing.

Everything here is read-only and analytical. Nothing in this module retrains a
model, moves a threshold, edits a policy or touches a decision. It measures, and
it reports what it cannot measure.

The discipline that matters most:

* **Unlabelled is not negative.** Precision and recall are computed only over
  transactions an analyst actually labelled. Treating the ~20,000 unreviewed
  transactions as legitimate examples would produce a flattering accuracy that
  describes nothing.
* **Small samples are reported as small.** Below a configured floor the metric
  is withheld and the response says "insufficient labeled data" rather than
  publishing a rate that one more label would swing by ten points.
* **Drift is distribution change, not fraud.** A DRIFT_DETECTED status says
  behaviour moved. It carries no claim about why.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ARRAY, Float, Select, case, func, select, type_coerce
from sqlalchemy.orm import Session

from app.models import (
    AnalystFeedback,
    Device,
    Investigation,
    Merchant,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import DecisionAction, DriftStatus, FeedbackOutcome, ReviewResolution
from app.services.analytics import latest_decisions
from app.services.anomaly import ANOMALY_SIGNAL
from app.services.monitoring_config import MonitoringConfig, get_monitoring_config
from app.services.review import is_override as review_is_override
from policy.loader import get_policy
from policy.rules import RULE_PRIMARY_ACTION, rule_description
from policy.schema import KNOWN_RULE_IDS, PolicyConfig

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Model performance against analyst labels
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LabelledSample:
    """One labelled transaction: what the machine said, what was true."""

    machine_flagged: bool
    actually_fraud: bool


def _labelled_samples(session: Session) -> list[LabelledSample]:
    """Every transaction carrying a ground-truth analyst label.

    One query. The join is on ``risk_decision_id``, so a label without a
    decision - possible, if a transaction was labelled before being decided -
    is excluded rather than silently counted against a decision that never
    happened.
    """
    rows = session.execute(
        select(RiskDecision.action, AnalystFeedback.outcome)
        .select_from(AnalystFeedback)
        .join(RiskDecision, RiskDecision.id == AnalystFeedback.risk_decision_id)
    ).all()

    samples: list[LabelledSample] = []
    for action, outcome in rows:
        parsed = FeedbackOutcome(str(outcome))
        if not parsed.is_ground_truth:
            continue
        samples.append(
            LabelledSample(
                # Anything other than a clean approval is the machine expressing
                # suspicion, which is the "positive" call being scored.
                machine_flagged=action != DecisionAction.APPROVE,
                actually_fraud=parsed.indicates_fraud,
            )
        )
    return samples


def model_metrics(session: Session, *, config: MonitoringConfig | None = None) -> dict[str, Any]:
    """Precision, recall, F1 and error rates over analyst-labelled data only.

    Returns ``sufficient: False`` and no metrics when the labelled sample is
    below the configured floor. That is not a placeholder - it is the honest
    answer, and the caller is expected to render it as such.
    """
    active = config or get_monitoring_config()
    samples = _labelled_samples(session)

    total_transactions = session.scalar(select(func.count(Transaction.id))) or 0
    total_feedback = session.scalar(select(func.count(AnalystFeedback.id))) or 0
    open_outcomes = total_feedback - len(samples)

    base = {
        "labelled_samples": len(samples),
        "total_feedback": total_feedback,
        "open_outcome_labels": max(open_outcomes, 0),
        "unlabelled_transactions": max(total_transactions - total_feedback, 0),
        "total_transactions": total_transactions,
        "minimum_required": active.metrics.min_labeled_samples,
        "label_source": "analyst_feedback",
    }

    if len(samples) < active.metrics.min_labeled_samples:
        return {
            **base,
            "sufficient": False,
            "message": (
                f"Insufficient labeled data. {len(samples)} ground-truth label"
                f"{'' if len(samples) == 1 else 's'} available; "
                f"{active.metrics.min_labeled_samples} required before precision and recall "
                "are meaningful."
            ),
            "selection_bias_note": None,
            "labelled_flagged": None,
            "labelled_unflagged": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "false_positive_rate": None,
            "false_negative_rate": None,
            "true_positive": None,
            "false_positive": None,
            "true_negative": None,
            "false_negative": None,
        }

    true_positive = sum(1 for s in samples if s.machine_flagged and s.actually_fraud)
    false_positive = sum(1 for s in samples if s.machine_flagged and not s.actually_fraud)
    true_negative = sum(1 for s in samples if not s.machine_flagged and not s.actually_fraud)
    false_negative = sum(1 for s in samples if not s.machine_flagged and s.actually_fraud)

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = (
        (2 * precision * recall / (precision + recall))
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )

    # Selection bias check. Labels overwhelmingly come from the review queue,
    # which by construction contains only flagged transactions. A sample with no
    # un-flagged examples yields recall = 1.0 and FPR = 1.0 by arithmetic, not by
    # merit, and publishing those without a warning would be actively misleading.
    unflagged = true_negative + false_negative
    flagged = true_positive + false_positive
    bias_note: str | None = None
    if unflagged == 0:
        bias_note = (
            "Every labelled transaction was one the system flagged, because labels come "
            "from the review queue. With no un-flagged examples, recall and the "
            "false-negative rate are 1.0 and 0.0 by construction rather than by "
            "measurement. Label a sample of approved transactions to measure them."
        )
    elif flagged == 0:
        bias_note = (
            "Every labelled transaction was one the system approved. Precision is not "
            "measurable from this sample."
        )
    elif min(unflagged, flagged) < active.metrics.min_labeled_samples / 4:
        bias_note = (
            f"The labelled sample is unbalanced: {flagged} flagged and {unflagged} "
            "un-flagged. The metric computed from the smaller group is fragile."
        )

    return {
        **base,
        "sufficient": True,
        "message": None,
        "selection_bias_note": bias_note,
        "labelled_flagged": flagged,
        "labelled_unflagged": unflagged,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": _ratio(false_positive, false_positive + true_negative),
        "false_negative_rate": _ratio(false_negative, false_negative + true_positive),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    """A rate, or None when the denominator is empty.

    Zero would be a claim; None is the absence of one.
    """
    return (numerator / denominator) if denominator else None


def label_coverage(session: Session) -> dict[str, Any]:
    """How much of the dataset carries which kind of label.

    The three categories are kept apart deliberately. The synthetic ``is_fraud``
    column is the dataset's own generation-time label - useful for reference,
    but it is not analyst-confirmed and must never be presented as if a human
    verified it.
    """
    total = session.scalar(select(func.count(Transaction.id))) or 0
    analyst_total = session.scalar(select(func.count(AnalystFeedback.id))) or 0
    ground_truth = 0
    for outcome, count in session.execute(
        select(AnalystFeedback.outcome, func.count()).group_by(AnalystFeedback.outcome)
    ).all():
        if FeedbackOutcome(str(outcome)).is_ground_truth:
            ground_truth += count

    simulated_fraud = (
        session.scalar(select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True)))
        or 0
    )

    return {
        "total_transactions": total,
        "confirmed_labels": ground_truth,
        "analyst_feedback_total": analyst_total,
        "open_outcome_labels": max(analyst_total - ground_truth, 0),
        "unlabelled": max(total - analyst_total, 0),
        "simulated_fraud_flags": simulated_fraud,
        "simulated_label_note": (
            "The dataset's is_fraud column is a generation-time property of the "
            "simulation, not an analyst confirmation. It is reported for reference "
            "and is never used as ground truth in the metrics above."
        ),
    }


# --------------------------------------------------------------------------
# Score distributions and drift
# --------------------------------------------------------------------------
def _window_bounds(
    days: int, *, offset_days: int = 0, now: datetime | None = None
) -> tuple[datetime, datetime]:
    end = (now or datetime.now(UTC)) - timedelta(days=offset_days)
    return end - timedelta(days=days), end


def population_stability_index(
    baseline: list[float], current: list[float], bins: int
) -> float | None:
    """PSI between two numeric samples, binned on baseline quantiles.

    Quantiles of the *baseline* define the bins, which is the point: PSI asks
    how the new population redistributes itself across the old one's shape.

    Empty bins are floored at a small epsilon rather than dropped. A category
    that vanished entirely is exactly the drift worth detecting, and ``ln(0)``
    would discard it.
    """
    if not baseline or not current:
        return None

    ordered = sorted(baseline)
    edges = [ordered[min(int(i * len(ordered) / bins), len(ordered) - 1)] for i in range(1, bins)]
    edges = sorted(set(edges))
    if not edges:
        return 0.0

    def shares(sample: list[float]) -> list[float]:
        counts = [0] * (len(edges) + 1)
        for value in sample:
            index = 0
            while index < len(edges) and value > edges[index]:
                index += 1
            counts[index] += 1
        size = len(sample)
        return [count / size for count in counts]

    epsilon = 1e-6
    baseline_shares = shares(ordered)
    current_shares = shares(current)

    psi = 0.0
    for expected, actual in zip(baseline_shares, current_shares, strict=True):
        expected = max(expected, epsilon)
        actual = max(actual, epsilon)
        psi += (actual - expected) * math.log(actual / expected)
    return psi


def categorical_psi(baseline: dict[str, int], current: dict[str, int]) -> float | None:
    """PSI over category shares, so one banding covers numeric and categorical."""
    baseline_total = sum(baseline.values())
    current_total = sum(current.values())
    if not baseline_total or not current_total:
        return None

    epsilon = 1e-6
    psi = 0.0
    for key in set(baseline) | set(current):
        expected = max(baseline.get(key, 0) / baseline_total, epsilon)
        actual = max(current.get(key, 0) / current_total, epsilon)
        psi += (actual - expected) * math.log(actual / expected)
    return psi


def classify_drift(psi: float | None, sample_size: int, config: MonitoringConfig) -> DriftStatus:
    """Band a PSI value, refusing to band one computed on too little data."""
    if psi is None or sample_size < config.drift.min_samples:
        return DriftStatus.INSUFFICIENT_DATA
    if psi >= config.drift.psi_drift:
        return DriftStatus.DRIFT_DETECTED
    if psi >= config.drift.psi_watch:
        return DriftStatus.WATCH
    return DriftStatus.NORMAL


def _numeric_window(
    session: Session, column: Any, join: Any, start: datetime, end: datetime
) -> list[float]:
    rows = session.execute(
        select(column)
        .select_from(Transaction)
        .join(join, join.transaction_id == Transaction.id)
        .where(Transaction.transaction_timestamp >= start)
        .where(Transaction.transaction_timestamp < end)
    ).all()
    return [float(value) for (value,) in rows if value is not None]


def _numeric_source(column: Any, join: Any) -> Select[Any]:
    """A SELECT of one numeric column, joined to transactions for its timestamp."""
    query = select(column.label("value"), Transaction.transaction_timestamp).select_from(
        Transaction
    )
    if join is RiskSignal:
        query = query.join(RiskSignal, RiskSignal.transaction_id == Transaction.id).where(
            RiskSignal.signal_name == ANOMALY_SIGNAL
        )
    elif join is not None:
        query = query.join(join, join.transaction_id == Transaction.id)
    return query


def _numeric_drift(
    session: Session,
    *,
    name: str,
    column: Any,
    join: Any,
    baseline: tuple[datetime, datetime],
    current: tuple[datetime, datetime],
    config: MonitoringConfig,
) -> dict[str, Any]:
    """PSI for one numeric feature, binned without shipping the rows to Python.

    On PostgreSQL the baseline quantile edges come from a single
    ``percentile_cont`` call and the per-bin counts from one grouped query, so a
    feature costs four small queries instead of two full column scans. Streaming
    40,000 floats per feature was measured at ~600ms for the drift endpoint; this
    removes that cost.

    SQLite has no ``percentile_cont``, so the tests keep the in-Python path. Both
    compute the same PSI over the same bins - a test asserts they agree.
    """
    source = _numeric_source(column, join)
    bins = config.drift.bins

    def window(bounds: tuple[datetime, datetime]) -> Select[Any]:
        return source.where(Transaction.transaction_timestamp >= bounds[0]).where(
            Transaction.transaction_timestamp < bounds[1]
        )

    if session.get_bind().dialect.name != "postgresql":
        baseline_values = [
            float(value)
            for (value, _) in session.execute(window(baseline)).all()
            if value is not None
        ]
        current_values = [
            float(value)
            for (value, _) in session.execute(window(current)).all()
            if value is not None
        ]
        psi = population_stability_index(baseline_values, current_values, bins)
        sample = min(len(baseline_values), len(current_values))
        return {
            "feature": name,
            "kind": "numeric",
            "psi": psi,
            "status": str(classify_drift(psi, sample, config)),
            "baseline_count": len(baseline_values),
            "current_count": len(current_values),
            "baseline_mean": _mean(baseline_values),
            "current_mean": _mean(current_values),
        }

    baseline_sub = window(baseline).subquery()
    current_sub = window(current).subquery()

    fractions = [index / bins for index in range(1, bins)]
    # `percentile_cont(<array>)` returns an array, but SQLAlchemy types a
    # `within_group` expression from its *ordering* column - Numeric here - and
    # then tries to run the Decimal result processor over a list. `type_coerce`
    # corrects the Python-side type only; the SQL and the value the database
    # computes are unchanged.
    quantiles = type_coerce(
        func.percentile_cont(fractions).within_group(baseline_sub.c.value.asc()),
        ARRAY(Float),
    )
    edges_row = session.execute(
        select(
            quantiles,
            func.count(baseline_sub.c.value),
            func.avg(baseline_sub.c.value),
        )
    ).one()
    raw_edges, baseline_count, baseline_mean = edges_row
    edges = sorted({float(edge) for edge in (raw_edges or []) if edge is not None})

    current_stats = session.execute(
        select(func.count(current_sub.c.value), func.avg(current_sub.c.value))
    ).one()
    current_count, current_mean = current_stats

    def bucket_counts(sub: Any) -> list[int]:
        if not edges:
            total = session.scalar(select(func.count(sub.c.value))) or 0
            return [int(total)]
        # One CASE expression assigns each row its bin; the database groups.
        whens = [(sub.c.value <= edge, index) for index, edge in enumerate(edges)]
        bucket = case(*whens, else_=len(edges)).label("bucket")
        rows = session.execute(
            select(bucket, func.count()).select_from(sub).group_by("bucket")
        ).all()
        counts = [0] * (len(edges) + 1)
        for index, count in rows:
            counts[int(index)] = int(count)
        return counts

    baseline_counts = bucket_counts(baseline_sub)
    current_counts = bucket_counts(current_sub)
    psi = _psi_from_counts(baseline_counts, current_counts)
    sample = min(int(baseline_count or 0), int(current_count or 0))

    return {
        "feature": name,
        "kind": "numeric",
        "psi": psi,
        "status": str(classify_drift(psi, sample, config)),
        "baseline_count": int(baseline_count or 0),
        "current_count": int(current_count or 0),
        "baseline_mean": float(baseline_mean) if baseline_mean is not None else None,
        "current_mean": float(current_mean) if current_mean is not None else None,
    }


def _psi_from_counts(baseline: list[int], current: list[int]) -> float | None:
    """PSI from two aligned bin-count vectors.

    Shared by the SQL and in-Python paths so both apply the same epsilon floor
    and produce the same number.
    """
    baseline_total = sum(baseline)
    current_total = sum(current)
    if not baseline_total or not current_total:
        return None

    epsilon = 1e-6
    psi = 0.0
    for expected_count, actual_count in zip(baseline, current, strict=True):
        expected = max(expected_count / baseline_total, epsilon)
        actual = max(actual_count / current_total, epsilon)
        psi += (actual - expected) * math.log(actual / expected)
    return psi


def drift_report(
    session: Session,
    *,
    baseline_days: int | None = None,
    current_days: int | None = None,
    now: datetime | None = None,
    config: MonitoringConfig | None = None,
) -> dict[str, Any]:
    """Compare a baseline window against a current one across six features.

    Windows are read from stored transaction timestamps - there is no invented
    baseline anywhere in this function.
    """
    active = config or get_monitoring_config()
    current_span = current_days or active.drift.current_days
    baseline_span = baseline_days or active.drift.baseline_days

    reference = now or session.scalar(select(func.max(Transaction.transaction_timestamp)))
    if reference is None:
        return {
            "features": [],
            "baseline_from": None,
            "baseline_to": None,
            "current_from": None,
            "current_to": None,
            "thresholds": active.as_dict(),
            "note": _DRIFT_NOTE,
        }

    current_start, current_end = _window_bounds(current_span, now=reference)
    baseline_start, baseline_end = _window_bounds(
        baseline_span, offset_days=current_span, now=reference
    )

    features: list[dict[str, Any]] = []

    # --- numeric ---------------------------------------------------------
    numeric_specs: tuple[tuple[str, Any, Any], ...] = (
        ("amount", Transaction.amount, None),
        ("fraud_probability", RiskPrediction.fraud_probability, RiskPrediction),
        ("anomaly_score", RiskSignal.signal_value, RiskSignal),
    )
    for name, column, join in numeric_specs:
        features.append(
            _numeric_drift(
                session,
                name=name,
                column=column,
                join=join,
                baseline=(baseline_start, baseline_end),
                current=(current_start, current_end),
                config=active,
            )
        )

    # --- categorical -----------------------------------------------------
    # Typed as Any: the columns are deliberately heterogeneous (str, an enum,
    # and a plain column), and the loop only ever groups by them.
    categorical_specs: tuple[tuple[str, Any, Any, Any], ...] = (
        ("merchant", Merchant.name, Merchant, Transaction.merchant_id),
        ("device", Device.device_type, Device, Transaction.device_id),
        ("location", Transaction.country, None, None),
    )
    for name, column, entity, foreign_key in categorical_specs:
        baseline_counts = _category_counts(
            session, column, entity, foreign_key, baseline_start, baseline_end
        )
        current_counts = _category_counts(
            session, column, entity, foreign_key, current_start, current_end
        )
        psi = categorical_psi(baseline_counts, current_counts)
        sample = min(sum(baseline_counts.values()), sum(current_counts.values()))
        features.append(
            {
                "feature": name,
                "kind": "categorical",
                "psi": psi,
                "status": str(classify_drift(psi, sample, active)),
                "baseline_count": sum(baseline_counts.values()),
                "current_count": sum(current_counts.values()),
                "baseline_mean": None,
                "current_mean": None,
            }
        )

    return {
        "features": features,
        "baseline_from": baseline_start,
        "baseline_to": baseline_end,
        "current_from": current_start,
        "current_to": current_end,
        "thresholds": active.as_dict(),
        "note": _DRIFT_NOTE,
    }


_DRIFT_NOTE = (
    "Drift means the distribution of a feature moved between the two windows. "
    "It is not evidence of fraud. A shift can equally reflect a new merchant, a "
    "campaign, a seasonal pattern or an upstream change."
)


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _anomaly_window(session: Session, start: datetime, end: datetime) -> list[float]:
    rows = session.execute(
        select(RiskSignal.signal_value)
        .select_from(Transaction)
        .join(RiskSignal, RiskSignal.transaction_id == Transaction.id)
        .where(RiskSignal.signal_name == ANOMALY_SIGNAL)
        .where(Transaction.transaction_timestamp >= start)
        .where(Transaction.transaction_timestamp < end)
    ).all()
    return [float(value) for (value,) in rows if value is not None]


def _category_counts(
    session: Session,
    column: Any,
    entity: Any,
    foreign_key: Any,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    query = select(column, func.count()).select_from(Transaction)
    if entity is not None:
        query = query.join(entity, entity.id == foreign_key)
    query = (
        query.where(Transaction.transaction_timestamp >= start)
        .where(Transaction.transaction_timestamp < end)
        .group_by(column)
    )
    return {str(key): count for key, count in session.execute(query).all() if key is not None}


def score_windows(
    session: Session,
    *,
    baseline_days: int | None = None,
    current_days: int | None = None,
    config: MonitoringConfig | None = None,
    policy: PolicyConfig | None = None,
) -> dict[str, Any]:
    """Fraud-probability and anomaly summaries for baseline vs current.

    High-risk and critical-anomaly percentages use the *policy's* thresholds, so
    the monitoring page and the decision engine agree on the words.
    """
    active = config or get_monitoring_config()
    thresholds = (policy or get_policy()).thresholds
    current_span = current_days or active.drift.current_days
    baseline_span = baseline_days or active.drift.baseline_days

    reference = session.scalar(select(func.max(Transaction.transaction_timestamp)))
    if reference is None:
        return {"baseline": None, "current": None, "thresholds": active.as_dict()}

    current_start, current_end = _window_bounds(current_span, now=reference)
    baseline_start, baseline_end = _window_bounds(
        baseline_span, offset_days=current_span, now=reference
    )

    return {
        "baseline": _window_summary(session, baseline_start, baseline_end, thresholds),
        "current": _window_summary(session, current_start, current_end, thresholds),
        "high_risk_threshold": thresholds.fraud_high,
        "critical_anomaly_threshold": thresholds.anomaly_critical,
        "thresholds": active.as_dict(),
    }


def _window_summary(
    session: Session, start: datetime, end: datetime, thresholds: Any
) -> dict[str, Any]:
    """One window's score summary. Two grouped queries, no row streaming."""
    high = Decimal(str(thresholds.fraud_high))
    critical = Decimal(str(thresholds.anomaly_critical))

    fraud = session.execute(
        select(
            func.count(RiskPrediction.id),
            func.avg(RiskPrediction.fraud_probability),
            func.sum(case((RiskPrediction.fraud_probability >= high, 1), else_=0)),
        )
        .select_from(Transaction)
        .join(RiskPrediction, RiskPrediction.transaction_id == Transaction.id)
        .where(Transaction.transaction_timestamp >= start)
        .where(Transaction.transaction_timestamp < end)
    ).one()

    anomaly = session.execute(
        select(
            func.count(RiskSignal.id),
            func.avg(RiskSignal.signal_value),
            func.sum(case((RiskSignal.signal_value >= critical, 1), else_=0)),
        )
        .select_from(Transaction)
        .join(RiskSignal, RiskSignal.transaction_id == Transaction.id)
        .where(RiskSignal.signal_name == ANOMALY_SIGNAL)
        .where(Transaction.transaction_timestamp >= start)
        .where(Transaction.transaction_timestamp < end)
    ).one()

    scored = int(fraud[0] or 0)
    anomaly_scored = int(anomaly[0] or 0)
    return {
        "from": start,
        "to": end,
        "scored_transactions": scored,
        "mean_fraud_probability": float(fraud[1]) if fraud[1] is not None else None,
        "high_risk_count": int(fraud[2] or 0),
        "high_risk_percent": (float(fraud[2] or 0) / scored * 100) if scored else None,
        "anomaly_scored_transactions": anomaly_scored,
        "mean_anomaly_score": float(anomaly[1]) if anomaly[1] is not None else None,
        "critical_anomaly_count": int(anomaly[2] or 0),
        "critical_anomaly_percent": (
            (float(anomaly[2] or 0) / anomaly_scored * 100) if anomaly_scored else None
        ),
    }


# --------------------------------------------------------------------------
# Policy effectiveness
# --------------------------------------------------------------------------
def policy_effectiveness(
    session: Session, *, config: MonitoringConfig | None = None
) -> dict[str, Any]:
    """Per-rule trigger counts, decision mix, and human override rate.

    Built from real decision rows and real analyst resolutions. Rules are never
    changed here - the output is evidence for a human to act on.
    """
    active = config or get_monitoring_config()
    latest = latest_decisions(session)

    # PostgreSQL expands the matched-rules array and groups server-side, so this
    # returns a few dozen rows instead of 20,000. Streaming every decision into
    # Python was measured at ~460ms for this endpoint.
    if session.get_bind().dialect.name == "postgresql":
        rule_column = func.jsonb_array_elements_text(RiskDecision.matched_rules).label("rule")
        grouped = session.execute(
            select(rule_column, RiskDecision.action, ReviewCase.resolution, func.count())
            .select_from(RiskDecision)
            .join(latest, latest.c.decision_id == RiskDecision.id)
            .join(ReviewCase, ReviewCase.risk_decision_id == RiskDecision.id, isouter=True)
            .group_by("rule", RiskDecision.action, ReviewCase.resolution)
        ).all()
        rows = [
            ([str(name)], action, resolution, int(count))
            for name, action, resolution, count in grouped
        ]
    else:
        rows = [
            (matched or [], action, resolution, 1)
            for matched, action, resolution in session.execute(
                select(RiskDecision.matched_rules, RiskDecision.action, ReviewCase.resolution)
                .select_from(RiskDecision)
                .join(latest, latest.c.decision_id == RiskDecision.id)
                .join(ReviewCase, ReviewCase.risk_decision_id == RiskDecision.id, isouter=True)
            ).all()
        ]

    blank = {
        "triggers": 0,
        "approve": 0,
        "step_up": 0,
        "review": 0,
        "block": 0,
        "resolved": 0,
        "overrides": 0,
    }
    stats: dict[str, dict[str, int]] = {rule: dict(blank) for rule in sorted(KNOWN_RULE_IDS)}

    for matched_rules, action, resolution, count in rows:
        action_key = str(action)
        overridden = _is_override(action_key, resolution)
        settled = resolution is not None
        for rule in matched_rules or []:
            bucket = stats.setdefault(str(rule), dict(blank))
            bucket["triggers"] += count
            bucket[action_key] = bucket.get(action_key, 0) + count
            if settled:
                bucket["resolved"] += count
                if overridden:
                    bucket["overrides"] += count

    results: list[dict[str, Any]] = []
    for rule_id, bucket in stats.items():
        resolved = bucket["resolved"]
        reportable = resolved >= active.metrics.min_rule_triggers
        override_rate = (bucket["overrides"] / resolved) if resolved else None
        results.append(
            {
                "rule_id": rule_id,
                "description": rule_description(rule_id),
                "primary_action": RULE_PRIMARY_ACTION.get(rule_id, "unknown"),
                "triggers": bucket["triggers"],
                "approve_count": bucket["approve"],
                "step_up_count": bucket["step_up"],
                "review_count": bucket["review"],
                "block_count": bucket["block"],
                "resolved_count": resolved,
                "override_count": bucket["overrides"],
                # Withheld below the floor: one override out of two is not a
                # 50% override rate in any sense an operator should act on.
                "override_rate": override_rate if reportable else None,
                "override_rate_reportable": reportable,
                "flagged_high_override": bool(
                    reportable
                    and override_rate is not None
                    and override_rate >= active.metrics.high_override_rate
                ),
            }
        )

    results.sort(key=lambda item: (-item["triggers"], item["rule_id"]))
    return {
        "rules": results,
        "high_override_threshold": active.metrics.high_override_rate,
        "min_rule_triggers": active.metrics.min_rule_triggers,
        "policy_version": get_policy().policy_version,
        "override_note": (
            "An override means the analyst contradicted a position the engine took. A REVIEW "
            "decision is the engine declining to decide and asking for a human, so nothing the "
            "analyst concludes can contradict it and REVIEW cases are never overrides. Because "
            "the queue is filled almost entirely by REVIEW, override rates here are structurally "
            "low; a rate near zero means the policy is routing well, not that analysts always "
            "agree."
        ),
    }


def _is_override(machine_action: str, resolution: Any) -> bool:
    """Delegate to the one definition of an override.

    Deliberately not reimplemented here: a second copy is how the API and the
    metrics end up disagreeing about the same number.
    """
    if resolution is None:
        return False
    return review_is_override(machine_action, ReviewResolution(str(resolution)))


# --------------------------------------------------------------------------
# The high-risk block funnel
# --------------------------------------------------------------------------
def high_risk_funnel(session: Session, *, policy: PolicyConfig | None = None) -> dict[str, Any]:
    """Why a high model score does not automatically become a BLOCK.

    Phase 7 surfaced the observation this exists to explain: 258 transactions
    crossed the block threshold and exactly one was blocked. That is not a bug -
    it is the Phase 6 corroboration requirement, which withholds a block unless
    an independent investigation supports it. This funnel makes each stage of
    that filtering countable.
    """
    active = policy or get_policy()
    thresholds = active.thresholds
    block_threshold = Decimal(str(thresholds.fraud_block))
    latest = latest_decisions(session)

    high_score = (
        session.scalar(
            select(func.count(RiskPrediction.id)).where(
                RiskPrediction.fraud_probability >= block_threshold
            )
        )
        or 0
    )

    # Of those, how many have an investigation at all, and how many a usable one.
    with_investigation = (
        session.scalar(
            select(func.count(Investigation.id))
            .select_from(RiskPrediction)
            .join(Investigation, Investigation.transaction_id == RiskPrediction.transaction_id)
            .where(RiskPrediction.fraud_probability >= block_threshold)
        )
        or 0
    )

    # Corroboration is what the engine actually recorded, read back from the
    # decision rather than recomputed - the funnel must describe the decisions
    # that were made, not decisions it would make now.
    decision_rows = session.execute(
        select(RiskDecision.action, RiskDecision.reason_codes)
        .select_from(RiskDecision)
        .join(latest, latest.c.decision_id == RiskDecision.id)
        .where(RiskDecision.fraud_probability >= block_threshold)
    ).all()

    decided = len(decision_rows)
    withheld = 0
    corroborated = 0
    blocked = 0
    by_action: dict[str, int] = {}
    for action, reason_codes in decision_rows:
        codes = {str(code) for code in (reason_codes or [])}
        key = str(action).upper()
        by_action[key] = by_action.get(key, 0) + 1
        if "BLOCK_WITHHELD_PENDING_INVESTIGATION" in codes:
            withheld += 1
        if "INDEPENDENT_CORROBORATION" in codes:
            corroborated += 1
        if action == DecisionAction.BLOCK:
            blocked += 1

    stages = [
        {
            "stage": "HIGH_FRAUD_SCORE",
            "count": high_score,
            "description": (
                f"Fraud probability at or above the block threshold ({thresholds.fraud_block})."
            ),
        },
        {
            "stage": "DECIDED",
            "count": decided,
            "description": "Of those, transactions carrying a current policy decision.",
        },
        {
            "stage": "INVESTIGATION_AVAILABLE",
            "count": with_investigation,
            "description": (
                "Of those, transactions with an investigation on record. Without one "
                "there is nothing to corroborate the model."
            ),
        },
        {
            "stage": "SUFFICIENT_CORROBORATION",
            "count": corroborated,
            "description": (
                f"Investigations that produced at least "
                f"{active.evidence.min_independent_sources_for_block} independent "
                "high-severity evidence sources."
            ),
        },
        {
            "stage": "BLOCK_ELIGIBLE",
            "count": corroborated,
            "description": "Transactions meeting every condition the block rule requires.",
        },
        {
            "stage": "FINAL_DECISION_BLOCK",
            "count": blocked,
            "description": "Transactions the engine actually blocked.",
        },
    ]

    return {
        "stages": stages,
        "withheld_pending_investigation": withheld,
        "final_actions": by_action,
        "block_threshold": thresholds.fraud_block,
        "min_independent_sources": active.evidence.min_independent_sources_for_block,
        "policy_version": active.policy_version,
        "explanation": (
            "A high model score is necessary but not sufficient for a block. The policy "
            "requires independent corroboration from an investigation before taking the "
            "one action a customer cannot undo in the moment; where that corroboration is "
            "absent the block is withheld and the transaction is routed to human review "
            "instead. The gap between the first and last stage is that safeguard working, "
            "not the model being ignored."
        ),
    }


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------
def recommendations(
    session: Session, *, config: MonitoringConfig | None = None
) -> list[dict[str, Any]]:
    """Analytical suggestions for a human to consider.

    **Nothing here is executed.** No model is retrained, no threshold moved, no
    policy edited, no decision revised. Each item names the metric that produced
    it so a reader can check the reasoning before acting on it.
    """
    active = config or get_monitoring_config()
    items: list[dict[str, Any]] = []

    effectiveness = policy_effectiveness(session, config=active)
    for rule in effectiveness["rules"]:
        if rule["flagged_high_override"]:
            items.append(
                {
                    "id": f"override-{rule['rule_id'].lower()}",
                    "severity": "high",
                    "title": f"{rule['rule_id']} has a high analyst override rate",
                    "detail": (
                        f"Analysts reached a different outcome on "
                        f"{rule['override_count']} of {rule['resolved_count']} resolved cases "
                        f"({rule['override_rate']:.0%}). Review threshold calibration for this "
                        "rule."
                    ),
                    "metric_source": "/api/monitoring/policy",
                    "action_required": "human_review",
                }
            )

    metrics = model_metrics(session, config=active)
    if not metrics["sufficient"]:
        items.append(
            {
                "id": "insufficient-labels",
                "severity": "info",
                "title": "Not enough labelled data to measure model performance",
                "detail": (
                    f"{metrics['labelled_samples']} ground-truth labels available; "
                    f"{metrics['minimum_required']} required. Resolve more review cases with "
                    "structured feedback to make precision and recall meaningful."
                ),
                "metric_source": "/api/monitoring/models",
                "action_required": "collect_more_feedback",
            }
        )
    elif metrics["false_positive_rate"] is not None and metrics["false_positive_rate"] > 0.3:
        items.append(
            {
                "id": "high-false-positive-rate",
                "severity": "medium",
                "title": "Measured false-positive rate is high",
                "detail": (
                    f"{metrics['false_positive']} of "
                    f"{metrics['false_positive'] + metrics['true_negative']} labelled legitimate "
                    "transactions were flagged. Consider reviewing the supervised thresholds."
                ),
                "metric_source": "/api/monitoring/models",
                "action_required": "human_review",
            }
        )

    drift = drift_report(session, config=active)
    drifting = [
        feature
        for feature in drift["features"]
        if feature["status"] == str(DriftStatus.DRIFT_DETECTED)
    ]
    for feature in drifting:
        items.append(
            {
                "id": f"drift-{feature['feature']}",
                "severity": "medium",
                "title": f"Distribution shift detected in {feature['feature']}",
                "detail": (
                    f"PSI {feature['psi']:.3f} exceeds the configured drift threshold "
                    f"({active.drift.psi_drift}). This means the distribution moved; it is not "
                    "evidence of fraud. Investigate what changed upstream."
                ),
                "metric_source": "/api/monitoring/drift",
                "action_required": "investigate",
            }
        )

    funnel = high_risk_funnel(session)
    if funnel["withheld_pending_investigation"] > 0:
        items.append(
            {
                "id": "blocks-withheld",
                "severity": "info",
                "title": "High-risk transactions had their block withheld",
                "detail": (
                    f"{funnel['withheld_pending_investigation']} transactions above the block "
                    "threshold lacked independent corroboration and were routed to review "
                    "instead. Running investigations on high-scoring transactions would let the "
                    "policy act on them."
                ),
                "metric_source": "/api/monitoring/high-risk-funnel",
                "action_required": "human_review",
            }
        )

    return items


__all__ = [
    "categorical_psi",
    "classify_drift",
    "drift_report",
    "high_risk_funnel",
    "label_coverage",
    "model_metrics",
    "policy_effectiveness",
    "population_stability_index",
    "recommendations",
    "score_windows",
]
