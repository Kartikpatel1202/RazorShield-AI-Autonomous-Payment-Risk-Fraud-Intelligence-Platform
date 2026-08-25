"""The eight read-only investigation tools."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.schemas.evidence import EvidenceSeverity
from agent.schemas.investigation import ToolName
from agent.tools.base import ToolResult, summarise
from agent.tools.behaviour_tools import get_location_history, get_velocity
from agent.tools.entity_tools import (
    get_customer_history,
    get_device_history,
    get_ip_history,
    get_transaction_context,
)
from agent.tools.model_tools import get_anomaly_result, get_ml_prediction
from agent.tools.registry import TOOL_DESCRIPTIONS, TOOL_REGISTRY, catalogue, resolve
from app.models import Investigation, RiskPrediction, RiskSignal, Transaction

NORMAL = "TXN_SCENARIO_A_CURRENT"
SUSPICIOUS = "TXN_SCENARIO_B_CURRENT"
RING = "TXN_SCENARIO_C_CURRENT_1"


# --- registry ---------------------------------------------------------------


def test_every_tool_name_is_registered() -> None:
    assert set(TOOL_REGISTRY) == set(ToolName)


def test_every_tool_is_described_for_the_model() -> None:
    assert set(TOOL_DESCRIPTIONS) == set(ToolName)
    for description in TOOL_DESCRIPTIONS.values():
        assert description.strip()


def test_the_catalogue_lists_every_tool() -> None:
    text = catalogue()
    for name in ToolName:
        assert str(name) in text


def test_resolving_an_unregistered_tool_fails() -> None:
    with pytest.raises(KeyError):
        resolve("execute_sql")  # type: ignore[arg-type]


def test_there_is_no_generic_query_tool() -> None:
    """A tool that ran arbitrary SQL would undo every other guarantee."""
    forbidden = {"execute_sql", "query", "run_sql", "eval", "exec", "raw_query"}
    assert not forbidden & {str(name) for name in ToolName}


# --- transaction context ----------------------------------------------------


def test_transaction_context_returns_the_transaction_and_its_entities(tool_context) -> None:  # noqa: ANN001
    result = get_transaction_context(tool_context(SUSPICIOUS))

    assert result.payload["transaction_id"] == SUSPICIOUS
    assert result.payload["amount"] > 0
    assert result.payload["merchant"]
    assert result.payload["customer_external_id"]
    assert result.payload["device_id"]
    assert result.payload["ip_address"]


def test_transaction_context_flags_a_foreign_origin(tool_context) -> None:  # noqa: ANN001
    result = get_transaction_context(tool_context(SUSPICIOUS))
    claims = [item.claim for item in result.evidence]
    assert any("outside the customer's home country" in claim for claim in claims)


def test_a_normal_transaction_yields_no_alarming_evidence(tool_context) -> None:  # noqa: ANN001
    result = get_transaction_context(tool_context(NORMAL))
    assert all(item.severity is not EvidenceSeverity.CRITICAL for item in result.evidence)


# --- customer history -------------------------------------------------------


def test_customer_history_reports_the_baseline(tool_context) -> None:  # noqa: ANN001
    result = get_customer_history(tool_context(SUSPICIOUS))

    assert result.payload["previous_transaction_count"] > 0
    assert result.payload["historical_average_amount"] > 0
    assert result.payload["account_age_days"] > 0
    assert isinstance(result.payload["recent_transactions"], list)


def test_customer_history_detects_a_spend_spike(tool_context) -> None:  # noqa: ANN001
    result = get_customer_history(tool_context(SUSPICIOUS))
    spikes = [
        item
        for item in result.evidence
        if "historical average" in item.claim
        and item.severity in {EvidenceSeverity.HIGH, EvidenceSeverity.CRITICAL}
    ]
    assert spikes, "an 85k payment against a ~2.5k baseline should register"


def test_customer_history_bounds_what_it_returns(tool_context) -> None:  # noqa: ANN001
    from agent.tools.entity_tools import RECENT_LIMIT

    result = get_customer_history(tool_context(NORMAL))
    assert len(result.payload["recent_transactions"]) <= RECENT_LIMIT


# --- device history ---------------------------------------------------------


def test_device_history_reports_sharing(tool_context) -> None:  # noqa: ANN001
    result = get_device_history(tool_context(RING))

    assert result.payload["has_device"] is True
    assert result.payload["distinct_customers_before"] >= 2
    claims = [item.claim for item in result.evidence]
    assert any("shared across" in claim for claim in claims)


def test_device_history_lists_associated_customers(tool_context) -> None:  # noqa: ANN001
    from agent.tools.entity_tools import ASSOCIATED_LIMIT

    result = get_device_history(tool_context(RING))
    associated = result.payload["associated_customers"]
    assert len(associated) >= 2
    assert len(associated) <= ASSOCIATED_LIMIT


def test_device_history_flags_a_brand_new_device(tool_context) -> None:  # noqa: ANN001
    result = get_device_history(tool_context(SUSPICIOUS))
    claims = [item.claim for item in result.evidence]
    assert any("first seen only" in claim or "never been seen" in claim for claim in claims)


def test_device_history_handles_a_transaction_without_a_device(
    tool_context, db_session: Session
) -> None:  # noqa: ANN001
    ctx = tool_context(NORMAL)
    ctx.transaction.device_id = None
    db_session.flush()

    result = get_device_history(ctx.build(db_session, ctx.transaction))
    assert result.payload["has_device"] is False
    assert result.evidence == []


# --- IP history -------------------------------------------------------------


def test_ip_history_reports_sharing_and_reputation(tool_context) -> None:  # noqa: ANN001
    result = get_ip_history(tool_context(RING))

    assert result.payload["has_ip"] is True
    assert result.payload["distinct_customers_before"] >= 2
    assert 0 <= result.payload["reputation_score"] <= 100
    assert isinstance(result.payload["countries_seen"], dict)


def test_ip_history_flags_a_proxy(tool_context) -> None:  # noqa: ANN001
    result = get_ip_history(tool_context(RING))
    claims = [item.claim for item in result.evidence]
    assert any("proxy" in claim for claim in claims)


# --- velocity ---------------------------------------------------------------


def test_velocity_returns_every_documented_window(tool_context) -> None:  # noqa: ANN001
    result = get_velocity(tool_context(RING))

    for key in (
        "transactions_last_5m",
        "transactions_last_1h",
        "transactions_last_24h",
        "failed_transactions_last_1h",
        "failed_transactions_last_24h",
        "amount_last_1h",
        "amount_last_24h",
    ):
        assert key in result.payload


def test_velocity_windows_end_at_the_transaction(tool_context) -> None:  # noqa: ANN001
    ctx = tool_context(RING)
    result = get_velocity(ctx)
    assert result.payload["windows_end_at"] == ctx.boundary.isoformat()


def test_velocity_detects_a_burst(tool_context) -> None:  # noqa: ANN001
    result = get_velocity(tool_context(RING))
    assert result.payload["transactions_last_1h"] >= 3
    assert any("in the hour before" in item.claim for item in result.evidence)


# --- location ---------------------------------------------------------------


def test_location_history_compares_against_prior_places(tool_context) -> None:  # noqa: ANN001
    result = get_location_history(tool_context(SUSPICIOUS))

    assert result.payload["current_country"]
    assert isinstance(result.payload["previous_countries"], dict)
    assert 0.0 <= result.payload["country_frequency"] <= 1.0


def test_location_history_recognises_a_familiar_place(tool_context) -> None:  # noqa: ANN001
    result = get_location_history(tool_context(NORMAL))
    assert result.payload["is_home_city"] is True
    assert all(item.severity is not EvidenceSeverity.HIGH for item in result.evidence)


# --- model tools ------------------------------------------------------------


def test_ml_prediction_tool_returns_the_model_output(tool_context) -> None:  # noqa: ANN001
    result = get_ml_prediction(tool_context(SUSPICIOUS))

    assert result.payload["available"] is True
    assert 0.0 <= result.payload["fraud_probability"] <= 1.0
    assert result.payload["model_version"]
    assert result.evidence


def test_anomaly_tool_returns_the_model_output(tool_context) -> None:  # noqa: ANN001
    result = get_anomaly_result(tool_context(RING))

    assert result.payload["available"] is True
    assert 0 <= result.payload["anomaly_score"] <= 100
    assert result.payload["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_model_tools_do_not_write_anything(tool_context, db_session: Session) -> None:  # noqa: ANN001
    """Reading a model's opinion must not persist a new one."""
    before_predictions = db_session.scalar(select(func.count(RiskPrediction.id)))
    before_signals = db_session.scalar(select(func.count(RiskSignal.id)))

    ctx = tool_context(RING)
    get_ml_prediction(ctx)
    get_anomaly_result(ctx)

    assert db_session.scalar(select(func.count(RiskPrediction.id))) == before_predictions
    assert db_session.scalar(select(func.count(RiskSignal.id))) == before_signals


# --- read-only guarantee ----------------------------------------------------


@pytest.mark.parametrize("tool", list(ToolName))
def test_no_tool_modifies_the_database(tool: ToolName, tool_context, db_session: Session) -> None:  # noqa: ANN001
    """Every tool must leave every table exactly as it found it."""
    counts_before = {
        model.__name__: db_session.scalar(select(func.count()).select_from(model))
        for model in (Transaction, RiskPrediction, RiskSignal, Investigation)
    }

    resolve(tool)(tool_context(RING))
    db_session.flush()

    counts_after = {
        model.__name__: db_session.scalar(select(func.count()).select_from(model))
        for model in (Transaction, RiskPrediction, RiskSignal, Investigation)
    }
    assert counts_after == counts_before


@pytest.mark.parametrize("tool", list(ToolName))
def test_every_tool_returns_a_well_formed_result(tool: ToolName, tool_context) -> None:  # noqa: ANN001
    result = resolve(tool)(tool_context(RING))

    assert isinstance(result, ToolResult)
    assert isinstance(result.payload, dict)
    for item in result.evidence:
        assert item.claim.strip()
        assert item.severity in set(EvidenceSeverity)


@pytest.mark.parametrize("tool", list(ToolName))
def test_no_tool_leaks_credentials_or_paths(tool: ToolName, tool_context) -> None:  # noqa: ANN001
    """Tool payloads reach the prompt, so they must carry nothing sensitive."""
    result = resolve(tool)(tool_context(RING))
    rendered = repr(result.payload).lower() + repr([e.details for e in result.evidence]).lower()

    for marker in ("password", "secret", "api_key", "postgresql://", "c:\\", "/srv/", "token"):
        assert marker not in rendered


# --- prompt rendering -------------------------------------------------------


def test_summaries_are_bounded() -> None:
    payload = {f"key_{index}": "x" * 200 for index in range(50)}
    assert len(summarise(payload, limit=500)) <= 500
