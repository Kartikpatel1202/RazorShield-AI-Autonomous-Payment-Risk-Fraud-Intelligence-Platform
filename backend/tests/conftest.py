"""Shared pytest fixtures.

Functional tests run against an isolated in-memory SQLite database seeded with a
small version of the real dataset. The simulation database is never touched:
nothing here connects to ``DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import create_app
from app.models import Base
from app.seed import SeedConfig, seed_database

# Small but structurally complete: enough customers for device/IP sharing to
# appear, and all three demo scenarios. Timestamps are pinned so every run
# produces byte-identical data.
TEST_SEED_CONFIG = SeedConfig(
    random_seed=4242,
    merchants=3,
    customers=120,
    ip_addresses=150,
    transactions=1_200,
    history_days=30,
    reference_time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
)


@pytest.fixture(scope="session")
def app() -> FastAPI:
    """The FastAPI application under test."""
    return create_app()


def _enable_sqlite_transactions(engine: Engine) -> None:
    """Make SQLite honour explicit transactions and SAVEPOINTs.

    pysqlite manages transactions itself and never emits a real ``BEGIN``, which
    silently breaks the savepoint-based isolation the ``db_session`` fixture
    relies on: a ``commit()`` inside a request would escape the outer rollback
    and leak rows into later tests. This is SQLAlchemy's documented workaround.
    """

    @event.listens_for(engine, "connect")
    def _disable_implicit_begin(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _emit_begin(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def seeded_engine() -> Generator[Engine, None, None]:
    """An in-memory database holding one full seed run.

    Built once per test session: seeding is the expensive part, and every test
    is isolated by a transaction rollback rather than by reseeding.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _enable_sqlite_transactions(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session, TEST_SEED_CONFIG)
        session.commit()

    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(seeded_engine: Engine) -> Generator[Session, None, None]:
    """A session whose writes are rolled back when the test finishes."""
    connection = seeded_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --- Phase 10 authentication fixtures ---------------------------------------
#
# Every /api endpoint is behind a permission check from Phase 10 onward. Rather
# than override the auth dependency - which would mean 900 tests exercising a
# stub instead of the real thing - the fixtures below create real accounts, mint
# real signed tokens through the real code path, and send them as a real
# Authorization header. Auth is therefore covered incidentally by the whole
# suite, and `test_security_auth` covers it deliberately.

#: Passwords used only inside the test process. They are never written to the
#: simulation database and never leave this file.
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def auth_users(db_session: Session) -> dict[str, object]:
    """One usable account per role, rolled back with the enclosing test."""
    from app.core.security import hash_password
    from app.models import User
    from app.models.enums import UserRole

    hashed = hash_password(TEST_PASSWORD)
    users = {
        "admin": User(
            email="admin@test.invalid",
            full_name="Test Admin",
            role=UserRole.ADMIN,
            is_active=True,
            password_hash=hashed,
        ),
        "analyst": User(
            email="analyst@test.invalid",
            full_name="Test Analyst",
            role=UserRole.RISK_ANALYST,
            is_active=True,
            password_hash=hashed,
        ),
        "viewer": User(
            email="viewer@test.invalid",
            full_name="Test Viewer",
            role=UserRole.VIEWER,
            is_active=True,
            password_hash=hashed,
        ),
        "merchant": User(
            email="merchant@test.invalid",
            full_name="Test Merchant",
            role=UserRole.MERCHANT,
            is_active=True,
            password_hash=hashed,
        ),
        "disabled": User(
            email="disabled@test.invalid",
            full_name="Disabled Admin",
            role=UserRole.ADMIN,
            is_active=False,
            password_hash=hashed,
        ),
    }
    db_session.add_all(list(users.values()))
    db_session.flush()
    return dict(users)


def token_for(user: object) -> str:
    """Mint a real access token for ``user`` using the production minter."""
    from app.core.security import create_access_token

    issued = create_access_token(
        user_id=user.id,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        role=str(user.role),  # type: ignore[attr-defined]
    )
    return issued.token


def authorize(test_client: TestClient, user: object) -> TestClient:
    """Attach ``user``'s bearer token to every request the client makes."""
    test_client.headers["Authorization"] = f"Bearer {token_for(user)}"
    return test_client


@pytest.fixture(autouse=True)
def _isolated_process_state() -> Generator[None, None, None]:
    """Reset the process-wide limiter and metric registry around each test.

    Both are module-level singletons by design - the middleware, the CLI scripts
    and the tests must all see the same object - which makes them shared state
    between tests. Resetting here is what keeps a rate-limit test from failing
    because an earlier test happened to spend the budget.

    The limiter is disabled by default: an ordinary functional test that loops
    over twenty transactions is not trying to test rate limiting, and should not
    trip it. `rate_limited_client` turns it back on for the tests that are.
    """
    from app.core.metrics import reset_metrics
    from app.core.ratelimit import limiter

    limiter.enabled = False
    limiter.reset()
    reset_metrics()
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture()
def anonymous_client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    """Test client that presents no credential at all.

    It still depends on ``auth_users`` so the accounts exist: these tests need
    real credentials to *not* use, and to hand to ``/auth/login`` explicitly.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    """Test client wired to the seeded session, authenticated as an admin."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["admin"])
    app.dependency_overrides.clear()


@pytest.fixture()
def analyst_client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["analyst"])
    app.dependency_overrides.clear()


@pytest.fixture()
def viewer_client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["viewer"])
    app.dependency_overrides.clear()


@pytest.fixture()
def merchant_client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["merchant"])
    app.dependency_overrides.clear()


@pytest.fixture()
def rate_limited_client(
    app: FastAPI, db_session: Session, auth_users: dict[str, object]
) -> Generator[TestClient, None, None]:
    """An authenticated client with the rate limiter switched on."""
    from app.core.ratelimit import limiter

    limiter.enabled = True
    limiter.reset()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["admin"])
    app.dependency_overrides.clear()
    limiter.enabled = False
    limiter.reset()


@pytest.fixture()
def seed_config() -> SeedConfig:
    return TEST_SEED_CONFIG


# --- ML fixtures ------------------------------------------------------------
#
# A model is trained once per session from the seeded test database. The tests
# never touch the artifact produced by a real `python -m ml.training.train` run,
# so they neither depend on it nor overwrite it.

TEST_MODEL_PARAMS = {
    "n_estimators": 120,
    "max_depth": 4,
    "learning_rate": 0.1,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "min_child_weight": 1,
    "reg_lambda": 1.0,
}


@pytest.fixture(scope="session")
def ml_dataset(seeded_engine: Engine) -> pd.DataFrame:
    """The point-in-time feature matrix for the seeded test database."""
    from ml.training.build_dataset import build_dataframe

    with Session(seeded_engine) as session:
        return build_dataframe(session)


@pytest.fixture(scope="session")
def trained_model_path(ml_dataset: pd.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Train a small model on the test dataset and return its artifact path."""
    import joblib

    from ml.features.schema import TARGET_COLUMN
    from ml.training.pipelines import build_primary, features_of, positive_class_weight
    from ml.training.settings import load_config
    from ml.training.split import split_chronologically
    from ml.training.train import build_artifact

    config = load_config()
    split = split_chronologically(ml_dataset, config.split)
    labels = split.train.frame[TARGET_COLUMN]

    model = build_primary(
        config.primary.fixed_params,
        TEST_MODEL_PARAMS,
        positive_class_weight(labels),
        config.random_seed,
    )
    model.fit(features_of(split.train.frame), labels)

    artifact_path = tmp_path_factory.mktemp("models") / "test_model.joblib"
    joblib.dump(
        build_artifact(
            model,
            model_version="xgboost-test",
            model_name="xgboost",
            config=config,
            threshold=0.5,
            training_rows=split.train.rows,
            params=TEST_MODEL_PARAMS,
            calibrated=False,
            calibration_method=None,
        ),
        artifact_path,
    )
    return artifact_path


@pytest.fixture()
def predictor(trained_model_path: Path):  # noqa: ANN201 - FraudRiskPredictor
    """A predictor loaded from the session-scoped test artifact."""
    from ml.inference.predictor import FraudRiskPredictor

    return FraudRiskPredictor.load(trained_model_path)


@pytest.fixture()
def risk_client(
    app: FastAPI,
    db_session: Session,
    trained_model_path: Path,
    auth_users: dict[str, object],
):  # noqa: ANN201
    """Test client whose risk endpoint uses the test model."""
    from ml.inference.predictor import get_predictor, reset_predictor

    reset_predictor()
    get_predictor(trained_model_path)  # primes the process-wide cache

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["admin"])
    app.dependency_overrides.clear()
    reset_predictor()


# --- Phase 4 anomaly fixtures ----------------------------------------------


@pytest.fixture(scope="session")
def anomaly_model_path(ml_dataset: pd.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fit a small Isolation Forest on the test dataset and return its artifact.

    Uses the production fitting path so the tests exercise real code, and writes
    to a temp file so a real `python -m ml.anomaly.train` artifact is never read
    or overwritten.
    """
    import joblib

    from ml.anomaly.scoring import SeverityBands
    from ml.anomaly.settings import load_anomaly_config
    from ml.anomaly.train import (
        anomaly_scores_for,
        build_artifact,
        build_fitting_population,
        fit_model,
    )
    from ml.features.schema import TARGET_COLUMN
    from ml.training.evaluation import select_threshold
    from ml.training.settings import ThresholdConfig
    from ml.training.split import split_chronologically

    config = load_anomaly_config()
    split = split_chronologically(ml_dataset, config.split)
    population = build_fitting_population(split.train)

    model, normalizer, customer_relative, grids, _ = fit_model(config, population)

    validation_scores = anomaly_scores_for(model, normalizer, split.validation.frame)
    bands = SeverityBands.from_scores(validation_scores, config.severity_percentiles)
    choice = select_threshold(
        np.asarray(split.validation.frame[TARGET_COLUMN].to_numpy(), dtype=int),
        validation_scores,
        ThresholdConfig(
            objective=config.threshold.objective,
            beta=config.threshold.beta,
            minimum_precision=config.threshold.minimum_precision,
        ),
    )

    artifact_path = tmp_path_factory.mktemp("anomaly") / "test_forest.joblib"
    joblib.dump(
        build_artifact(
            model,
            normalizer,
            customer_relative,
            grids,
            bands,
            config,
            choice.threshold,
            len(population),
        ),
        artifact_path,
    )
    return artifact_path


@pytest.fixture()
def anomaly_predictor(anomaly_model_path: Path):  # noqa: ANN201 - BehavioralAnomalyPredictor
    from ml.anomaly.predictor import BehavioralAnomalyPredictor

    return BehavioralAnomalyPredictor.load(anomaly_model_path)


@pytest.fixture()
def anomaly_client(
    app: FastAPI,
    db_session: Session,
    anomaly_model_path: Path,
    auth_users: dict[str, object],
):  # noqa: ANN201
    """Test client whose anomaly endpoint uses the test forest."""
    from ml.anomaly.predictor import get_anomaly_predictor, reset_anomaly_predictor

    reset_anomaly_predictor()
    get_anomaly_predictor(anomaly_model_path)

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["admin"])
    app.dependency_overrides.clear()
    reset_anomaly_predictor()


# --- Phase 5 investigation agent fixtures -----------------------------------


@pytest.fixture()
def mock_provider():  # noqa: ANN201 - MockLLMProvider
    """A deterministic mock LLM. Never reaches a real provider."""
    from agent.llm.mock import MockLLMProvider

    return MockLLMProvider()


@pytest.fixture()
def investigation_agent(mock_provider):  # noqa: ANN001, ANN201
    """An agent wired to the deterministic mock."""
    from agent.graph.executor import InvestigationAgent

    return InvestigationAgent(provider=mock_provider, max_iterations=8)


@pytest.fixture()
def tool_context(db_session: Session):  # noqa: ANN201 - ToolContext factory
    """Builds a ToolContext for a transaction reference."""
    from sqlalchemy import select as _select

    from agent.tools.base import ToolContext
    from app.models import Transaction as _Transaction

    def _build(reference: str) -> ToolContext:
        transaction = db_session.scalars(
            _select(_Transaction).where(_Transaction.transaction_id == reference)
        ).one()
        return ToolContext.build(db_session, transaction)

    return _build


@pytest.fixture()
def agent_client(
    app: FastAPI,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    auth_users: dict[str, object],
):  # noqa: ANN201
    """Test client whose investigation endpoint uses the deterministic mock."""
    from agent.config import AgentSettings, LLMProviderName, get_agent_settings

    get_agent_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", LLMProviderName.MOCK.value)
    monkeypatch.setenv("AGENT_MOCK_BEHAVIOUR", "normal")

    # Settings read a .env file by default; force the test values to win.
    monkeypatch.setattr(
        "agent.config.get_agent_settings",
        lambda: AgentSettings(llm_provider=LLMProviderName.MOCK, agent_mock_behaviour="normal"),
    )
    monkeypatch.setattr(
        "agent.investigator.get_agent_settings",
        lambda: AgentSettings(llm_provider=LLMProviderName.MOCK, agent_mock_behaviour="normal"),
    )

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield authorize(test_client, auth_users["admin"])
    app.dependency_overrides.clear()
    get_agent_settings.cache_clear()


# --- Phase 7 analytics fixtures ---------------------------------------------

from decimal import Decimal  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models import RiskPrediction, RiskSignal, Transaction  # noqa: E402
from app.models.enums import SignalSeverity  # noqa: E402
from app.services.anomaly import ANOMALY_SIGNAL  # noqa: E402
from app.services.decision_batch import decide_all  # noqa: E402

#: Probabilities placed deliberately across every policy band, including the
#: exact threshold values, so the aggregates under test have known answers
#: rather than whatever the model happened to output.
_PROBABILITY_CYCLE = (
    0.0,
    0.01,
    0.14,
    0.15,
    0.30,
    0.533209,
    0.60,
    0.89,
    0.90,
    0.999,
    1.0,
)
_ANOMALY_CYCLE = (
    (5, SignalSeverity.LOW),
    (60, SignalSeverity.LOW),
    (93, SignalSeverity.MEDIUM),
    (98, SignalSeverity.HIGH),
    (100, SignalSeverity.CRITICAL),
)


@pytest.fixture()
def scored(db_session: Session) -> int:
    """Give every seeded transaction a prediction and an anomaly signal.

    Phase 2's seed deliberately leaves ``risk_predictions`` and ``risk_signals``
    empty, so without this the analytics assertions would compare zero against
    zero and pass on any implementation. The values cycle through every band and
    sit exactly on the thresholds, which is where bucketing bugs live.
    """
    transactions = list(db_session.scalars(select(Transaction).order_by(Transaction.id)))
    for index, transaction in enumerate(transactions):
        probability = _PROBABILITY_CYCLE[index % len(_PROBABILITY_CYCLE)]
        score, severity = _ANOMALY_CYCLE[index % len(_ANOMALY_CYCLE)]
        db_session.add(
            RiskPrediction(
                transaction_id=transaction.id,
                fraud_probability=Decimal(str(probability)).quantize(Decimal("0.00001")),
                risk_score=int(round(probability * 100)),
                model_version="xgboost-test",
            )
        )
        db_session.add(
            RiskSignal(
                transaction_id=transaction.id,
                signal_name=ANOMALY_SIGNAL,
                signal_value=Decimal(score),
                severity=severity,
                source="isolation-forest-test",
            )
        )
    db_session.flush()
    return len(transactions)


@pytest.fixture()
def decided(db_session: Session, scored: int) -> int:
    """Decide every seeded transaction once, so aggregates have data.

    Uses the batch path - the same pure engine the API uses - rather than
    fabricating decision rows, so the distributions under test are the ones the
    policy actually produces.
    """
    outcome = decide_all(db_session, limit=None)
    db_session.flush()
    return outcome.decided
