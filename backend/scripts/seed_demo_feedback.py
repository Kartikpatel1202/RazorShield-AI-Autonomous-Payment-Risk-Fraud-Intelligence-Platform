"""Seed simulated analyst feedback so the monitoring pages have data.

Run from the ``backend/`` directory:

    python scripts/seed_demo_feedback.py --dry-run
    python scripts/seed_demo_feedback.py --yes
    python scripts/seed_demo_feedback.py --yes --count 120
    python scripts/seed_demo_feedback.py --purge --yes

WHAT THIS IS, AND WHAT IT IS NOT
================================

These labels are **simulated**, not human. Every row it writes carries a note
saying so, and the seeded feedback is attributed to no analyst
(``analyst_id`` is null) precisely so it can never be mistaken for a person's
work.

The outcomes are derived from the dataset's own ``is_fraud`` column - the
simulation's generation-time property. That makes the resulting precision and
recall a measurement of *the model against the simulator*, which is a useful
demonstration of the machinery and **not** a measurement of real-world accuracy
or of analyst performance. Anyone reading those metrics should know that, which
is why it is stated here, in the notes of every row, and in the final report.

To see the honest "insufficient labeled data" behaviour instead, run
``--purge --yes`` and leave the table empty.

WHAT IT TOUCHES
===============

Writes: ``analyst_feedback``, and - for reviewed cases only - the resolution
        fields on ``review_cases`` plus an ``analyst_decisions`` row, which is
        what a real analyst settling a case produces.
Reads:  ``risk_decisions``, ``transactions``.

It never writes to ``risk_decisions`` - the append-only guard would raise - and
never modifies a transaction, a prediction or a signal. Audited approvals are
labelled without touching any review case, because no case exists for them.

Resolving alongside labelling is deliberate: it mirrors the real workflow, where
an analyst settles a case and records what they concluded in one action. Without
it every rule's override rate stays "n/a" and the policy-effectiveness page has
nothing to show.

HOW CASES ARE CHOSEN
====================

Two groups, because one alone would be misleading.

**Reviewed cases** - the earliest N review cases by id. These are all
transactions the system flagged, so on their own they yield no true negatives
and no false negatives: recall would be 1.0 by construction, not by merit.

**Sampled approvals** - a deterministic sample of transactions the system
approved, taken at a fixed stride so the same database yields the same sample.
This mirrors what a real risk team does: audit a slice of the approvals to find
what the model let through. It is what makes recall and the false-negative rate
measurable at all.

Both are deterministic and idempotent - a transaction that already carries
feedback is skipped, so re-running never duplicates a label or double-counts a
transaction in the confusion matrix.

The five ``TXN_SCENARIO_*`` demo transactions are deliberately excluded. They are
the walkthrough, and an analyst should be able to resolve C1 by hand and watch
the label appear; pre-labelling them would leave nothing to demonstrate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow `python scripts/seed_demo_feedback.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import AnalystFeedback, ReviewCase, RiskDecision, Transaction  # noqa: E402
from app.models.enums import (  # noqa: E402
    DecisionAction,
    FeedbackOutcome,
    FeedbackReason,
    ReviewResolution,
)
from app.services.feedback import record_feedback  # noqa: E402
from app.services.review import ReviewCaseError, resolve_case  # noqa: E402

logger = logging.getLogger("seed-demo-feedback")

PROTECTED_ENVIRONMENTS = frozenset({"production", "prod", "live"})

SIMULATED_NOTE = (
    "SIMULATED demo label, derived from the dataset's generation-time is_fraud "
    "flag. Not a human analyst judgement."
)

DEFAULT_COUNT = 120

#: How many approved transactions to audit alongside the reviewed cases. Without
#: these the labelled sample contains only flagged transactions and half the
#: confusion matrix is empty by construction.
DEFAULT_APPROVED_SAMPLE = 80

#: Left unlabelled so the walkthrough has something to do. Matching on the
#: reference prefix rather than hardcoded ids keeps this working if the seed
#: regenerates them.
SCENARIO_PREFIX = "TXN_SCENARIO"


def _outcome_for(
    *, actually_fraud: bool, machine_action: DecisionAction
) -> tuple[FeedbackOutcome, FeedbackReason]:
    """Map (truth, machine call) onto the outcome an analyst would have recorded.

    The four combinations map to the four ground-truth outcomes, which is what
    makes the seeded confusion matrix meaningful rather than arbitrary.
    """
    flagged = machine_action != DecisionAction.APPROVE

    if actually_fraud and flagged:
        return FeedbackOutcome.CONFIRMED_FRAUD, FeedbackReason.COORDINATED_ACTIVITY
    if actually_fraud and not flagged:
        # The machine let a fraudulent payment through.
        return FeedbackOutcome.FALSE_NEGATIVE, FeedbackReason.MODEL_FALSE_NEGATIVE
    if not actually_fraud and flagged:
        # The machine stopped a legitimate payment.
        return FeedbackOutcome.FALSE_POSITIVE, FeedbackReason.MODEL_FALSE_POSITIVE
    return FeedbackOutcome.LEGITIMATE, FeedbackReason.KNOWN_CUSTOMER_BEHAVIOR


def _approved_sample(
    session: Session, size: int, exclude: set[int]
) -> list[tuple[None, int, bool, str]]:
    """A deterministic slice of approved transactions to audit.

    Taken at a fixed stride over the ordered set rather than at random, so the
    same database always produces the same sample and the demo is reproducible.
    Fraudulent approvals are drawn first: they are rare, and a sample containing
    none of them would show a false-negative rate of zero that reflects the
    sampling, not the model.
    """
    if size <= 0:
        return []

    rows = session.execute(
        select(Transaction.id, Transaction.is_fraud, RiskDecision.action)
        .select_from(Transaction)
        .join(RiskDecision, RiskDecision.transaction_id == Transaction.id)
        .where(RiskDecision.action == DecisionAction.APPROVE)
        .where(Transaction.transaction_id.notlike(f"{SCENARIO_PREFIX}%"))
        .order_by(Transaction.is_fraud.desc(), Transaction.id)
    ).all()

    available = [row for row in rows if row[0] not in exclude]
    if not available:
        return []

    fraudulent = [row for row in available if row[1]]
    legitimate = [row for row in available if not row[1]]

    picked = list(fraudulent[:size])
    remaining = size - len(picked)
    if remaining > 0 and legitimate:
        stride = max(len(legitimate) // remaining, 1)
        picked.extend(legitimate[::stride][:remaining])

    return [(None, row[0], bool(row[1]), str(row[2])) for row in picked]


def _resolution_for(outcome: FeedbackOutcome) -> ReviewResolution:
    """What an analyst reaching this conclusion would have done with the payment.

    Note this is a *separate* judgement from the outcome. Confirmed fraud stops
    the payment; a false positive releases it; an open outcome escalates rather
    than pretending the question was settled.
    """
    if outcome in (FeedbackOutcome.CONFIRMED_FRAUD, FeedbackOutcome.FALSE_NEGATIVE):
        return ReviewResolution.REJECTED
    if outcome in (FeedbackOutcome.LEGITIMATE, FeedbackOutcome.FALSE_POSITIVE):
        return ReviewResolution.APPROVED
    return ReviewResolution.ESCALATED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed simulated analyst feedback for the monitoring demo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--yes", action="store_true", help="Actually write the rows.")
    parser.add_argument("--dry-run", action="store_true", help="Report and exit.")
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help=f"How many review cases to label. Default {DEFAULT_COUNT}.",
    )
    parser.add_argument(
        "--approved-sample",
        type=int,
        default=DEFAULT_APPROVED_SAMPLE,
        help=(
            "How many approved transactions to audit, so false negatives and true "
            f"negatives are measurable. Default {DEFAULT_APPROVED_SAMPLE}."
        ),
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete every analyst_feedback row before seeding (or instead of it).",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="With --purge, delete without writing new labels.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    environment = (settings.environment or "").strip().lower()
    if environment in PROTECTED_ENVIRONMENTS:
        logger.error("Refusing to run: ENVIRONMENT=%r is protected.", environment)
        return 2

    with SessionLocal() as session:
        existing = session.scalar(select(func.count(AnalystFeedback.id))) or 0
        already_labelled = {
            row for (row,) in session.execute(select(AnalystFeedback.transaction_id)).all()
        }

        candidates = session.execute(
            select(ReviewCase.id, Transaction.id, Transaction.is_fraud, RiskDecision.action)
            .select_from(ReviewCase)
            .join(Transaction, Transaction.id == ReviewCase.transaction_id)
            .join(RiskDecision, RiskDecision.id == ReviewCase.risk_decision_id)
            .where(Transaction.transaction_id.notlike(f"{SCENARIO_PREFIX}%"))
            .order_by(ReviewCase.id)
        ).all()
        selectable = [row for row in candidates if row[1] not in already_labelled]
        planned = selectable[: max(args.count, 0)]

        approvals = _approved_sample(
            session, max(args.approved_sample, 0), already_labelled | {row[1] for row in planned}
        )

        logger.info("Existing analyst_feedback rows: %d", existing)
        logger.info("Review cases available to label: %d", len(selectable))
        logger.info(
            "Would write: %d labels from review cases + %d from audited approvals",
            0 if args.no_seed else len(planned),
            0 if args.no_seed else len(approvals),
        )
        if args.purge:
            logger.info("Would first delete all %d existing feedback rows", existing)
        logger.info("")
        logger.info("These labels are SIMULATED and derived from the dataset's is_fraud flag.")
        logger.info("They are not human judgements and analyst_id is left null.")

        if not args.yes or args.dry_run:
            logger.info("")
            logger.info("Dry run - nothing changed. Re-run with --yes to apply.")
            return 0

        if args.purge:
            session.execute(delete(AnalystFeedback))
            session.flush()
            logger.info("Deleted %d feedback rows.", existing)
            if args.no_seed:
                session.commit()
                return 0
            planned = list(candidates)[: max(args.count, 0)]
            approvals = _approved_sample(
                session, max(args.approved_sample, 0), {row[1] for row in planned}
            )

        written = 0
        resolved = 0
        counts: dict[str, int] = {}
        # Reviewed cases first, then the audited approvals. Both go through the
        # same recording path, so both are validated the same way.
        for _case_id, transaction_id, is_fraud, action in [*planned, *approvals]:
            transaction = session.get(Transaction, transaction_id)
            if transaction is None:
                continue
            case = session.scalar(
                select(ReviewCase).where(ReviewCase.transaction_id == transaction_id)
            )
            outcome, reason = _outcome_for(
                actually_fraud=bool(is_fraud), machine_action=DecisionAction(str(action))
            )
            record_feedback(
                session,
                transaction=transaction,
                outcome=outcome,
                reason=reason,
                notes=SIMULATED_NOTE,
                analyst_id=None,
                review_case=case,
            )
            counts[str(outcome)] = counts.get(str(outcome), 0) + 1
            written += 1

            # Settle the case too, when there is one. An already-settled case is
            # left alone rather than re-resolved - resolutions are recorded once.
            if case is not None and case.status != "resolved":
                try:
                    resolve_case(
                        session,
                        case,
                        _resolution_for(outcome),
                        analyst_id=None,
                        reason=SIMULATED_NOTE,
                    )
                    resolved += 1
                except ReviewCaseError:
                    pass

        session.commit()

        total = session.scalar(select(func.count(AnalystFeedback.id))) or 0
        logger.info(
            "Wrote %d simulated labels and resolved %d review cases. Total feedback rows: %d",
            written,
            resolved,
            total,
        )
        for outcome, count in sorted(counts.items()):
            logger.info("  %-24s %d", outcome, count)

        # These tables must be exactly as they were.
        for model, name in (
            (Transaction, "transactions"),
            (RiskDecision, "risk_decisions"),
        ):
            logger.info(
                "  %-24s %d (unchanged)", name, session.scalar(select(func.count(model.id)))
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
