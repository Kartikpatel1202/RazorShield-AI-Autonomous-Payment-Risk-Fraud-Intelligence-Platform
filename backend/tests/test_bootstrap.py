"""The deployment bootstrap's stage ordering, and what survives a bad stage.

These tests exist because of a production incident the dashboard reported
honestly and nobody could explain: 3,000 transactions, 3,000 predictions, 6,000
signals, 75 investigations - and 0 decisions, indefinitely, across restarts.

The stages themselves were fine. What was wrong was the coupling between them.
Stage 5 (investigations) is the one stage that reaches a language model over the
network, and stage 6 (decisions) - the stage every dashboard aggregate is
computed from - ran strictly after it with nothing in between. Any way stage 5
could end other than by returning normally took stages 6 and 7 down with it, so
a deployment could sit at zero decisions and no operator account forever, with a
single log line as the only evidence.

The tests below pin the property that matters: an enrichment stage must not be
able to prevent a required one. They deliberately do not assert how many
investigations succeed - that is allowed to vary - only that the backlog gets
decided either way.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.bootstrap as bootstrap
from app.models import (
    Base,
    Investigation,
    ReviewCase,
    RiskDecision,
    RiskPrediction,
    RiskSignal,
    Transaction,
)
from app.models.enums import InvestigationStatus, SignalSeverity
from app.seed import SeedConfig, seed_database
from app.services.anomaly import ANOMALY_SIGNAL

# The same shape as the production backlog - every transaction scored, a handful
# investigated, nothing decided - at the smallest size the seed's own validator
# accepts. Shrinking it further trips the fraud-prevalence and shared-IP checks,
# which exist to stop a dataset that cannot exercise the policy.
_CONFIG = SeedConfig(
    random_seed=4242,
    merchants=3,
    customers=120,
    ip_addresses=150,
    transactions=1_200,
    history_days=30,
    reference_time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
)

#: Transactions carrying an investigation when the run starts. Production had 75
#: of 3,000 - an interrupted stage, not a finished one.
_PARTIAL_INVESTIGATIONS = 5


@pytest.fixture()
def bootstrapped(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """A database in the state production was wedged in.

    Scored end to end, partially investigated, entirely undecided. ``bootstrap``
    is pointed at it by replacing the module-level session factory, which is how
    the script reaches a database in a deployment too.
    """
    engine: Engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

    with factory() as session:
        seed_database(session, _CONFIG)
        transactions = list(session.scalars(select(Transaction).order_by(Transaction.id)))
        for index, transaction in enumerate(transactions):
            # A spread of probabilities, so the policy produces a mix of actions
            # rather than filling one bucket.
            probability = (index % 100) / 100.0
            session.add(
                RiskPrediction(
                    transaction_id=transaction.id,
                    fraud_probability=Decimal(str(probability)).quantize(Decimal("0.00001")),
                    risk_score=int(round(probability * 100)),
                    model_version="xgboost-test",
                )
            )
            session.add(
                RiskSignal(
                    transaction_id=transaction.id,
                    signal_name=ANOMALY_SIGNAL,
                    signal_value=Decimal(index % 101),
                    severity=SignalSeverity.LOW,
                    source="isolation-forest-test",
                )
            )
        for transaction in transactions[:_PARTIAL_INVESTIGATIONS]:
            session.add(
                Investigation(
                    transaction_id=transaction.id,
                    status=InvestigationStatus.COMPLETED,
                    confidence=Decimal("0.8000"),
                    public_id=f"INV-{transaction.id:08d}",
                    report={},
                )
            )
        session.commit()

    monkeypatch.setattr(bootstrap, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _counts(factory: sessionmaker[Session]) -> tuple[int, int, int]:
    """Investigations, decisions and review cases currently stored."""
    with factory() as session:
        return (
            int(session.scalar(select(func.count()).select_from(Investigation)) or 0),
            int(session.scalar(select(func.count()).select_from(RiskDecision)) or 0),
            int(session.scalar(select(func.count()).select_from(ReviewCase)) or 0),
        )


def test_partially_investigated_backlog_still_gets_decided(
    bootstrapped: sessionmaker[Session],
) -> None:
    """A resumed run reaches stage 6 and the dashboard fills.

    This is the state the earlier resumability fix addressed - investigations
    present but incomplete - and it has to keep working.
    """
    bootstrap.run(transactions=0, investigations=20, skip_schema=True, force_decisions=False)

    _, decisions, _ = _counts(bootstrapped)
    assert decisions > 0, "stage 6 must decide the backlog once stage 5 has had its turn"


def test_decisions_are_written_even_when_the_investigation_stage_fails(
    bootstrapped: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage 5 that raises must not take stage 6 with it.

    The language model is the one dependency here that is expected to be
    unavailable sometimes. When it is, the policy already has a defined answer -
    a block without corroboration downgrades to review - so the right outcome is
    decisions of slightly lower confidence, never no decisions at all.
    """

    def _explode(_cap: int) -> int:
        raise RuntimeError("language model unreachable")

    monkeypatch.setattr(bootstrap, "investigate_backlog", _explode)

    bootstrap.run(transactions=0, investigations=20, skip_schema=True, force_decisions=False)

    _, decisions, _ = _counts(bootstrapped)
    assert decisions > 0, (
        "a failed investigation stage must not stop the backlog being decided - "
        "this is the production wedge: investigations present, 0 decisions"
    )


def test_one_unusable_row_does_not_abort_the_investigation_stage(
    bootstrapped: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assembling a context can fail per row; the stage must survive it.

    ``build_context`` dereferences ``transaction.customer``. A single row whose
    context cannot be built used to end the whole stage - and with it the whole
    bootstrap - instead of being skipped the way a failed investigation is.
    """
    from app.services import decision as decision_service

    real_build = decision_service.build_context
    calls = {"n": 0}

    def _sometimes_explode(session: Session, transaction: Transaction) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("customer row is missing")
        return real_build(session, transaction)

    monkeypatch.setattr(decision_service, "build_context", _sometimes_explode)

    bootstrap.run(transactions=0, investigations=20, skip_schema=True, force_decisions=False)

    _, decisions, _ = _counts(bootstrapped)
    assert calls["n"] > 0, "the stage must actually have tried to build a context"
    assert decisions > 0, "one unusable row must not prevent every decision"


def test_investigation_stage_yields_when_its_time_budget_is_spent(
    bootstrapped: sessionmaker[Session],
) -> None:
    """A slow provider costs investigations, never decisions.

    The budget is what makes the stage stop *itself*. Without it the platform
    stops it instead, at a point of the platform's choosing, which is always
    before the stage behind it has run.
    """
    investigated = bootstrap.investigate_backlog(cap=200, budget_seconds=0)

    assert investigated == 0, "a spent budget must stop the stage before it starts work"

    stored_investigations, _, _ = _counts(bootstrapped)
    assert stored_investigations == _PARTIAL_INVESTIGATIONS, (
        "yielding must leave the backlog untouched for the next run, not consume it"
    )


def test_a_slow_investigation_stage_still_leaves_time_to_decide(
    bootstrapped: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end shape of the production failure, with the clock as cause.

    Nothing raises here. The stage is simply slower than the window it is given,
    which is precisely the case resumability alone does not cover.
    """
    monkeypatch.setattr(bootstrap, "DEFAULT_INVESTIGATION_SECONDS", 0)

    bootstrap.run(transactions=0, investigations=200, skip_schema=True, force_decisions=False)

    _, decisions, reviews = _counts(bootstrapped)
    assert decisions > 0, "the decision stage must still run when stage 5 runs out of time"
    assert reviews > 0


def test_review_cases_are_opened_by_the_decision_stage(
    bootstrapped: sessionmaker[Session],
) -> None:
    """Reviews are a product of deciding, not a stage of their own.

    Zero reviews alongside zero decisions is therefore one fault and not two:
    the queue is opened by the policy when it asks for a human, so nothing can
    appear in it until stage 6 has run.
    """
    _, decisions_before, reviews_before = _counts(bootstrapped)
    assert decisions_before == 0
    assert reviews_before == 0

    bootstrap.run(transactions=0, investigations=20, skip_schema=True, force_decisions=False)

    _, decisions, reviews = _counts(bootstrapped)
    assert decisions > 0
    assert reviews > 0, "the policy routes part of this spread to a human"
