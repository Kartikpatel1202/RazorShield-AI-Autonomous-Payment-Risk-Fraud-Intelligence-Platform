"""The risk learning assistant.

A read-only analytical assistant that answers operational questions from
structured backend metrics.

**It has no language model.** Not as a fallback, not for phrasing, not for
"summarising". Every sentence it returns is assembled from numbers a query
produced, and every answer names the endpoints those numbers came from, the time
window they cover, and how much data was behind them.

That design is not timidity - it is the only version of this feature that can be
trusted. An assistant that generates prose about metrics will eventually
generate a number that is not in the metrics, and on a risk console a
confidently wrong figure is worse than no figure. Here it is structurally
impossible: the answer builders receive dictionaries of measured values and
interpolate them, so an invented number would have to come from somewhere, and
there is nowhere.

When the data cannot answer a question, the assistant says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ReviewCase, Transaction
from app.models.enums import DecisionAction, DriftStatus
from app.services import feedback as feedback_service
from app.services import monitoring
from app.services.analytics import latest_decisions

logger = logging.getLogger(__name__)


class QuestionTopic(StrEnum):
    """The questions the assistant can answer.

    A closed set. An assistant that accepts free-form questions must either
    understand them - which needs a model - or guess, and guessing about risk
    metrics is exactly what this module exists to avoid. The UI offers these as
    buttons.
    """

    REVIEW_VOLUME = "review_volume"
    HIGHEST_OVERRIDE_RULE = "highest_override_rule"
    HIGH_RISK_NOT_BLOCKED = "high_risk_not_blocked"
    CONFIRMED_FRAUD_COUNT = "confirmed_fraud_count"
    MODEL_DRIFT = "model_drift"
    MODEL_PERFORMANCE = "model_performance"


@dataclass(frozen=True)
class Answer:
    """One grounded answer.

    ``metric_sources`` and ``data_availability`` are not decoration. They are how
    a reader checks the answer without trusting it.
    """

    topic: QuestionTopic
    question: str
    answer: str
    metric_sources: list[str]
    time_window: str
    data_availability: str
    sufficient: bool
    figures: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": str(self.topic),
            "question": self.question,
            "answer": self.answer,
            "metric_sources": self.metric_sources,
            "time_window": self.time_window,
            "data_availability": self.data_availability,
            "sufficient": self.sufficient,
            "figures": self.figures,
        }


QUESTIONS: dict[QuestionTopic, str] = {
    QuestionTopic.REVIEW_VOLUME: "Why did review volume increase?",
    QuestionTopic.HIGHEST_OVERRIDE_RULE: "Which rule has the highest override rate?",
    QuestionTopic.HIGH_RISK_NOT_BLOCKED: "Why were high-risk transactions not blocked?",
    QuestionTopic.CONFIRMED_FRAUD_COUNT: "How many confirmed fraud cases were found?",
    QuestionTopic.MODEL_DRIFT: "Is model behavior drifting?",
    QuestionTopic.MODEL_PERFORMANCE: "How is the model performing against analyst labels?",
}


def _was(count: int) -> str:
    """Subject-verb agreement, so a count of one does not read as a typo."""
    return "was" if count == 1 else "were"


def _have(count: int) -> str:
    return "has" if count == 1 else "have"


def _dataset_window(session: Session) -> str:
    span = session.execute(
        select(
            func.min(Transaction.transaction_timestamp),
            func.max(Transaction.transaction_timestamp),
        )
    ).one()
    if span[0] is None:
        return "no transaction data"
    return f"{span[0]:%Y-%m-%d} to {span[1]:%Y-%m-%d} (entire dataset)"


# --------------------------------------------------------------------------
# Answer builders
# --------------------------------------------------------------------------
def _review_volume(session: Session) -> Answer:
    """Explain the review queue's size from the rules that fill it."""
    latest = latest_decisions(session)
    total_cases = session.scalar(select(func.count(ReviewCase.id))) or 0
    open_cases = (
        session.scalar(
            select(func.count(ReviewCase.id)).where(ReviewCase.status.in_(("open", "in_review")))
        )
        or 0
    )
    review_decisions = (
        session.scalar(
            select(func.count())
            .select_from(latest)
            .where(latest.c.action.in_((DecisionAction.REVIEW, DecisionAction.BLOCK)))
        )
        or 0
    )

    effectiveness = monitoring.policy_effectiveness(session)
    contributors = sorted(
        (rule for rule in effectiveness["rules"] if rule["review_count"] > 0),
        key=lambda rule: -rule["review_count"],
    )[:3]

    if total_cases == 0:
        return Answer(
            topic=QuestionTopic.REVIEW_VOLUME,
            question=QUESTIONS[QuestionTopic.REVIEW_VOLUME],
            answer="No review cases exist yet, so there is no review volume to explain.",
            metric_sources=["/api/reviews", "/api/monitoring/policy"],
            time_window=_dataset_window(session),
            data_availability="0 review cases",
            sufficient=False,
        )

    breakdown = "; ".join(
        f"{rule['rule_id']} routed {rule['review_count']:,}" for rule in contributors
    )
    return Answer(
        topic=QuestionTopic.REVIEW_VOLUME,
        question=QUESTIONS[QuestionTopic.REVIEW_VOLUME],
        answer=(
            f"{total_cases:,} review cases exist and {open_cases:,} are still open. "
            f"{review_decisions:,} current decisions require human review. The rules "
            f"sending the most work to the queue are: {breakdown}. "
            "Review volume is driven by these rules matching, not by any change in "
            "queue behaviour."
        ),
        metric_sources=["/api/reviews", "/api/monitoring/policy", "/api/analytics/overview"],
        time_window=_dataset_window(session),
        data_availability=f"{total_cases:,} review cases, {review_decisions:,} decisions",
        sufficient=True,
        figures={
            "total_cases": total_cases,
            "open_cases": open_cases,
            "review_or_block_decisions": review_decisions,
            "top_contributors": contributors,
        },
    )


def _highest_override_rule(session: Session) -> Answer:
    """Name the rule analysts disagree with most, or say the sample is too small."""
    effectiveness = monitoring.policy_effectiveness(session)
    reportable = [rule for rule in effectiveness["rules"] if rule["override_rate"] is not None]

    if not reportable:
        resolved = sum(rule["resolved_count"] for rule in effectiveness["rules"])
        return Answer(
            topic=QuestionTopic.HIGHEST_OVERRIDE_RULE,
            question=QUESTIONS[QuestionTopic.HIGHEST_OVERRIDE_RULE],
            answer=(
                "Insufficient data. No rule has enough resolved review cases to report an "
                f"override rate; the threshold is {effectiveness['min_rule_triggers']} resolved "
                f"cases per rule and only {resolved} case"
                f"{'' if resolved == 1 else 's'} have been resolved in total."
            ),
            metric_sources=["/api/monitoring/policy"],
            time_window=_dataset_window(session),
            data_availability=f"{resolved} resolved review cases",
            sufficient=False,
            figures={"resolved_total": resolved},
        )

    top = max(reportable, key=lambda rule: rule["override_rate"])
    return Answer(
        topic=QuestionTopic.HIGHEST_OVERRIDE_RULE,
        question=QUESTIONS[QuestionTopic.HIGHEST_OVERRIDE_RULE],
        answer=(
            f"{top['rule_id']} has the highest override rate: analysts reached a different "
            f"outcome on {top['override_count']} of {top['resolved_count']} resolved cases "
            f"({top['override_rate']:.0%}). It triggered {top['triggers']:,} times overall. "
            "A high override rate is evidence for reviewing threshold calibration; no "
            "threshold has been changed."
        ),
        metric_sources=["/api/monitoring/policy"],
        time_window=_dataset_window(session),
        data_availability=(
            f"{len(reportable)} rule(s) above the "
            f"{effectiveness['min_rule_triggers']}-case reporting floor"
        ),
        sufficient=True,
        figures={"rule": top},
    )


def _high_risk_not_blocked(session: Session) -> Answer:
    """Walk the funnel that separates a high score from an automatic block."""
    funnel = monitoring.high_risk_funnel(session)
    stages = {stage["stage"]: stage["count"] for stage in funnel["stages"]}

    return Answer(
        topic=QuestionTopic.HIGH_RISK_NOT_BLOCKED,
        question=QUESTIONS[QuestionTopic.HIGH_RISK_NOT_BLOCKED],
        answer=(
            f"{stages.get('HIGH_FRAUD_SCORE', 0):,} transactions scored at or above the block "
            f"threshold ({funnel['block_threshold']}), and "
            f"{stages.get('FINAL_DECISION_BLOCK', 0):,} "
            f"{_was(stages.get('FINAL_DECISION_BLOCK', 0))} blocked. The difference is the "
            "corroboration requirement: a block also needs an investigation that independently "
            f"found the same thing, from at least {funnel['min_independent_sources']} separate "
            f"tools. Only {stages.get('INVESTIGATION_AVAILABLE', 0):,} of those transactions "
            f"{_have(stages.get('INVESTIGATION_AVAILABLE', 0))} an investigation at all, and "
            f"{stages.get('SUFFICIENT_CORROBORATION', 0):,} "
            f"produced enough corroborating evidence. The remaining "
            f"{funnel['withheld_pending_investigation']:,} had their block withheld and were "
            "routed to human review instead. That is the safeguard working, not the model "
            "being ignored."
        ),
        metric_sources=["/api/monitoring/high-risk-funnel", "/api/policy"],
        time_window=_dataset_window(session),
        data_availability=f"{stages.get('DECIDED', 0):,} decided high-score transactions",
        sufficient=stages.get("HIGH_FRAUD_SCORE", 0) > 0,
        figures={"stages": funnel["stages"], "withheld": funnel["withheld_pending_investigation"]},
    )


def _confirmed_fraud_count(session: Session) -> Answer:
    """Report confirmed fraud, with the coverage that qualifies it."""
    summary = feedback_service.summary(session)
    confirmed = summary["confirmed_fraud"]
    total = summary["total_feedback"]

    if total == 0:
        return Answer(
            topic=QuestionTopic.CONFIRMED_FRAUD_COUNT,
            question=QUESTIONS[QuestionTopic.CONFIRMED_FRAUD_COUNT],
            answer=(
                "No analyst feedback has been recorded yet, so no fraud has been confirmed. "
                "Resolve a review case with structured feedback to begin labelling."
            ),
            metric_sources=["/api/feedback/summary"],
            time_window=_dataset_window(session),
            data_availability="0 feedback records",
            sufficient=False,
        )

    return Answer(
        topic=QuestionTopic.CONFIRMED_FRAUD_COUNT,
        question=QUESTIONS[QuestionTopic.CONFIRMED_FRAUD_COUNT],
        answer=(
            f"{confirmed:,} transaction{'' if confirmed == 1 else 's'} confirmed as fraud by an "
            f"analyst, out of {total:,} feedback record{'' if total == 1 else 's'} covering "
            f"{summary['ground_truth_labels']:,} ground-truth label"
            f"{'' if summary['ground_truth_labels'] == 1 else 's'}. That is "
            f"{summary['labelled_share_of_transactions']:.2%} of the "
            f"{summary['total_transactions']:,} transactions in the dataset - the remainder are "
            "unlabelled, not legitimate."
        ),
        metric_sources=["/api/feedback/summary"],
        time_window=_dataset_window(session),
        data_availability=(
            f"{summary['ground_truth_labels']:,} ground-truth labels of "
            f"{summary['total_transactions']:,} transactions"
        ),
        sufficient=True,
        figures={
            "confirmed_fraud": confirmed,
            "total_feedback": total,
            "ground_truth_labels": summary["ground_truth_labels"],
        },
    )


def _model_drift(session: Session) -> Answer:
    """Report drift status per feature, and say what drift does not mean."""
    report = monitoring.drift_report(session)
    features = report["features"]

    if not features:
        return Answer(
            topic=QuestionTopic.MODEL_DRIFT,
            question=QUESTIONS[QuestionTopic.MODEL_DRIFT],
            answer="No transaction data is available to compare windows.",
            metric_sources=["/api/monitoring/drift"],
            time_window="no data",
            data_availability="0 transactions",
            sufficient=False,
        )

    drifted = [f for f in features if f["status"] == str(DriftStatus.DRIFT_DETECTED)]
    watching = [f for f in features if f["status"] == str(DriftStatus.WATCH)]
    insufficient = [f for f in features if f["status"] == str(DriftStatus.INSUFFICIENT_DATA)]

    if drifted:
        headline = (
            f"Yes, for {len(drifted)} of {len(features)} monitored features: "
            + ", ".join(f"{f['feature']} (PSI {f['psi']:.3f})" for f in drifted)
            + "."
        )
    elif watching:
        headline = (
            f"No drift detected, but {len(watching)} feature(s) are in the WATCH band: "
            + ", ".join(f"{f['feature']} (PSI {f['psi']:.3f})" for f in watching)
            + "."
        )
    else:
        headline = f"No. All {len(features) - len(insufficient)} measurable features are NORMAL."

    caveat = (
        " Drift means the distribution moved between the baseline and current windows. "
        "It is not evidence of fraud."
    )
    if insufficient:
        caveat += (
            f" {len(insufficient)} feature(s) had too little data in one window to measure: "
            + ", ".join(f["feature"] for f in insufficient)
            + "."
        )

    window = (
        f"baseline {report['baseline_from']:%Y-%m-%d} to {report['baseline_to']:%Y-%m-%d}, "
        f"current {report['current_from']:%Y-%m-%d} to {report['current_to']:%Y-%m-%d}"
    )
    return Answer(
        topic=QuestionTopic.MODEL_DRIFT,
        question=QUESTIONS[QuestionTopic.MODEL_DRIFT],
        answer=headline + caveat,
        metric_sources=["/api/monitoring/drift"],
        time_window=window,
        data_availability=(
            f"{len(features) - len(insufficient)} of {len(features)} features measurable"
        ),
        sufficient=len(insufficient) < len(features),
        figures={"features": features},
    )


def _model_performance(session: Session) -> Answer:
    """Report precision and recall, or explain why they are withheld."""
    metrics = monitoring.model_metrics(session)

    if not metrics["sufficient"]:
        return Answer(
            topic=QuestionTopic.MODEL_PERFORMANCE,
            question=QUESTIONS[QuestionTopic.MODEL_PERFORMANCE],
            answer=(
                metrics["message"]
                + " Unlabelled transactions are not counted as legitimate examples, so the "
                "metric is withheld rather than computed against assumed negatives."
            ),
            metric_sources=["/api/monitoring/models"],
            time_window=_dataset_window(session),
            data_availability=(
                f"{metrics['labelled_samples']} labelled of "
                f"{metrics['total_transactions']:,} transactions"
            ),
            sufficient=False,
            figures={"labelled_samples": metrics["labelled_samples"]},
        )

    return Answer(
        topic=QuestionTopic.MODEL_PERFORMANCE,
        question=QUESTIONS[QuestionTopic.MODEL_PERFORMANCE],
        answer=(
            f"Measured over {metrics['labelled_samples']:,} analyst-labelled transactions: "
            f"precision {metrics['precision']:.1%}, recall {metrics['recall']:.1%}, "
            f"F1 {metrics['f1']:.3f}. False positives {metrics['false_positive']:,}, "
            f"false negatives {metrics['false_negative']:,}. These cover labelled data only; "
            f"{metrics['unlabelled_transactions']:,} transactions remain unlabelled and are "
            "excluded rather than assumed legitimate."
        ),
        metric_sources=["/api/monitoring/models"],
        time_window=_dataset_window(session),
        data_availability=(
            f"{metrics['labelled_samples']:,} labelled of "
            f"{metrics['total_transactions']:,} transactions"
        ),
        sufficient=True,
        figures=metrics,
    )


_BUILDERS: dict[QuestionTopic, Callable[[Session], Answer]] = {
    QuestionTopic.REVIEW_VOLUME: _review_volume,
    QuestionTopic.HIGHEST_OVERRIDE_RULE: _highest_override_rule,
    QuestionTopic.HIGH_RISK_NOT_BLOCKED: _high_risk_not_blocked,
    QuestionTopic.CONFIRMED_FRAUD_COUNT: _confirmed_fraud_count,
    QuestionTopic.MODEL_DRIFT: _model_drift,
    QuestionTopic.MODEL_PERFORMANCE: _model_performance,
}


def answer(session: Session, topic: QuestionTopic) -> Answer:
    """Answer one question from measured data."""
    return _BUILDERS[topic](session)


def available_questions() -> list[dict[str, str]]:
    """The closed question set, for the UI to render as choices."""
    return [{"topic": str(topic), "question": text} for topic, text in QUESTIONS.items()]


def answered_at() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "QUESTIONS",
    "Answer",
    "QuestionTopic",
    "answer",
    "available_questions",
]
