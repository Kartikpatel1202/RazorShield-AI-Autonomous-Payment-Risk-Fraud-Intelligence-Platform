"""Bring an empty deployment to a usable state, idempotently.

    python scripts/bootstrap.py                 # do whatever is still missing
    python scripts/bootstrap.py --status        # report only, change nothing
    python scripts/bootstrap.py --transactions 5000

WHY THIS EXISTS

Every stage of this platform already had a way to be run: ``alembic upgrade``,
``scripts/seed_data.py``, ``python -m ml.inference.batch_predict``,
``python -m ml.anomaly.batch_predict``, ``scripts/reset_decisions.py``,
``scripts/manage_users.py``. What did not exist was an order to run them in that
a deployment could execute on its own, which is why a freshly deployed database
comes up with a complete schema, zero rows, and a dashboard that honestly
reports zero for everything.

The stages, and what each one unblocks:

  1. schema      alembic upgrade head
  2. dataset     merchants, customers, devices, IPs, transactions
                 - the simulator refuses to start without a merchant to
                   attribute simulated payments to
  3. predictions Phase 3 supervised fraud probabilities
  4. signals     Phase 4 behavioural anomaly scores
  5. investigations
                 Phase 5 over the transactions the policy would have wanted
                 investigated - the stage the batch path used to skip, without
                 which a block has no corroboration and downgrades to REVIEW
  6. decisions   Phase 6 deterministic policy over 3, 4 and 5, plus the review
                 cases and audit rows it opens
                 - until this runs, "decided by policy" is genuinely 0 and the
                   dashboard is right to say so
  7. operator    an account that can actually sign in, and one that holds
                 ``simulator:control``

IDEMPOTENCE

Each stage is skipped when its output already exists. Re-running this creates no
duplicate row and rewrites no history; ``--status`` shows what a run would do.
The one destructive operation in the codebase - the seed generator's table
reset - is reached only when ``transactions`` is empty, so a populated database
is never cleared by a redeploy.

CREDENTIALS

Read from the environment, never from argv, and never defaulted. Without
``BOOTSTRAP_ADMIN_EMAIL`` and ``BOOTSTRAP_ADMIN_PASSWORD`` the account stage is
skipped with a message rather than inventing a password - a demo deployment with
a guessable administrator is worse than one with none.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow `python scripts/bootstrap.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.security import (  # noqa: E402
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    hash_password,
)
from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Investigation,
    Merchant,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
    User,
)
from app.models.enums import UserRole  # noqa: E402
from app.services.auth import find_user_by_email  # noqa: E402

logger = logging.getLogger("bootstrap")


#: Default dataset size for a bootstrap run: the same number
#: ``scripts/seed_data.py`` uses, read from :class:`SeedConfig` rather than
#: restated here.
#:
#: This was previously a literal 3,000, chosen so a bootstrap would finish
#: quickly inside a small host's start-up window. The cost of that shortcut was
#: two entry points to the same generator disagreeing about what the dataset
#: is: a local `seed_data.py` produced 20,000 transactions and a deployment
#: produced 3,000, with nothing in either place saying so. The environments then
#: disagreed about every aggregate on the dashboard.
#:
#: One default, one source of truth. A host that genuinely cannot seed 20,000 -
#: a 512 MB free instance seeding across a network hop to a managed database -
#: sets ``BOOTSTRAP_TRANSACTIONS`` explicitly, which is a decision someone made
#: and can see, rather than a difference nobody knew about.
def _default_transactions() -> int:
    from app.seed import SeedConfig

    return SeedConfig().transactions


#: How many of the highest-risk transactions to investigate during a
#: bootstrap. Only those the policy's own gate accepts are actually run, so
#: this is a ceiling on the work rather than a target. It is a knob because
#: an investigation is the one stage that can call a language model: free
#: under the default mock provider, metered against a real one.
DEFAULT_INVESTIGATIONS = 200

#: How long stage 5 may spend before handing the run on, in seconds.
#:
#: A ceiling on the *work* is not a ceiling on the *time*: 125 remaining
#: candidates against a language model that answers in 60s is over two hours,
#: and no free instance stays up that long. The count knob bounds spend; this
#: one bounds the window, which is what actually decides whether the decision
#: stage behind it is ever reached. Five minutes is comfortably enough for the
#: default mock provider to exhaust the cap, and short enough that a real one
#: cannot hold the run open indefinitely.
DEFAULT_INVESTIGATION_SECONDS = 300

#: Rows the Phase 4 batch scorer writes per transaction - a behavioural
#: anomaly score and a customer-relative deviation. Used to tell a finished
#: signal stage from an interrupted one.
SIGNALS_PER_TRANSACTION = 2

#: Same floor as ``scripts/manage_users.py``. Below this, bcrypt's cost stops
#: mattering because the search space is small enough to enumerate.
MIN_PASSWORD_LENGTH = 12

ADMIN_EMAIL_ENV = "BOOTSTRAP_ADMIN_EMAIL"
ADMIN_PASSWORD_ENV = "BOOTSTRAP_ADMIN_PASSWORD"
VIEWER_EMAIL_ENV = "BOOTSTRAP_VIEWER_EMAIL"
VIEWER_PASSWORD_ENV = "BOOTSTRAP_VIEWER_PASSWORD"


class BootstrapError(RuntimeError):
    """A stage could not complete. The message is safe to log."""


@dataclass
class Counts:
    """What the database currently holds, per stage."""

    merchants: int
    transactions: int
    predictions: int
    signals: int
    investigations: int
    decisions: int
    login_capable_users: int
    admins: int

    def describe(self) -> str:
        return (
            f"merchants={self.merchants:,} transactions={self.transactions:,} "
            f"predictions={self.predictions:,} signals={self.signals:,} "
            f"investigations={self.investigations:,} "
            f"decisions={self.decisions:,} accounts_with_password={self.login_capable_users:,} "
            f"admins={self.admins:,}"
        )


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def read_counts(session: Session) -> Counts:
    """One read of every stage's output. Cheap enough to run on every start."""
    return Counts(
        merchants=_count(session, Merchant),
        transactions=_count(session, Transaction),
        predictions=_count(session, RiskPrediction),
        signals=_count(session, RiskSignal),
        investigations=_count(session, Investigation),
        decisions=_count(session, RiskDecision),
        login_capable_users=int(
            session.scalar(
                select(func.count()).select_from(User).where(User.password_hash.is_not(None))
            )
            or 0
        ),
        admins=int(
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.ADMIN, User.password_hash.is_not(None))
            )
            or 0
        ),
    )


# --------------------------------------------------------------------------
# Stage 1 - schema
# --------------------------------------------------------------------------
def upgrade_schema() -> None:
    """Run ``alembic upgrade head`` in-process.

    In-process rather than by shelling out so the migration uses the same
    ``DATABASE_URL`` this script already resolved. A subprocess would re-read
    the environment and could, in a container where only part of it is
    exported, quietly migrate a different database than the one being seeded.
    """
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini.exists():
        raise BootstrapError(f"alembic.ini not found at {ini}")

    config = Config(str(ini))
    # `alembic.ini` resolves script_location relative to itself; make sure the
    # working directory cannot change what that means.
    config.set_main_option("script_location", str(ini.parent.parent / "database" / "migrations"))

    # Stop `env.py` calling `fileConfig()` on alembic.ini. `fileConfig` defaults
    # to disable_existing_loggers=True, which silently switches off every logger
    # already configured - including this one and the application's structured
    # handler. The symptom is not a crash: the migration runs, and then the rest
    # of the bootstrap produces no output at all, successful or otherwise.
    config.config_file_name = None

    logger.info("[1/7] schema: alembic upgrade head")
    try:
        command.upgrade(config, "head")
    except Exception as exc:
        raise BootstrapError(f"alembic upgrade failed: {exc}") from exc


# --------------------------------------------------------------------------
# Stage 2 - dataset
# --------------------------------------------------------------------------
def _snapshot_real_accounts(session: Session) -> list[dict[str, Any]]:
    """Capture the accounts a person could actually sign in with.

    ``seed_database`` opens by truncating every simulation table, and ``users``
    is on that list - the generator owns the four operator rows it creates. On a
    fresh deployment that is harmless, because the table is empty.

    It is not harmless here, and this is not hypothetical: the production
    database was bootstrapped *after* people had already signed up through the
    console, so "transactions is empty" was true while real accounts existed.
    The seed deleted them. The symptom was an account that had worked minutes
    earlier being rejected at login, with a signup for the same address
    returning 201 rather than 409 - the row was gone, not the password wrong.

    Only rows with a password hash are captured. The generator's own operator
    accounts deliberately have none, so they are left to be regenerated rather
    than accumulating a duplicate set on every reseed.
    """
    rows = session.execute(
        select(
            User.email,
            User.password_hash,
            User.full_name,
            User.role,
            User.is_active,
            User.created_at,
        ).where(User.password_hash.is_not(None))
    ).all()
    return [
        {
            "email": email,
            "password_hash": password_hash,
            "full_name": full_name,
            "role": role,
            "is_active": is_active,
            "created_at": created_at,
        }
        for email, password_hash, full_name, role, is_active, created_at in rows
    ]


def _restore_real_accounts(session: Session, saved: list[dict[str, Any]]) -> int:
    """Put back any captured account the seed removed, hash and role intact.

    Addresses the seed happened to regenerate are skipped rather than
    overwritten - restoring must not clobber a row that already exists.
    """
    if not saved:
        return 0

    present = set(session.scalars(select(User.email)))
    missing = [row for row in saved if row["email"] not in present]
    for row in missing:
        session.add(User(**row))
    if missing:
        session.flush()
    return len(missing)


def seed_dataset(transactions: int, customers: int | None = None) -> int:
    """Generate the payment universe, but only into an empty database.

    The caller has already checked that ``transactions`` is zero. That check is
    repeated here, inside the same session that does the work, because the
    generator's first act is to truncate every simulation table. A redeploy that
    raced the check and rebuilt a populated database would be a far worse
    outcome than a bootstrap that declines to run.

    Real accounts are carried across that truncation - see
    :func:`_snapshot_real_accounts` for why that is not optional.
    """
    from app.seed import SeedConfig, seed_database
    from app.seed.runner import format_summary
    from app.seed.validation import SeedValidationError

    # The generator requires at least one transaction per customer.
    customer_count = customers if customers is not None else max(100, transactions // 8)

    config = SeedConfig(transactions=transactions, customers=customer_count)

    with SessionLocal() as session:
        if _count(session, Transaction) > 0:
            logger.info("[2/7] dataset: transactions already present, refusing to reseed")
            return 0

        preserved = _snapshot_real_accounts(session)
        if preserved:
            logger.info(
                "[2/7] dataset: carrying %d sign-in-capable account(s) across the reseed",
                len(preserved),
            )
        try:
            result = seed_database(session, config)
            restored = _restore_real_accounts(session, preserved)
            if restored:
                logger.info("[2/7] dataset: restored %d account(s)", restored)
            session.commit()
        except SeedValidationError as exc:
            session.rollback()
            raise BootstrapError(f"seed validation failed: {exc}") from exc
        except Exception as exc:
            session.rollback()
            raise BootstrapError(f"seeding failed, database left unchanged: {exc}") from exc

    logger.info("[2/7] dataset:\n%s", format_summary(result))
    return result.transactions


# --------------------------------------------------------------------------
# Stages 3 and 4 - model outputs
# --------------------------------------------------------------------------
def score_fraud() -> int:
    """Phase 3 supervised scoring over every stored transaction."""
    from ml.inference.batch_predict import score_all
    from ml.inference.predictor import get_predictor

    predictor = get_predictor()
    logger.info("[3/7] predictions: scoring with model %s", predictor.model_version)
    with SessionLocal() as session:
        try:
            scored = score_all(session, predictor, None)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise BootstrapError(f"fraud scoring failed, no predictions written: {exc}") from exc
    return scored


def score_anomalies() -> int:
    """Phase 4 behavioural scoring over every stored transaction."""
    from ml.anomaly.batch_predict import score_all
    from ml.anomaly.predictor import get_anomaly_predictor

    predictor = get_anomaly_predictor()
    logger.info("[4/7] signals: scoring behaviour with model %s", predictor.model_version)
    with SessionLocal() as session:
        try:
            scored = score_all(session, predictor, None)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise BootstrapError(f"anomaly scoring failed, no signals written: {exc}") from exc
    return scored


# --------------------------------------------------------------------------
# Stage 5 - investigations
# --------------------------------------------------------------------------
def investigate_backlog(cap: int, budget_seconds: int | None = None) -> int:
    """Run Phase 5 over the backlog the policy would have wanted investigated.

    WHY THE BACKLOG NEEDS THIS AND THE SEED DID NOT

    The live pipeline runs four stages - predict, detect, investigate, decide -
    and gates the third on ``investigation_warranted``. The batch path runs only
    three. That difference is invisible until you read the policy: a block
    additionally requires a usable investigation (``require_investigation_for_
    block``), so a backfilled transaction the model scored at 0.99 downgrades to
    REVIEW for want of evidence nobody gathered. In a freshly bootstrapped
    database that made BLOCK unreachable - not because the policy declined to
    block anything, but because the batch path never gave it the corroboration
    its own rule demands.

    So this runs the same service the live pipeline runs, on the same gate, and
    hands the result to the same decision engine. It invents no evidence: an
    investigation that comes back unusable simply leaves the transaction where
    the policy's fail-safe puts it.

    Highest supervised probability first, because that is where the difference
    between REVIEW and BLOCK is actually decided, and capped because an
    investigation is the one stage that can call a language model. With the
    default ``LLM_PROVIDER=mock`` it is fast and free; against a real provider
    it is neither, which is why the cap is a knob and not a constant.

    RESUMABILITY

    This stage used to skip itself entirely when *any* investigation existed.
    That is fine on a run that completes and wrong on one that does not, and a
    long stage on a small host does not always complete: the production
    bootstrap stopped at 75 investigations, and every restart afterwards saw
    "investigations exist" and skipped straight past - so the decision stage
    behind it never ran, and the dashboard sat at 3,000 transactions and 0
    decisions indefinitely.

    Candidates that already have an investigation are now excluded by the query
    instead, so a re-run continues from where the last one stopped and a
    completed run is a no-op.

    WHY THERE IS ALSO A TIME BUDGET

    Resumability alone fixes the skip but not the wedge. This stage is the only
    one that makes network calls to a language model, so it is the only one
    whose duration is set by something outside this process; the stages behind
    it - decisions, and the operator account - are fast and are what the console
    actually needs. Left unbounded, a slow provider means the platform stops the
    run inside stage 5 again, and stage 6 is never reached no matter how neatly
    stage 5 resumes.

    So the stage yields: it stops at ``budget_seconds`` and hands what is left
    to the next run. Fewer investigations is a defined outcome - the policy
    downgrades a block it cannot corroborate to a review - while no decisions at
    all is not.
    """
    from app.services import investigation as investigation_service
    from app.services.decision import build_context
    from policy.loader import get_policy
    from policy.rules import investigation_warranted

    policy = get_policy()
    investigated = 0
    skipped = 0

    with SessionLocal() as session:
        done = _count(session, Investigation)
        remaining = cap - done
        if remaining <= 0:
            logger.info(
                "[5/7] investigations: %d already stored, at the cap of %d, skipping", done, cap
            )
            return 0

        candidates = list(
            session.scalars(
                select(Transaction)
                .join(RiskPrediction, RiskPrediction.transaction_id == Transaction.id)
                .outerjoin(Investigation, Investigation.transaction_id == Transaction.id)
                .where(Investigation.id.is_(None))
                .order_by(RiskPrediction.fraud_probability.desc())
                .limit(remaining)
            )
        )
        logger.info(
            "[5/7] investigations: %d already stored, %d further candidate(s) by supervised risk",
            done,
            len(candidates),
        )

        # Resolved here rather than in the signature so the default stays a
        # single named constant that a caller - or a test - can move.
        budget = DEFAULT_INVESTIGATION_SECONDS if budget_seconds is None else budget_seconds
        deadline = time.perf_counter() + budget
        for transaction in candidates:
            # Stop cleanly rather than being stopped. The next run resumes from
            # here - candidates already investigated are excluded by the query
            # above - and the stages behind this one still get their turn on
            # this one. An investigation stage that runs until the platform
            # kills the process is how the backlog stayed undecided.
            if time.perf_counter() >= deadline:
                logger.info(
                    "[5/7] investigations: %ds budget reached after %d; leaving the rest "
                    "for the next run so the decision stage is not starved",
                    budget,
                    investigated,
                )
                break
            try:
                # Inside the guard, not before it: assembling a context reads
                # the customer behind the transaction, and a row whose context
                # cannot be built is exactly as skippable as one whose
                # investigation fails. Outside the guard it ended the stage.
                context = build_context(session, transaction)
                if not investigation_warranted(context, policy):
                    skipped += 1
                    continue
                investigation_service.run_investigation(session, transaction)
                investigated += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not end the run
                session.rollback()
                logger.warning("investigation failed for %s: %s", transaction.transaction_id, exc)
                continue
            if investigated % 25 == 0:
                session.commit()
                logger.info("  investigated %d", investigated)
        session.commit()

    logger.info(
        "[5/7] investigations: %d run, %d candidates the policy did not want investigated",
        investigated,
        skipped,
    )
    return investigated


# --------------------------------------------------------------------------
# Stage 6 - decisions
# --------------------------------------------------------------------------
def decide_backlog() -> str:
    """Run the deterministic policy over every transaction that has signals.

    The same engine the live pipeline uses, called through the same batch path
    the test suite uses. Nothing here decides anything on its own.
    """
    from app.services.decision_batch import decide_all

    logger.info("[6/7] decisions: applying the active policy to the backlog")
    with SessionLocal() as session:
        try:
            outcome = decide_all(session, limit=None)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise BootstrapError(f"decisioning failed, no decisions written: {exc}") from exc
    return outcome.summary()


# --------------------------------------------------------------------------
# Stage 6 - accounts
# --------------------------------------------------------------------------
def _credentials(email_env: str, password_env: str) -> tuple[str, str] | None:
    """Read one account's credentials, or report why it is being skipped."""
    email = (os.environ.get(email_env) or "").strip().lower()
    password = os.environ.get(password_env) or ""

    if not email and not password:
        return None
    if not email or not password:
        logger.warning(
            "Skipping account: %s and %s must both be set, or neither.", email_env, password_env
        )
        return None
    if len(password) < MIN_PASSWORD_LENGTH:
        logger.warning(
            "Skipping %s: %s must be at least %d characters.",
            email,
            password_env,
            MIN_PASSWORD_LENGTH,
        )
        return None
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        logger.warning("Skipping %s: %s exceeds what bcrypt can hash.", email, password_env)
        return None
    return email, password


def ensure_account(session: Session, email: str, password: str, role: UserRole, name: str) -> str:
    """Create the account, or align an existing one with the configured role.

    An existing account keeps its password. Resetting it on every redeploy would
    mean anyone who ever read the platform's environment could sign in
    indefinitely, and it would silently undo a password the operator changed
    through ``manage_users.py``. Only a genuinely missing hash is filled in -
    the seed generator creates operator rows without one, and those accounts
    cannot sign in until something puts a credential on them.
    """
    user = find_user_by_email(session, email)

    if user is None:
        try:
            hashed = hash_password(password)
        except PasswordTooLongError as exc:
            raise BootstrapError(str(exc)) from exc
        session.add(
            User(
                email=email,
                full_name=name,
                role=role,
                is_active=True,
                password_hash=hashed,
            )
        )
        return f"created {email} as {role}"

    changes: list[str] = []
    if user.password_hash is None:
        user.password_hash = hash_password(password)
        changes.append("set password")
    if user.role != role:
        changes.append(f"role {user.role} -> {role}")
        user.role = role
    if not user.is_active:
        user.is_active = True
        changes.append("reactivated")

    return f"{email}: {', '.join(changes)}" if changes else f"{email} already correct"


def ensure_accounts() -> list[str]:
    """Create or align the operator accounts described by the environment."""
    wanted: list[tuple[tuple[str, str] | None, UserRole, str]] = [
        (_credentials(ADMIN_EMAIL_ENV, ADMIN_PASSWORD_ENV), UserRole.ADMIN, "Demo Operator"),
        (_credentials(VIEWER_EMAIL_ENV, VIEWER_PASSWORD_ENV), UserRole.VIEWER, "Demo Viewer"),
    ]

    messages: list[str] = []
    with SessionLocal() as session:
        for credentials, role, name in wanted:
            if credentials is None:
                continue
            email, password = credentials
            messages.append(ensure_account(session, email, password, role, name))
        session.commit()

    if not messages:
        logger.warning(
            "[7/7] accounts: no credentials in the environment. Set %s and %s to create an "
            "account that can sign in and hold simulator:control.",
            ADMIN_EMAIL_ENV,
            ADMIN_PASSWORD_ENV,
        )
    for message in messages:
        logger.info("[7/7] accounts: %s", message)
    return messages


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run(
    transactions: int,
    investigations: int,
    *,
    skip_schema: bool,
    force_decisions: bool,
    investigation_seconds: int | None = None,
) -> int:
    settings = get_settings()
    logger.info(
        "Bootstrapping RazorShield AI (environment=%s, service=%s)",
        settings.environment,
        settings.service_name,
    )

    if not skip_schema:
        upgrade_schema()
    else:
        logger.info("[1/7] schema: skipped by request")

    with SessionLocal() as session:
        counts = read_counts(session)
    logger.info("Current state: %s", counts.describe())

    if counts.transactions == 0:
        seed_dataset(transactions)
        with SessionLocal() as session:
            counts = read_counts(session)
    else:
        logger.info("[2/7] dataset: %d transactions already present, skipping", counts.transactions)

    # Every gate below compares against the transaction count rather than
    # against zero. "Some rows exist" is not the same as "this stage finished",
    # and treating the two as equivalent is what left production wedged: an
    # interrupted run had written 75 investigations, so every later start
    # skipped the stage, and the decision stage behind it never ran.
    #
    # Scoring re-runs are safe to repeat: both batch scorers replace a
    # transaction's rows rather than appending to them.
    if counts.predictions < counts.transactions:
        logger.info(
            "[3/7] predictions: %d of %d stored, scoring",
            counts.predictions,
            counts.transactions,
        )
        logger.info("[3/7] predictions: scored %d transaction(s)", score_fraud())
    else:
        logger.info("[3/7] predictions: %d complete, skipping", counts.predictions)

    # Two signals per transaction: a behavioural score and a customer-relative
    # deviation, both written together by the Phase 4 batch scorer.
    if counts.signals < counts.transactions * SIGNALS_PER_TRANSACTION:
        logger.info(
            "[4/7] signals: %d of %d stored, scoring",
            counts.signals,
            counts.transactions * SIGNALS_PER_TRANSACTION,
        )
        logger.info("[4/7] signals: scored %d transaction(s)", score_anomalies())
    else:
        logger.info("[4/7] signals: %d complete, skipping", counts.signals)

    # Stage 5 enriches; stage 6 is what the console reads. Running the second
    # after the first is deliberate and stays that way - a decision that can
    # cite an investigation is a better decision, and the policy will not block
    # without one. Letting the first *prevent* the second is a different thing
    # entirely, and it is what wedged production: an enrichment stage that talks
    # to a language model is the most likely stage to fail, and it sat in front
    # of the only stage the dashboard reads and the stage that creates the
    # operator account.
    #
    # So its failure is contained here. The traceback is logged in full because
    # a stage that silently degrades is its own incident later; the run then
    # continues, and every transaction without a usable investigation is decided
    # on the signals that do exist.
    try:
        investigate_backlog(investigations, investigation_seconds)
    except Exception:
        logger.exception(
            "[5/7] investigations: stage failed; continuing to decisions. Transactions "
            "without a usable investigation are decided on the signals that exist, which "
            "the policy handles by downgrading a block it cannot corroborate to a review."
        )

    if counts.decisions < counts.transactions or force_decisions:
        logger.info(
            "[6/7] decisions: %d of %d stored, deciding", counts.decisions, counts.transactions
        )
        logger.info("[6/7] decisions: %s", decide_backlog())
    else:
        logger.info("[6/7] decisions: %d complete, skipping", counts.decisions)

    ensure_accounts()

    with SessionLocal() as session:
        final = read_counts(session)
    logger.info("Final state: %s", final.describe())

    if final.admins == 0:
        logger.warning(
            "No administrator with a password exists. Nobody can operate the simulator until "
            "one is created - set %s/%s and re-run, or use scripts/manage_users.py.",
            ADMIN_EMAIL_ENV,
            ADMIN_PASSWORD_ENV,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--transactions",
        type=int,
        default=int(os.environ.get("BOOTSTRAP_TRANSACTIONS") or _default_transactions()),
        help=(
            "dataset size when seeding an empty database "
            f"(default {_default_transactions():,}, the same as scripts/seed_data.py)"
        ),
    )
    parser.add_argument(
        "--investigations",
        type=int,
        default=int(os.environ.get("BOOTSTRAP_INVESTIGATIONS", DEFAULT_INVESTIGATIONS)),
        help=(
            f"ceiling on Phase 5 investigations over the backlog (default {DEFAULT_INVESTIGATIONS})"
        ),
    )
    parser.add_argument(
        "--investigation-seconds",
        type=int,
        default=int(
            os.environ.get("BOOTSTRAP_INVESTIGATION_SECONDS", DEFAULT_INVESTIGATION_SECONDS)
        ),
        help=(
            "how long Phase 5 may run before handing the rest of the backlog to the "
            "next run, so the decision stage is always reached "
            f"(default {DEFAULT_INVESTIGATION_SECONDS}s)"
        ),
    )
    parser.add_argument(
        "--status", action="store_true", help="report what is present and exit without writing"
    )
    parser.add_argument(
        "--skip-schema", action="store_true", help="do not run alembic (it already ran)"
    )
    parser.add_argument(
        "--force-decisions",
        action="store_true",
        help="re-run the policy over the backlog even when decisions exist",
    )
    parser.add_argument("--log-level", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(args.log_level or settings.log_level, service=settings.service_name)

    if args.status:
        with SessionLocal() as session:
            print(read_counts(session).describe())
        return 0

    started = time.perf_counter()
    try:
        code = run(
            args.transactions,
            args.investigations,
            skip_schema=args.skip_schema,
            force_decisions=args.force_decisions,
            investigation_seconds=args.investigation_seconds,
        )
    except BootstrapError as exc:
        logger.error("Bootstrap failed: %s", exc)
        return 1
    except Exception:
        logger.exception("Bootstrap failed unexpectedly")
        return 1

    logger.info("Bootstrap finished in %.1fs", time.perf_counter() - started)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
