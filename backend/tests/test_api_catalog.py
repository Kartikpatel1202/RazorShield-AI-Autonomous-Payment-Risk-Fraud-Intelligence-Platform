"""Data-access endpoint tests: lookups, listing, filtering and pagination."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, Device, IpAddress, Transaction
from app.schemas.common import MAX_PAGE_SIZE
from app.seed import scenarios as scn

API = "/api"


def test_merchants_are_listed(client: TestClient) -> None:
    response = client.get(f"{API}/merchants")
    assert response.status_code == 200

    merchants = response.json()
    assert merchants
    assert {"id", "external_merchant_id", "name", "email", "is_active"} <= set(merchants[0])


def test_customer_is_fetched_by_external_id(client: TestClient) -> None:
    response = client.get(f"{API}/customers/{scn.CUSTOMER_SUSPICIOUS}")
    assert response.status_code == 200

    body = response.json()
    assert body["external_customer_id"] == scn.CUSTOMER_SUSPICIOUS
    assert body["historical_risk_level"] in {"low", "medium", "high"}


def test_customer_is_also_fetchable_by_primary_key(client: TestClient, db_session: Session) -> None:
    customer = db_session.scalars(
        select(Customer).where(Customer.external_customer_id == scn.CUSTOMER_NORMAL)
    ).one()

    response = client.get(f"{API}/customers/{customer.id}")
    assert response.status_code == 200
    assert response.json()["external_customer_id"] == scn.CUSTOMER_NORMAL


def test_unknown_customer_returns_404(client: TestClient) -> None:
    response = client.get(f"{API}/customers/NO_SUCH_CUSTOMER")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_device_and_ip_lookups_use_their_business_keys(client: TestClient) -> None:
    device = client.get(f"{API}/devices/{scn.DEVICE_FRAUD_SHARED}")
    assert device.status_code == 200
    assert device.json()["device_id"] == scn.DEVICE_FRAUD_SHARED

    ip = client.get(f"{API}/ip-addresses/{scn.IP_FRAUD_SHARED}")
    assert ip.status_code == 200
    assert ip.json()["ip_address"] == scn.IP_FRAUD_SHARED
    assert ip.json()["is_proxy"] is True


def test_unknown_device_and_ip_return_404(client: TestClient) -> None:
    assert client.get(f"{API}/devices/dev_missing").status_code == 404
    assert client.get(f"{API}/ip-addresses/203.0.113.99").status_code == 404


# --- transaction history ----------------------------------------------------


def test_customer_transactions_are_newest_first(client: TestClient) -> None:
    response = client.get(f"{API}/customers/{scn.CUSTOMER_SUSPICIOUS}/transactions")
    assert response.status_code == 200

    items = response.json()["items"]
    stamps = [item["transaction_timestamp"] for item in items]
    assert stamps == sorted(stamps, reverse=True)
    assert items[0]["transaction_id"] == scn.TXN_SCENARIO_B_CURRENT


def test_customer_transactions_only_contain_that_customer(
    client: TestClient, db_session: Session
) -> None:
    customer = db_session.scalars(
        select(Customer).where(Customer.external_customer_id == scn.CUSTOMER_NORMAL)
    ).one()

    items = client.get(f"{API}/customers/{scn.CUSTOMER_NORMAL}/transactions").json()["items"]
    assert items
    assert {item["customer_id"] for item in items} == {customer.id}


def test_device_transactions_expose_the_shared_device(
    client: TestClient, db_session: Session
) -> None:
    response = client.get(f"{API}/devices/{scn.DEVICE_FRAUD_SHARED}/transactions?page_size=100")
    assert response.status_code == 200

    items = response.json()["items"]
    expected = {
        db_session.scalars(select(Customer.id).where(Customer.external_customer_id == name)).one()
        for name in scn.CUSTOMERS_FRAUD
    }
    assert {item["customer_id"] for item in items} == expected


def test_ip_transactions_expose_the_shared_ip(client: TestClient) -> None:
    response = client.get(f"{API}/ip-addresses/{scn.IP_FRAUD_SHARED}/transactions?page_size=100")
    assert response.status_code == 200
    assert len({item["customer_id"] for item in response.json()["items"]}) == len(
        scn.CUSTOMERS_FRAUD
    )


# --- pagination -------------------------------------------------------------


def test_transaction_list_is_paginated_not_dumped(client: TestClient, db_session: Session) -> None:
    total = db_session.scalar(select(func.count(Transaction.id)))

    response = client.get(f"{API}/transactions")
    body = response.json()

    assert body["meta"]["total_items"] == total
    assert len(body["items"]) == body["meta"]["page_size"]
    assert len(body["items"]) < total


def test_pages_do_not_overlap_and_cover_the_collection(client: TestClient) -> None:
    first = client.get(f"{API}/transactions?page=1&page_size=10").json()
    second = client.get(f"{API}/transactions?page=2&page_size=10").json()

    first_ids = [item["id"] for item in first["items"]]
    second_ids = [item["id"] for item in second["items"]]

    assert len(first_ids) == len(second_ids) == 10
    assert not set(first_ids) & set(second_ids)
    assert first["meta"]["has_next"] is True
    assert first["meta"]["has_previous"] is False
    assert second["meta"]["has_previous"] is True


def test_last_page_reports_no_next(client: TestClient) -> None:
    first = client.get(f"{API}/transactions?page_size=200").json()
    last_page = first["meta"]["total_pages"]

    body = client.get(f"{API}/transactions?page={last_page}&page_size=200").json()
    assert body["meta"]["has_next"] is False
    assert body["items"]


def test_page_size_is_capped(client: TestClient) -> None:
    assert client.get(f"{API}/transactions?page_size={MAX_PAGE_SIZE}").status_code == 200
    assert client.get(f"{API}/transactions?page_size={MAX_PAGE_SIZE + 1}").status_code == 422


def test_page_number_must_be_positive(client: TestClient) -> None:
    assert client.get(f"{API}/transactions?page=0").status_code == 422


def test_page_beyond_the_end_returns_an_empty_page(client: TestClient) -> None:
    body = client.get(f"{API}/transactions?page=9999&page_size=50").json()
    assert body["items"] == []
    assert body["meta"]["has_next"] is False


# --- filtering --------------------------------------------------------------


def test_fraud_filter_selects_only_labelled_rows(client: TestClient) -> None:
    body = client.get(f"{API}/transactions?is_fraud=true&page_size=50").json()
    assert body["items"]
    assert all(item["is_fraud"] is True for item in body["items"])


def test_status_filter_selects_only_that_status(client: TestClient) -> None:
    body = client.get(f"{API}/transactions?status=failed&page_size=50").json()
    assert body["items"]
    assert all(item["status"] == "failed" for item in body["items"])


def test_merchant_filter_scopes_the_feed(client: TestClient, db_session: Session) -> None:
    merchant_id = db_session.scalars(select(Transaction.merchant_id).limit(1)).one()

    body = client.get(f"{API}/transactions?merchant_id={merchant_id}&page_size=50").json()
    assert body["items"]
    assert all(item["merchant_id"] == merchant_id for item in body["items"])


def test_single_transaction_is_fetchable_by_reference(client: TestClient) -> None:
    response = client.get(f"{API}/transactions/{scn.TXN_SCENARIO_B_CURRENT}")
    assert response.status_code == 200
    assert response.json()["amount"] == str(scn.SUSPICIOUS_CURRENT_AMOUNT)


def test_transaction_payload_carries_no_risk_score(client: TestClient) -> None:
    body = client.get(f"{API}/transactions/{scn.TXN_SCENARIO_B_CURRENT}").json()
    assert not {"risk_score", "fraud_probability", "risk_level"} & set(body)


def test_devices_and_ips_referenced_by_transactions_exist(
    client: TestClient, db_session: Session
) -> None:
    items = client.get(f"{API}/transactions?page_size=50").json()["items"]

    for item in items:
        if item["device_id"] is not None:
            assert db_session.get(Device, item["device_id"]) is not None
        if item["ip_address_id"] is not None:
            assert db_session.get(IpAddress, item["ip_address_id"]) is not None
