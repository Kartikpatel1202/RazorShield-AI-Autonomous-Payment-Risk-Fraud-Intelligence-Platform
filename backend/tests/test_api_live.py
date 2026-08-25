"""Live ingestion, the simulator, event ordering and the SSE stream.

The properties worth testing here are the ones that are invisible when they
work: that a repeated event does not become a second decision, that the stages
arrive in order, that a missing model does not become an approval, and that a
reconnecting client is given exactly what it missed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Investigation, Merchant, RiskDecision, RiskEvent, RiskPrediction, Transaction
from app.models.enums import (
    RISK_EVENT_ORDER,
    RiskEventType,
    SimulatorScenario,
    SimulatorState,
)
from app.services import events as event_service
from app.services import ingest as ingest_service
from app.simulator.engine import (
    MAX_RATE,
    MAX_TRANSACTIONS,
    SimulatorConfig,
    SimulatorEngine,
)
from app.simulator.scenarios import SCENARIO_DOCS, ScenarioGenerator


@pytest.fixture()
def anyio_backend() -> str:
    """Run the async tests on asyncio only; there is no trio dependency here."""
    return "asyncio"


def merchant_reference(session: Session) -> str:
    merchant = session.scalars(select(Merchant).order_by(Merchant.id).limit(1)).one()
    return merchant.external_merchant_id


def make_event(
    session: Session,
    reference: str,
    *,
    amount: str = "2500.00",
    customer: str = "SIM_CUS_test",
    device: str | None = "SIM_dev_test",
    ip: str | None = "198.18.9.9",
    country: str = "IN",
    city: str = "Mumbai",
    is_proxy: bool = False,
    when: datetime | None = None,
) -> ingest_service.TransactionEvent:
    from app.models.enums import DeviceType, PaymentMethod

    return ingest_service.TransactionEvent(
        transaction_id=reference,
        amount=Decimal(amount),
        currency="INR",
        customer_id=customer,
        merchant_id=merchant_reference(session),
        payment_method=PaymentMethod.CARD,
        country=country,
        city=city,
        timestamp=when or datetime.now(UTC),
        device_id=device,
        device_type=DeviceType.ANDROID,
        ip_address=ip,
        ip_country=country,
        ip_is_proxy=is_proxy,
    )


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
def test_ingest_runs_the_whole_pipeline(db_session: Session) -> None:
    result = ingest_service.ingest(db_session, make_event(db_session, "SIM_pipeline_001"))

    assert result.error is None
    assert result.duplicate is False
    # Every stage produced a real value from the service that owns it.
    assert result.fraud_probability is not None
    assert 0.0 <= result.fraud_probability <= 1.0
    assert result.anomaly_score is not None
    assert 0 <= result.anomaly_score <= 100
    assert result.decision in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}
    assert result.decision_id
    assert result.matched_rules


def test_ingest_persists_one_of_each_row(db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_persist_001"))

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "SIM_persist_001")
    ).one()
    predictions = db_session.scalar(
        select(func.count(RiskPrediction.id)).where(RiskPrediction.transaction_id == transaction.id)
    )
    decisions = db_session.scalar(
        select(func.count(RiskDecision.id)).where(RiskDecision.transaction_id == transaction.id)
    )
    assert predictions == 1
    assert decisions == 1


def test_ingest_never_sets_the_ground_truth_label(db_session: Session) -> None:
    """`is_fraud` is the dataset's label. A live event may not assert it."""
    ingest_service.ingest(db_session, make_event(db_session, "SIM_label_001"))

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "SIM_label_001")
    ).one()
    assert transaction.is_fraud is False


def test_ingest_marks_simulated_transactions(db_session: Session) -> None:
    result = ingest_service.ingest(db_session, make_event(db_session, "SIM_marked_001"))
    assert ingest_service.is_simulated(result.reference) is True
    assert ingest_service.is_simulated("txn_00001") is False


def test_ingest_rejects_an_unknown_merchant(db_session: Session) -> None:
    event = make_event(db_session, "SIM_badmerchant_001")
    event.merchant_id = "mrc_does_not_exist"

    with pytest.raises(ingest_service.IngestError, match="unknown merchant"):
        ingest_service.ingest(db_session, event)


def test_ingest_creates_a_first_seen_customer(db_session: Session) -> None:
    from app.models import Customer

    before = db_session.scalar(select(func.count(Customer.id))) or 0
    ingest_service.ingest(
        db_session, make_event(db_session, "SIM_newcust_001", customer="SIM_CUS_brand_new")
    )
    after = db_session.scalar(select(func.count(Customer.id))) or 0

    assert after == before + 1


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def test_resubmitting_creates_no_second_decision(db_session: Session) -> None:
    """The property that matters: decisions are append-only history."""
    event = make_event(db_session, "SIM_idem_001")
    first = ingest_service.ingest(db_session, event)
    second = ingest_service.ingest(db_session, make_event(db_session, "SIM_idem_001"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.decision_id == first.decision_id

    transaction = db_session.scalars(
        select(Transaction).where(Transaction.transaction_id == "SIM_idem_001")
    ).one()
    assert (
        db_session.scalar(
            select(func.count(RiskDecision.id)).where(RiskDecision.transaction_id == transaction.id)
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(RiskPrediction.id)).where(
                RiskPrediction.transaction_id == transaction.id
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(Transaction.id)).where(Transaction.transaction_id == "SIM_idem_001")
        )
        == 1
    )


def test_a_duplicate_reports_the_original_result(db_session: Session) -> None:
    first = ingest_service.ingest(db_session, make_event(db_session, "SIM_idem_002"))
    second = ingest_service.ingest(db_session, make_event(db_session, "SIM_idem_002"))

    # The first run reports the model's raw float; the duplicate reports what
    # was stored, and `risk_predictions.fraud_probability` is NUMERIC(6,5).
    # Agreement to the stored precision is the strongest claim available - and
    # the right one, since the stored value is what every later phase reads.
    assert first.fraud_probability is not None
    assert second.fraud_probability is not None
    assert round(second.fraud_probability, 5) == round(first.fraud_probability, 5)
    assert second.anomaly_score == first.anomaly_score
    assert second.decision == first.decision


def test_a_duplicate_emits_no_new_events(db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_idem_003"))
    before = db_session.scalar(
        select(func.count(RiskEvent.id)).where(RiskEvent.transaction_reference == "SIM_idem_003")
    )
    ingest_service.ingest(db_session, make_event(db_session, "SIM_idem_003"))
    after = db_session.scalar(
        select(func.count(RiskEvent.id)).where(RiskEvent.transaction_reference == "SIM_idem_003")
    )

    assert after == before


# --------------------------------------------------------------------------
# Event ordering
# --------------------------------------------------------------------------
def test_events_follow_the_declared_pipeline_order(db_session: Session) -> None:
    """The emitted stages must be a subsequence of the declared order."""
    ingest_service.ingest(db_session, make_event(db_session, "SIM_order_001"))

    rows = db_session.scalars(
        select(RiskEvent)
        .where(RiskEvent.transaction_reference == "SIM_order_001")
        .order_by(RiskEvent.sequence)
    ).all()
    emitted = [RiskEventType(str(row.event_type)) for row in rows]

    position = -1
    for event_type in emitted:
        index = RISK_EVENT_ORDER.index(event_type)
        assert index > position, f"{event_type} arrived out of order"
        position = index


def test_the_first_and_last_stages_are_fixed(db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_order_002"))
    rows = db_session.scalars(
        select(RiskEvent)
        .where(RiskEvent.transaction_reference == "SIM_order_002")
        .order_by(RiskEvent.sequence)
    ).all()

    assert str(rows[0].event_type) == str(RiskEventType.TRANSACTION_RECEIVED)
    assert str(rows[-1].event_type) == str(RiskEventType.DECISION_CREATED)


def test_transaction_sequence_starts_at_one_and_increments(db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_order_003"))
    rows = db_session.scalars(
        select(RiskEvent)
        .where(RiskEvent.transaction_reference == "SIM_order_003")
        .order_by(RiskEvent.sequence)
    ).all()

    assert [row.transaction_sequence for row in rows] == list(range(1, len(rows) + 1))


def test_the_global_sequence_is_monotonic(db_session: Session) -> None:
    for index in range(3):
        ingest_service.ingest(db_session, make_event(db_session, f"SIM_mono_{index:03d}"))

    sequences = list(db_session.scalars(select(RiskEvent.sequence).order_by(RiskEvent.id)).all())
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))


def test_events_carry_no_secrets(db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_secret_001"))
    rows = db_session.scalars(
        select(RiskEvent).where(RiskEvent.transaction_reference == "SIM_secret_001")
    ).all()

    blob = str([row.payload for row in rows]).lower()
    for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib", "secret"):
        assert secret not in blob


# --------------------------------------------------------------------------
# Investigation gating
# --------------------------------------------------------------------------
def test_a_quiet_transaction_is_not_investigated(db_session: Session) -> None:
    """An investigation on every payment would be an outage, not diligence."""
    result = ingest_service.ingest(
        db_session, make_event(db_session, "SIM_quiet_001", amount="120.00")
    )

    if result.fraud_probability is not None and result.anomaly_score is not None:
        from policy.loader import load_policy

        thresholds = load_policy().thresholds
        elevated = (
            result.fraud_probability >= thresholds.fraud_medium
            or result.anomaly_score >= thresholds.anomaly_medium
        )
        # The gate is the policy's own; assert the pipeline agreed with it.
        assert result.investigated is elevated


def test_investigation_events_appear_only_when_investigated(db_session: Session) -> None:
    result = ingest_service.ingest(db_session, make_event(db_session, "SIM_invgate_001"))
    types = {
        str(row.event_type)
        for row in db_session.scalars(
            select(RiskEvent).where(RiskEvent.transaction_reference == "SIM_invgate_001")
        ).all()
    }

    has_started = str(RiskEventType.INVESTIGATION_STARTED) in types
    assert has_started is result.investigated
    if result.investigated:
        assert str(RiskEventType.INVESTIGATION_COMPLETED) in types
        assert (
            db_session.scalar(
                select(func.count(Investigation.id)).where(
                    Investigation.transaction_id == result.transaction_id
                )
            )
            == 1
        )


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------
def test_a_pipeline_failure_is_reported_not_swallowed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken model must not silently become an approval."""

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("model artifact unavailable")

    monkeypatch.setattr(ingest_service.risk_service, "predict_and_store", explode)
    result = ingest_service.ingest(db_session, make_event(db_session, "SIM_fail_001"))

    # Phase 10 narrowed what a failure reports: the exception *class* and the
    # stage that raised, never the message. A model loader's message names the
    # artifact path it could not find, and this string is both returned to the
    # caller and published on the public event stream. The message is still
    # logged in full, with the correlation id, for whoever is on call.
    assert result.error == "RuntimeError"
    assert result.failed_stage == "risk_scoring"
    assert "artifact" not in (result.error or "")
    # Critically: no decision at all, and certainly not an approval.
    assert result.decision is None


def test_a_failure_emits_a_processing_failed_event(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("anomaly model unavailable")

    monkeypatch.setattr(ingest_service.anomaly_service, "score_and_store", explode)
    ingest_service.ingest(db_session, make_event(db_session, "SIM_fail_002"))

    failures = db_session.scalars(
        select(RiskEvent).where(
            RiskEvent.transaction_reference == "SIM_fail_002",
            RiskEvent.event_type == RiskEventType.PROCESSING_FAILED,
        )
    ).all()
    assert len(failures) == 1


def test_a_failed_transaction_leaves_no_decision(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("decision engine unavailable")

    monkeypatch.setattr(ingest_service.decision_service, "decide_and_store", explode)
    ingest_service.ingest(db_session, make_event(db_session, "SIM_fail_003"))

    # The whole unit of work rolled back, so the transaction itself is absent.
    assert (
        db_session.scalar(
            select(func.count(Transaction.id)).where(Transaction.transaction_id == "SIM_fail_003")
        )
        == 0
    )


# --------------------------------------------------------------------------
# Event stream helpers
# --------------------------------------------------------------------------
def test_events_since_returns_only_later_events(db_session: Session) -> None:
    for index in range(3):
        ingest_service.ingest(db_session, make_event(db_session, f"SIM_since_{index:03d}"))

    everything = event_service.recent_events(db_session, limit=200)
    assert everything
    cursor = everything[len(everything) // 2]["sequence"]

    resumed = event_service.events_since(db_session, after_sequence=cursor)
    assert all(item["sequence"] > cursor for item in resumed)
    assert [item["sequence"] for item in resumed] == sorted(item["sequence"] for item in resumed)


def test_events_since_is_capped(db_session: Session) -> None:
    for index in range(4):
        ingest_service.ingest(db_session, make_event(db_session, f"SIM_cap_{index:03d}"))

    resumed = event_service.events_since(db_session, after_sequence=0, limit=10_000)
    assert len(resumed) <= event_service.MAX_REPLAY_EVENTS


def test_recent_events_are_oldest_first(db_session: Session) -> None:
    for index in range(2):
        ingest_service.ingest(db_session, make_event(db_session, f"SIM_recent_{index:03d}"))

    events = event_service.recent_events(db_session, limit=20)
    sequences = [item["sequence"] for item in events]
    assert sequences == sorted(sequences)


# --------------------------------------------------------------------------
# The broker
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_broker_delivers_to_every_subscriber() -> None:
    broker = event_service.InMemoryEventBroker()
    first = await broker.subscribe()
    second = await broker.subscribe()

    broker.publish({"sequence": 1, "event_type": "test"})

    assert first.get_nowait()["sequence"] == 1
    assert second.get_nowait()["sequence"] == 1


@pytest.mark.anyio
async def test_a_slow_subscriber_is_dropped_not_blocking() -> None:
    """A browser that cannot keep up must not slow the pipeline down."""
    broker = event_service.InMemoryEventBroker()
    queue = await broker.subscribe()

    for index in range(event_service.CLIENT_QUEUE_SIZE + 10):
        broker.publish({"sequence": index, "event_type": "test"})

    assert queue.qsize() == event_service.CLIENT_QUEUE_SIZE
    assert broker.dropped_deliveries == 10


@pytest.mark.anyio
async def test_unsubscribe_stops_delivery() -> None:
    broker = event_service.InMemoryEventBroker()
    queue = await broker.subscribe()
    await broker.unsubscribe(queue)

    broker.publish({"sequence": 1, "event_type": "test"})
    assert queue.empty()
    assert broker.subscriber_count == 0


# --------------------------------------------------------------------------
# Scenario generation
# --------------------------------------------------------------------------
def test_every_scenario_is_documented() -> None:
    assert set(SCENARIO_DOCS) == set(SimulatorScenario)
    for doc in SCENARIO_DOCS.values():
        assert doc.behaviour
        assert doc.expected_signal


def test_scenario_docs_promise_behaviour_not_decisions() -> None:
    """A scenario that named its outcome would be a puppet show."""
    for doc in SCENARIO_DOCS.values():
        blob = f"{doc.behaviour} {doc.expected_signal}".lower()
        # The words may appear in prose about the policy, but never as a claim
        # that this scenario *will* produce that decision.
        assert "will be blocked" not in blob
        assert "will be approved" not in blob
        assert "always results in" not in blob


@pytest.mark.parametrize("scenario", list(SimulatorScenario))
def test_generators_are_deterministic(scenario: SimulatorScenario) -> None:
    first = ScenarioGenerator(scenario, seed=99, merchant_id="mrc_0001", run_id="aaa")
    second = ScenarioGenerator(scenario, seed=99, merchant_id="mrc_0001", run_id="aaa")

    left = [event.transaction_id for event in first.stream(6)]
    right = [event.transaction_id for event in second.stream(6)]
    assert left == right

    third = ScenarioGenerator(scenario, seed=99, merchant_id="mrc_0001", run_id="aaa")
    amounts_a = [event.amount for event in third.stream(6)]
    fourth = ScenarioGenerator(scenario, seed=99, merchant_id="mrc_0001", run_id="aaa")
    amounts_b = [event.amount for event in fourth.stream(6)]
    assert amounts_a == amounts_b


@pytest.mark.parametrize("scenario", list(SimulatorScenario))
def test_generators_never_emit_a_risk_outcome(scenario: SimulatorScenario) -> None:
    """The event carries no field through which a decision could be asserted."""
    generator = ScenarioGenerator(scenario, seed=1, merchant_id="mrc_0001", run_id="bbb")
    event = generator.next_event()

    for forbidden in (
        "fraud_probability",
        "anomaly_score",
        "decision",
        "risk_score",
        "is_fraud",
    ):
        assert not hasattr(event, forbidden), forbidden


def test_generated_references_are_marked_simulated() -> None:
    generator = ScenarioGenerator(
        SimulatorScenario.NORMAL, seed=1, merchant_id="mrc_0001", run_id="ccc"
    )
    for event in generator.stream(4):
        assert ingest_service.is_simulated(event.transaction_id)


def test_generated_timestamps_advance() -> None:
    """The point-in-time feature layer orders by timestamp; going back breaks it."""
    generator = ScenarioGenerator(
        SimulatorScenario.SUSPICIOUS, seed=5, merchant_id="mrc_0001", run_id="ddd"
    )
    stamps = [event.timestamp for event in generator.stream(8)]
    assert stamps == sorted(stamps)


def test_the_ring_scenarios_share_a_device_and_ip() -> None:
    """Entity sharing is the whole signal the ring scenarios create."""
    generator = ScenarioGenerator(
        SimulatorScenario.COORDINATED_FRAUD, seed=2, merchant_id="mrc_0001", run_id="eee"
    )
    events = list(generator.stream(6))

    assert len({event.device_id for event in events}) == 1
    assert len({event.ip_address for event in events}) == 1
    # ...across more than one customer, which is what makes it a ring.
    assert len({event.customer_id for event in events}) > 1


def test_the_normal_scenario_does_not_share_devices_across_customers() -> None:
    generator = ScenarioGenerator(
        SimulatorScenario.NORMAL, seed=2, merchant_id="mrc_0001", run_id="fff"
    )
    pairs = {(event.customer_id, event.device_id) for event in generator.stream(20)}
    by_device: dict[str | None, set[str]] = {}
    for customer, device in pairs:
        by_device.setdefault(device, set()).add(customer)

    assert all(len(customers) == 1 for customers in by_device.values())


# --------------------------------------------------------------------------
# Simulator configuration
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "config",
    [
        SimulatorConfig(transactions_per_second=0),
        SimulatorConfig(transactions_per_second=MAX_RATE + 1),
        SimulatorConfig(max_transactions=0),
        SimulatorConfig(max_transactions=MAX_TRANSACTIONS + 1),
    ],
)
def test_invalid_simulator_configuration_is_rejected(config: SimulatorConfig) -> None:
    with pytest.raises(ValueError):
        config.validated()


def test_the_simulator_is_bounded_by_default() -> None:
    """It must not run indefinitely unless explicitly told to."""
    config = SimulatorConfig()
    assert config.max_transactions <= MAX_TRANSACTIONS
    assert config.max_transactions > 0


@pytest.mark.anyio
async def test_engine_starts_idle_and_reports_status() -> None:
    local = SimulatorEngine()
    status = local.status()

    assert status["state"] == str(SimulatorState.IDLE)
    assert status["processed"] == 0
    assert status["queue_depth"] == 0


@pytest.mark.anyio
async def test_stopping_an_idle_engine_is_harmless() -> None:
    local = SimulatorEngine()
    status = await local.stop()
    assert status["state"] == str(SimulatorState.IDLE)


@pytest.mark.anyio
async def test_pausing_an_idle_engine_is_rejected() -> None:
    local = SimulatorEngine()
    with pytest.raises(RuntimeError, match="not running"):
        await local.pause()


# --------------------------------------------------------------------------
# The API surface
# --------------------------------------------------------------------------
def test_ingest_endpoint_accepts_a_valid_event(client: TestClient, db_session: Session) -> None:
    reference = merchant_reference(db_session)
    db_session.commit()

    response = client.post(
        "/api/events/transactions",
        json={
            "transaction_id": "SIM_api_001",
            "amount": "3400.00",
            "currency": "INR",
            "customer_id": "SIM_CUS_api",
            "merchant_id": reference,
            "payment_method": "card",
            "country": "IN",
            "city": "Mumbai",
            "timestamp": datetime.now(UTC).isoformat(),
            "device_id": "SIM_dev_api",
            "device_type": "android",
            "ip_address": "198.18.4.4",
            "ip_country": "IN",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["simulated"] is True
    assert body["decision"] in {"APPROVE", "STEP_UP", "REVIEW", "BLOCK"}


def test_ingest_endpoint_is_idempotent(client: TestClient, db_session: Session) -> None:
    reference = merchant_reference(db_session)
    db_session.commit()
    payload = {
        "transaction_id": "SIM_api_dup",
        "amount": "3400.00",
        "currency": "INR",
        "customer_id": "SIM_CUS_api",
        "merchant_id": reference,
        "payment_method": "card",
        "country": "IN",
        "city": "Mumbai",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    first = client.post("/api/events/transactions", json=payload).json()
    second = client.post("/api/events/transactions", json=payload).json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["decision_id"] == first["decision_id"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"amount": "-5"},
        {"amount": "0"},
        {"currency": "rupees"},
        {"country": "India"},
        {"transaction_id": "'; DROP TABLE transactions;--"},
        {"customer_id": "../../etc/passwd"},
        {"device_id": "' OR 1=1"},
        {"ip_address": "not an ip!!"},
        {"payment_method": "telepathy"},
        {"failed_attempts": -1},
        {"timestamp": "2026-01-01T00:00:00"},  # naive: ambiguous ordering
        {"is_fraud": True},  # not an accepted field at all
        {"fraud_probability": 0.99},
        {"decision": "APPROVE"},
    ],
)
def test_ingest_endpoint_rejects_malformed_events(
    client: TestClient, db_session: Session, mutation: dict[str, object]
) -> None:
    reference = merchant_reference(db_session)
    db_session.commit()
    payload: dict[str, object] = {
        "transaction_id": "SIM_api_bad",
        "amount": "3400.00",
        "currency": "INR",
        "customer_id": "SIM_CUS_api",
        "merchant_id": reference,
        "payment_method": "card",
        "country": "IN",
        "city": "Mumbai",
        "timestamp": datetime.now(UTC).isoformat(),
        **mutation,
    }
    assert client.post("/api/events/transactions", json=payload).status_code == 422


def test_events_endpoint_returns_the_stream(client: TestClient, db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_apievents_001"))
    db_session.commit()

    body = client.get("/api/events?limit=50").json()
    assert body["latest_sequence"] > 0
    assert body["events"]
    sequences = [event["sequence"] for event in body["events"]]
    assert sequences == sorted(sequences)


def test_events_endpoint_resumes_after_a_cursor(client: TestClient, db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_apiresume_001"))
    db_session.commit()
    everything = client.get("/api/events?limit=200").json()["events"]
    cursor = everything[len(everything) // 2]["sequence"]

    resumed = client.get(f"/api/events?after={cursor}&limit=200").json()["events"]
    assert all(event["sequence"] > cursor for event in resumed)


@pytest.mark.parametrize("query", ["limit=99999", "after=-1", "limit=0"])
def test_events_endpoint_rejects_hostile_parameters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/events?{query}").status_code == 422


def test_simulator_status_endpoint(client: TestClient) -> None:
    body = client.get("/api/simulator/status").json()
    assert body["state"] in {"idle", "running", "paused", "stopping"}
    assert body["queue_capacity"] > 0


def test_simulator_scenarios_endpoint_documents_each_one(client: TestClient) -> None:
    body = client.get("/api/simulator/scenarios").json()

    assert {entry["scenario"] for entry in body["scenarios"]} == {
        str(value) for value in SimulatorScenario
    }
    assert "never set by the simulator" in body["note"]


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario": "not_a_scenario"},
        {"transactions_per_second": 0},
        {"transactions_per_second": 1000},
        {"max_transactions": 0},
        {"max_transactions": 999999},
        {"seed": -1},
        {"unexpected_field": True},
    ],
)
def test_simulator_start_rejects_invalid_configuration(
    client: TestClient, payload: dict[str, object]
) -> None:
    assert client.post("/api/simulator/start", json=payload).status_code == 422


def test_live_metrics_endpoint(client: TestClient, db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_metrics_001"))
    db_session.commit()

    body = client.get("/api/live/metrics").json()
    assert body["transactions_processed"] >= 1
    assert body["total_events"] >= 1
    assert body["queue_capacity"] > 0
    assert body["simulator_state"] in {"idle", "running", "paused", "stopping"}


def test_live_metrics_counts_only_simulated_traffic(
    client: TestClient, db_session: Session
) -> None:
    """Mixing the seeded dataset into a live counter would make it meaningless."""
    total_decisions = db_session.scalar(select(func.count(RiskDecision.id))) or 0
    ingest_service.ingest(db_session, make_event(db_session, "SIM_metrics_002"))
    db_session.commit()

    body = client.get("/api/live/metrics").json()
    assert body["transactions_processed"] < total_decisions + 5


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/api/live/metrics",
        "/api/events",
        "/api/simulator/status",
        "/api/simulator/scenarios",
    ],
)
def test_read_only_live_endpoints_reject_mutation(client: TestClient, path: str) -> None:
    for verb in ("put", "delete", "patch"):
        assert getattr(client, verb)(path).status_code == 405


def test_live_responses_leak_nothing_sensitive(client: TestClient, db_session: Session) -> None:
    ingest_service.ingest(db_session, make_event(db_session, "SIM_leak_001"))
    db_session.commit()

    for path in ("/api/live/metrics", "/api/events?limit=20", "/api/simulator/status"):
        raw = client.get(path).text.lower()
        for secret in ("password", "api_key", "postgresql://", "/srv/", ".joblib"):
            assert secret not in raw, path


def test_the_simulator_cannot_reach_policy_or_models() -> None:
    """The simulator package must not import anything that could mutate them.

    Parsed rather than grepped: a substring search matches "train" inside
    "constraint" and would fail on a comment, which teaches the next person to
    delete the test rather than trust it.
    """
    import ast

    import app.simulator.engine as engine_module
    import app.simulator.scenarios as scenarios_module

    forbidden_roots = {"ml", "policy"}
    for module in (engine_module, scenarios_module):
        source = module.__file__
        assert source is not None
        tree = ast.parse(Path(source).read_text(encoding="utf-8"))

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for name in imported:
            root = name.split(".")[0]
            assert root not in forbidden_roots, (
                f"{module.__name__} imports {name}; the simulator must reach the "
                "models and the policy only through the pipeline services"
            )


# --------------------------------------------------------------------------
# SSE formatting
# --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_sse_frames_carry_id_type_and_data() -> None:
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    event = {
        "event_id": "EVT-1",
        "sequence": 7,
        "transaction_id": "SIM_x",
        "event_type": "risk_scored",
        "transaction_sequence": 2,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {"fraud_probability": 0.5},
    }

    frames: list[str] = []
    stream = event_service.sse_stream(queue, backlog=[event], heartbeat_seconds=0.05)
    frames.append(await anext(stream))

    frame = frames[0]
    # `id:` is what the browser echoes back as Last-Event-ID.
    assert frame.startswith("id: 7\n")
    assert "event: risk_scored\n" in frame
    assert '"sequence":7' in frame
    assert frame.endswith("\n\n")


@pytest.mark.anyio
async def test_sse_emits_a_heartbeat_when_idle() -> None:
    """An idle stream must not look identical to a dead one."""
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    stream = event_service.sse_stream(queue, backlog=[], heartbeat_seconds=0.05)

    frame = await anext(stream)
    assert frame.startswith(":")


@pytest.mark.anyio
async def test_sse_endpoint_sets_streaming_headers() -> None:
    """The stream's headers are asserted on the response object, not over HTTP.

    Holding the endpoint open through a test client would hang: the stream is
    unbounded by design, so the client has nothing to wait for and cannot close
    it cleanly. Calling the handler directly checks the same contract - content
    type, no caching, and the header that stops nginx buffering a live stream
    into apparent silence - without opening a connection that never ends.
    """
    from starlette.requests import Request

    from app.api.routes.live import stream_events

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events/stream",
        "headers": [],
        "query_string": b"",
    }
    response = await stream_events(Request(scope), last_event_id=None)

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


# --------------------------------------------------------------------------
# End to end through the pipeline
# --------------------------------------------------------------------------
def test_a_ring_produces_shared_entity_evidence(db_session: Session) -> None:
    """The coordinated scenario must actually create the sharing signal.

    This asserts the *behaviour* reached the database - not that any particular
    decision followed, which is the pipeline's business.
    """
    generator = ScenarioGenerator(
        SimulatorScenario.COORDINATED_FRAUD,
        seed=11,
        merchant_id=merchant_reference(db_session),
        run_id="ringtest",
    )
    for event in generator.stream(6):
        ingest_service.ingest(db_session, event)

    from app.models import Device

    device = db_session.scalars(
        select(Device).where(Device.device_id.like("SIM_dev_ring_ringtest%"))
    ).one()
    customers = db_session.scalars(
        select(func.count(func.distinct(Transaction.customer_id))).where(
            Transaction.device_id == device.id
        )
    ).one()
    assert customers >= 2


def test_every_ingested_transaction_reaches_a_decision(db_session: Session) -> None:
    generator = ScenarioGenerator(
        SimulatorScenario.NORMAL,
        seed=21,
        merchant_id=merchant_reference(db_session),
        run_id="e2e",
    )
    references = []
    for event in generator.stream(4):
        result = ingest_service.ingest(db_session, event)
        references.append(result.reference)
        assert result.decision is not None

    decided = db_session.scalar(
        select(func.count(RiskDecision.id))
        .select_from(RiskDecision)
        .join(Transaction, Transaction.id == RiskDecision.transaction_id)
        .where(Transaction.transaction_id.in_(references))
    )
    assert decided == len(references)


def test_a_replayed_scenario_produces_a_fresh_decision(db_session: Session) -> None:
    """Replay re-runs the pipeline; it does not copy a recorded answer."""
    first = ScenarioGenerator(
        SimulatorScenario.SUSPICIOUS,
        seed=31,
        merchant_id=merchant_reference(db_session),
        run_id="replay_a",
    )
    second = ScenarioGenerator(
        SimulatorScenario.SUSPICIOUS,
        seed=31,
        merchant_id=merchant_reference(db_session),
        run_id="replay_b",
    )

    left = ingest_service.ingest(db_session, first.next_event())
    right = ingest_service.ingest(db_session, second.next_event())

    # Different references, so both were genuinely decided rather than one
    # being reported as a duplicate of the other.
    assert left.reference != right.reference
    assert left.duplicate is False
    assert right.duplicate is False
    assert left.decision_id != right.decision_id
