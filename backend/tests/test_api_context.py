"""Tests for ``/api/transactions/{id}/context``.

The context endpoint must return complete, factual evidence - and must not
return, imply or invent a risk verdict.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.seed import scenarios as scn

API = "/api"

RISK_VERDICT_FIELDS = {
    "risk_score",
    "risk_level",
    "fraud_probability",
    "recommendation",
    "recommended_action",
    "decision",
    "verdict",
}


def _context(client: TestClient, reference: str) -> dict:
    response = client.get(f"{API}/transactions/{reference}/context")
    assert response.status_code == 200, response.text
    return response.json()


def test_context_returns_every_documented_section(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_B_CURRENT)

    assert set(body) == {
        "transaction",
        "merchant",
        "customer",
        "device",
        "ip_address",
        "location",
        "customer_velocity",
        "device_usage",
        "ip_usage",
        "recent_customer_transactions",
    }


def test_context_for_an_unknown_transaction_returns_404(client: TestClient) -> None:
    assert client.get(f"{API}/transactions/nope/context").status_code == 404


def test_context_never_contains_a_risk_verdict(client: TestClient) -> None:
    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert not RISK_VERDICT_FIELDS & set(node), f"risk verdict leaked: {set(node)}"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for reference in (
        scn.TXN_SCENARIO_A_CURRENT,
        scn.TXN_SCENARIO_B_CURRENT,
        scn.TXN_SCENARIO_C_CURRENT[0],
    ):
        walk(_context(client, reference))


def test_context_relates_to_the_requested_transaction(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_B_CURRENT)

    assert body["transaction"]["transaction_id"] == scn.TXN_SCENARIO_B_CURRENT
    assert body["customer"]["id"] == body["transaction"]["customer_id"]
    assert body["merchant"]["id"] == body["transaction"]["merchant_id"]
    assert body["device"]["id"] == body["transaction"]["device_id"]
    assert body["ip_address"]["id"] == body["transaction"]["ip_address_id"]


def test_recent_transactions_precede_the_subject_and_exclude_it(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_B_CURRENT)
    subject = body["transaction"]
    recent = body["recent_customer_transactions"]

    assert recent
    assert all(item["id"] != subject["id"] for item in recent)
    assert all(item["customer_id"] == subject["customer_id"] for item in recent)
    assert all(item["transaction_timestamp"] <= subject["transaction_timestamp"] for item in recent)
    stamps = [item["transaction_timestamp"] for item in recent]
    assert stamps == sorted(stamps, reverse=True)


# --- scenario A: everything familiar ----------------------------------------


def test_scenario_a_context_shows_a_settled_customer(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_A_CURRENT)

    assert body["location"]["matches_customer_home_city"] is True
    assert body["location"]["matches_customer_home_country"] is True
    assert body["device_usage"]["shared_with_other_customers"] is False
    assert body["device"]["is_trusted"] is True
    assert body["customer_velocity"]["failed_last_1_hour"] == 0


# --- scenario B: unfamiliar everything --------------------------------------


def test_scenario_b_context_exposes_the_spending_deviation(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_B_CURRENT)

    amount = Decimal(body["transaction"]["amount"])
    average = Decimal(body["customer"]["average_transaction_amount"])
    assert amount > average * 10


def test_scenario_b_context_exposes_the_unfamiliar_origin(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_B_CURRENT)

    assert body["location"]["matches_customer_home_country"] is False
    assert body["device"]["device_id"] == scn.DEVICE_SUSPICIOUS_NEW
    assert body["device"]["is_trusted"] is False
    assert body["ip_address"]["is_proxy"] is True
    assert Decimal(body["ip_address"]["reputation_score"]) < Decimal("50")


def test_scenario_b_context_exposes_the_failed_attempts(client: TestClient) -> None:
    velocity = _context(client, scn.TXN_SCENARIO_B_CURRENT)["customer_velocity"]

    assert velocity["failed_last_1_hour"] == scn.SUSPICIOUS_FAILED_ATTEMPTS
    assert velocity["last_1_hour"] >= scn.SUSPICIOUS_FAILED_ATTEMPTS


# --- scenario C: coordinated fraud ------------------------------------------


def test_scenario_c_context_exposes_device_and_ip_sharing(client: TestClient) -> None:
    body = _context(client, scn.TXN_SCENARIO_C_CURRENT[0])

    assert body["device_usage"]["shared_with_other_customers"] is True
    assert body["device_usage"]["distinct_customers"] == len(scn.CUSTOMERS_FRAUD)
    assert body["ip_usage"]["shared_with_other_customers"] is True
    assert body["ip_usage"]["distinct_customers"] == len(scn.CUSTOMERS_FRAUD)


def test_scenario_c_context_exposes_the_velocity_burst(client: TestClient) -> None:
    for reference in scn.TXN_SCENARIO_C_CURRENT:
        velocity = _context(client, reference)["customer_velocity"]
        assert velocity["last_1_hour"] >= scn.FRAUD_BURST_PER_CUSTOMER


# --- velocity windows are consistent ----------------------------------------


def test_velocity_windows_are_monotonic(client: TestClient) -> None:
    velocity = _context(client, scn.TXN_SCENARIO_C_CURRENT[0])["customer_velocity"]

    assert (
        velocity["last_5_minutes"]
        <= velocity["last_1_hour"]
        <= velocity["last_24_hours"]
        <= velocity["last_7_days"]
    )
