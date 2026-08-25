"""Reset the decision layer to a clean demo state.

Run from the ``backend/`` directory:

    python scripts/reset_decisions.py --dry-run
    python scripts/reset_decisions.py --yes
    python scripts/reset_decisions.py --yes --limit 2000

WHAT THIS REMOVES (and nothing else):

  * every row in ``risk_decisions``
  * every row in ``review_cases``
  * every row in ``analyst_decisions``
  * rows in ``audit_logs`` whose ``event_type`` is one of
    ``risk.decision``, ``review.case_opened``, ``review.resolved``

WHAT THIS PRESERVES:

  * all 20,000 ``transactions``
  * all 20,000 ``risk_predictions`` (Phase 3)
  * all 40,000 ``risk_signals`` (Phase 4)
  * all ``investigations`` (Phase 5) and their ``investigation.completed``
    audit entries
  * every customer, merchant, device and IP address

It never drops a table, never drops the database, and never touches the
PostgreSQL volume.

WHY IT BYPASSES THE IMMUTABILITY GUARD

``risk_decisions`` is append-only, enforced by an ORM ``before_flush`` listener
(``app.db.immutability``). That guard exists to stop the *application* from
rewriting its own history, which is the real threat. This script is an
administrative operation, not an application one: it issues Core DELETE
statements, which the ORM guard does not cover, and it says so out loud rather
than quietly disabling the listener.

To keep that distinction honest the script refuses to run when
``ENVIRONMENT`` looks like production, requires an explicit ``--yes``, and
prints exactly what it will delete before doing it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow `python scripts/reset_decisions.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AnalystDecision,
    AuditLog,
    Investigation,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.services.decision_batch import decide_all  # noqa: E402

logger = logging.getLogger("reset-decisions")

#: Audit events this script owns. Phase 5's ``investigation.completed`` is
#: deliberately absent - those entries belong to investigations we keep.
DECISION_EVENT_TYPES = ("risk.decision", "review.case_opened", "review.resolved")

#: Environments where this must never run.
PROTECTED_ENVIRONMENTS = frozenset({"production", "prod", "live"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset decision records and regenerate them from stored signals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the reset. Without it the script only reports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and exit. Implied when --yes is absent.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Decide only the earliest N transactions. Default: every transaction.",
    )
    parser.add_argument(
        "--no-regenerate",
        action="store_true",
        help="Delete the decision records without creating new ones.",
    )
    return parser


def report_state(session: Session) -> dict[str, int]:
    """Row counts for everything this script touches or promises to preserve."""
    return {
        "transactions": session.scalar(select(func.count(Transaction.id))) or 0,
        "risk_predictions": session.scalar(select(func.count(RiskPrediction.id))) or 0,
        "risk_signals": session.scalar(select(func.count(RiskSignal.id))) or 0,
        "investigations": session.scalar(select(func.count(Investigation.id))) or 0,
        "risk_decisions": session.scalar(select(func.count(RiskDecision.id))) or 0,
        "review_cases": session.scalar(select(func.count(ReviewCase.id))) or 0,
        "analyst_decisions": session.scalar(select(func.count(AnalystDecision.id))) or 0,
        "audit_logs_decision": session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.event_type.in_(DECISION_EVENT_TYPES))
        )
        or 0,
        "audit_logs_other": session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.event_type.notin_(DECISION_EVENT_TYPES))
        )
        or 0,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    environment = (settings.environment or "").strip().lower()
    if environment in PROTECTED_ENVIRONMENTS:
        logger.error("Refusing to run: ENVIRONMENT=%r is protected.", environment)
        return 2

    with SessionLocal() as session:
        before = report_state(session)

        logger.info("Current state:")
        for name, count in before.items():
            logger.info("  %-22s %8d", name, count)

        to_delete = (
            before["risk_decisions"]
            + before["review_cases"]
            + before["analyst_decisions"]
            + before["audit_logs_decision"]
        )
        logger.info("")
        logger.info("Would delete %d rows across 4 tables:", to_delete)
        logger.info("  risk_decisions        %8d", before["risk_decisions"])
        logger.info("  review_cases          %8d", before["review_cases"])
        logger.info("  analyst_decisions     %8d", before["analyst_decisions"])
        logger.info("  audit_logs (decision) %8d", before["audit_logs_decision"])
        logger.info(
            "Preserving %d transactions, %d predictions, %d signals, %d investigations,",
            before["transactions"],
            before["risk_predictions"],
            before["risk_signals"],
            before["investigations"],
        )
        logger.info("and %d non-decision audit entries.", before["audit_logs_other"])

        if not args.yes or args.dry_run:
            logger.info("")
            logger.info("Dry run - nothing changed. Re-run with --yes to apply.")
            return 0

        # Order matters: analyst decisions reference review cases, which
        # reference decisions.
        session.execute(delete(AnalystDecision))
        session.execute(delete(ReviewCase))
        session.execute(delete(RiskDecision))
        session.execute(delete(AuditLog).where(AuditLog.event_type.in_(DECISION_EVENT_TYPES)))
        session.flush()
        logger.info("Deleted %d decision-layer rows.", to_delete)

        if not args.no_regenerate:
            outcome = decide_all(session, limit=args.limit)
            logger.info("Regenerated: %s", outcome.summary())

        session.commit()

        after = report_state(session)
        logger.info("")
        logger.info("Final state:")
        for name, count in after.items():
            logger.info("  %-22s %8d", name, count)

        for preserved in ("transactions", "risk_predictions", "risk_signals", "investigations"):
            if after[preserved] != before[preserved]:
                logger.error(
                    "PRESERVATION FAILURE: %s went from %d to %d",
                    preserved,
                    before[preserved],
                    after[preserved],
                )
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
