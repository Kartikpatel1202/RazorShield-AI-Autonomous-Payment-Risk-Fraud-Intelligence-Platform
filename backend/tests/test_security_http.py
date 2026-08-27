"""HTTP-layer hardening: headers, correlation, rate limits, input validation.

The unifying claim: **a malformed, hostile or oversized request fails safely.**
Safely means a 4xx with a body that says what was wrong with the request and
nothing about the machine serving it - no stack trace, no filesystem path, no
SQL, no library version, and never a 500.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.context import CORRELATION_HEADER, sanitize_correlation_id
from app.core.ratelimit import FixedWindowRateLimiter

OVERVIEW = "/api/analytics/overview"
TRANSACTIONS = "/api/transactions"
EXPLORER = "/api/transactions/explorer"


# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Cache-Control", "no-store"),
    ],
)
def test_responses_carry_the_defensive_headers(
    client: TestClient, header: str, expected: str
) -> None:
    response = client.get(OVERVIEW)
    assert response.headers[header] == expected


def test_the_api_content_security_policy_permits_nothing(client: TestClient) -> None:
    """`default-src 'none'` on a JSON API.

    There is nothing for this response to load, so the strictest possible policy
    costs nothing and means a response somehow rendered as a document could
    still not fetch or execute anything.
    """
    policy = client.get(OVERVIEW).headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "base-uri 'none'" in policy
    assert "form-action 'none'" in policy


def test_the_error_response_is_protected_too(anonymous_client: TestClient) -> None:
    """Headers must be on the responses that short-circuit, not just the happy
    path - a 401 rendered in a frame is as much a problem as a 200."""
    response = anonymous_client.get(OVERVIEW)
    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_the_server_header_does_not_advertise_uvicorn(client: TestClient) -> None:
    """Not a vulnerability; a free hint about which CVEs are worth trying."""
    server = client.get(OVERVIEW).headers.get("Server", "")
    assert server == "razorshield"
    assert "uvicorn" not in server.lower()


def test_no_response_header_leaks_a_version(client: TestClient) -> None:
    joined = " ".join(f"{k}: {v}" for k, v in client.get(OVERVIEW).headers.items()).lower()
    for marker in ("uvicorn", "python/3", "starlette", "fastapi/"):
        assert marker not in joined


def test_hsts_is_absent_over_plain_http(client: TestClient) -> None:
    """Deliberate. The compose stack serves HTTP, where HSTS is ignored by the
    browser and would be a foot-gun anywhere it were not."""
    assert "Strict-Transport-Security" not in client.get(OVERVIEW).headers


def test_the_docs_are_withheld_in_production() -> None:
    """The schema is a map of the attack surface.

    Built from a Settings object rather than by mutating the environment, so the
    assertion is about the rule and not about test ordering.
    """
    from app.main import create_app

    production = Settings(
        environment="production",
        jwt_secret="a" * 48,
        auth_expose_dev_reset_token=False,
        database_url="sqlite://",
    )
    assert not production.docs_enabled

    app = create_app(production)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/docs" not in paths
    assert "/openapi.json" not in paths
    # The API itself is unaffected.
    assert OVERVIEW in paths


def test_a_deployed_environment_refuses_the_placeholder_secret() -> None:
    """Fails at startup rather than minting forgeable tokens for an hour."""
    with pytest.raises(ValueError, match="placeholder"):
        Settings(environment="production", jwt_secret="change-me-in-local-development-only")


def test_a_deployed_environment_refuses_a_short_secret() -> None:
    with pytest.raises(ValueError, match="at least"):
        Settings(environment="staging", jwt_secret="short")


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------
def test_a_correlation_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get(OVERVIEW)
    assert len(response.headers[CORRELATION_HEADER]) >= 8


def test_a_valid_client_correlation_id_is_honoured(client: TestClient) -> None:
    """Joining our trace to the caller's is the entire point of accepting one."""
    response = client.get(OVERVIEW, headers={CORRELATION_HEADER: "caller-trace-0001"})
    assert response.headers[CORRELATION_HEADER] == "caller-trace-0001"


@pytest.mark.parametrize(
    "hostile",
    [
        "short",  # under the minimum length
        "a" * 200,  # over the maximum
        "trace\r\nX-Admin: true",  # header injection
        "trace\ninjected",
        'trace" onload="alert(1)',
        "trace<script>",
        "trace with spaces",
    ],
)
def test_a_hostile_correlation_id_is_replaced_not_sanitised(
    client: TestClient, hostile: str
) -> None:
    """Replaced, not cleaned.

    A cleaned-up version of an attacker's value is still an attacker's value in
    our logs. This one is echoed into a response header and written to every log
    line for the request, so it is an injection vector in both directions.
    """
    response = client.get(OVERVIEW, headers={CORRELATION_HEADER: hostile})
    returned = response.headers[CORRELATION_HEADER]
    assert returned != hostile
    assert "\r" not in returned and "\n" not in returned
    assert returned.isalnum() or "-" in returned


def test_sanitize_accepts_the_shapes_a_tracing_system_produces() -> None:
    for value in ("abcdefgh", "req-2026-08-25-0001", "a" * 64, "trace.id:0001"):
        assert sanitize_correlation_id(value) == value


def test_sanitize_replaces_anything_else() -> None:
    for value in (None, "", "tiny", "a" * 65, "has space", "semi;colon"):
        replaced = sanitize_correlation_id(value)
        assert replaced != value
        assert len(replaced) == 32


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
def test_the_limiter_counts_within_a_window() -> None:
    limiter = FixedWindowRateLimiter()
    for index in range(3):
        decision = limiter.check("bucket", "client", 3)
        assert decision.allowed
        assert decision.remaining == 2 - index

    refused = limiter.check("bucket", "client", 3)
    assert not refused.allowed
    assert refused.retry_after >= 1


def test_the_limiter_separates_clients_and_buckets() -> None:
    """One noisy client must not lock out everyone else, and exhausting the
    login budget must not also block ingestion."""
    limiter = FixedWindowRateLimiter()
    for _ in range(2):
        limiter.check("login", "10.0.0.1", 2)
    assert not limiter.check("login", "10.0.0.1", 2).allowed
    assert limiter.check("login", "10.0.0.2", 2).allowed
    assert limiter.check("ingest", "10.0.0.1", 2).allowed


def test_a_zero_limit_disables_a_bucket() -> None:
    """How an operator turns one off without a code change."""
    limiter = FixedWindowRateLimiter()
    for _ in range(50):
        assert limiter.check("bucket", "client", 0).allowed


def test_a_disabled_limiter_allows_everything() -> None:
    limiter = FixedWindowRateLimiter(enabled=False)
    for _ in range(50):
        assert limiter.check("bucket", "client", 1).allowed


def test_closed_windows_are_evicted() -> None:
    """Bounded memory. Without eviction, one key per client address is retained
    for the life of the process, which is a slow leak an attacker controls."""
    from app.core import ratelimit

    limiter = FixedWindowRateLimiter()
    for index in range(20):
        limiter.check("bucket", f"client-{index}", 5)
    assert limiter.tracked_clients() == 20

    # Reach past the window without sleeping through it.
    original = ratelimit.monotonic
    ratelimit.monotonic = lambda: original() + ratelimit.WINDOW_SECONDS + 1  # type: ignore[assignment]
    try:
        limiter.check("bucket", "client-fresh", 5)
    finally:
        ratelimit.monotonic = original  # type: ignore[assignment]

    assert limiter.tracked_clients() == 1


def test_login_is_rate_limited(rate_limited_client: TestClient) -> None:
    """Credential stuffing is the attack this exists for."""
    limit = 10  # rate_limit_login_per_minute
    body = {"email": "admin@test.invalid", "password": "wrong-password"}

    for _ in range(limit):
        assert rate_limited_client.post("/api/auth/login", json=body).status_code == 401

    refused = rate_limited_client.post("/api/auth/login", json=body)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.headers["X-RateLimit-Limit"] == str(limit)


def test_review_resolution_is_rate_limited(rate_limited_client: TestClient) -> None:
    limit = 60  # rate_limit_review_per_minute
    for _ in range(limit):
        # 404s still count: the limiter runs before the handler, which is what
        # stops an attacker from probing ids for free.
        rate_limited_client.post("/api/reviews/999999/resolve", json={"resolution": "rejected"})

    refused = rate_limited_client.post(
        "/api/reviews/999999/resolve", json={"resolution": "rejected"}
    )
    assert refused.status_code == 429


def test_simulator_control_is_rate_limited(rate_limited_client: TestClient) -> None:
    limit = 30  # rate_limit_simulator_per_minute
    for _ in range(limit):
        rate_limited_client.post("/api/simulator/stop")
    assert rate_limited_client.post("/api/simulator/stop").status_code == 429


def test_reads_are_not_rate_limited(rate_limited_client: TestClient) -> None:
    """Only mutations and the login endpoint are capped.

    Limiting dashboard reads would break the console's own polling long before
    it inconvenienced anyone hostile.
    """
    for _ in range(40):
        assert rate_limited_client.get(OVERVIEW).status_code == 200


# --------------------------------------------------------------------------
# Input hardening
# --------------------------------------------------------------------------
SQL_INJECTION = [
    "' OR '1'='1",
    "'; DROP TABLE transactions; --",
    "1 UNION SELECT password_hash FROM users",
    "admin'--",
    '" OR 1=1 --',
]

XSS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<svg/onload=alert(1)>",
]

TRAVERSAL = [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\config\\sam",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "/etc/shadow",
]

HEADER_INJECTION = ["a\r\nSet-Cookie: admin=1", "a\nX-Role: admin", "a\r\n\r\n<html>"]


@pytest.mark.parametrize("payload", SQL_INJECTION + XSS + TRAVERSAL + HEADER_INJECTION)
def test_a_hostile_path_parameter_fails_safely(client: TestClient, payload: str) -> None:
    """Every query is parameterised, so injection cannot reach the planner.

    What this asserts is the observable consequence: a 4xx, and a response that
    quotes nothing back that could execute in a browser or reveal the machine.
    The payload is percent-encoded because httpx refuses to *build* a URL
    containing a raw CR or LF - which is a fine default and would otherwise mean
    the header-injection cases never reached the server at all.
    """
    response = client.get(f"/api/transactions/{quote(payload, safe='')}")
    assert response.status_code in (400, 404, 422)
    assert response.status_code != 500

    body = response.text.lower()
    for marker in ("<script", "onerror=", "select ", "drop table", "traceback", "sqlalchemy"):
        assert marker not in body


@pytest.mark.parametrize("payload", HEADER_INJECTION)
def test_a_crlf_payload_cannot_split_the_response(client: TestClient, payload: str) -> None:
    """CRLF in a path must not become a header in the response."""
    response = client.get(f"/api/transactions/{quote(payload, safe='')}")
    assert "set-cookie" not in {name.lower() for name in response.headers}
    assert "x-role" not in {name.lower() for name in response.headers}


@pytest.mark.parametrize("payload", SQL_INJECTION + XSS)
def test_a_hostile_query_filter_fails_safely(client: TestClient, payload: str) -> None:
    response = client.get(EXPLORER, params={"search": payload})
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        # A search that matches nothing is the correct outcome; what matters is
        # that the string was treated as a value, not as syntax.
        assert response.json()["items"] == []


@pytest.mark.parametrize("payload", SQL_INJECTION)
def test_injection_does_not_alter_the_database(client: TestClient, payload: str) -> None:
    """The strongest form of the claim: the table is still there afterwards."""
    before = client.get(TRANSACTIONS, params={"page_size": 1}).json()["meta"]["total_items"]
    client.get(EXPLORER, params={"search": payload})
    after = client.get(TRANSACTIONS, params={"page_size": 1}).json()["meta"]["total_items"]
    assert after == before
    assert before > 0


@pytest.mark.parametrize("value", ["-1", "0", "abc", "99999999999999999999", "1.5", ""])
def test_invalid_pagination_is_refused(client: TestClient, value: str) -> None:
    response = client.get(TRANSACTIONS, params={"page": value})
    assert response.status_code == 422


def test_an_enormous_page_size_is_capped_or_refused(client: TestClient) -> None:
    """Unbounded page sizes are a denial-of-service primitive: one request that
    asks for every row is cheaper to send than to serve."""
    response = client.get(TRANSACTIONS, params={"page_size": 1_000_000})
    assert response.status_code == 422


@pytest.mark.parametrize("value", ["not-a-status", "PENDING; DROP TABLE x", "<script>", "", "null"])
def test_an_invalid_enum_is_refused(client: TestClient, value: str) -> None:
    response = client.get(TRANSACTIONS, params={"status": value})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "2026-13-45", "0000-00-00", "2026-08-25T99:99:99", "'; SELECT 1"],
)
def test_a_malformed_date_is_refused(client: TestClient, value: str) -> None:
    response = client.get("/api/reviews", params={"created_after": value})
    assert response.status_code == 422


def test_invalid_json_is_refused_without_a_stack_trace(client: TestClient) -> None:
    response = client.post(
        "/api/feedback",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "traceback" not in response.text.lower()


def test_a_wrong_json_type_is_refused(client: TestClient) -> None:
    for body in ("[]", '"a string"', "42", "null"):
        response = client.post(
            "/api/feedback", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 422


def test_an_oversized_body_is_refused_before_it_is_read(client: TestClient) -> None:
    """413 from the size middleware, checked against Content-Length, so a
    declared 100 MB upload costs nothing to reject."""
    payload = "x" * (2 * 1024 * 1024)
    response = client.post("/api/feedback", json={"notes": payload})
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_a_malformed_content_length_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/feedback",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "not-a-number"},
    )
    assert response.status_code in (400, 422)


#: Strings that have historically broken naive handling somewhere in the stack.
#: Built with `chr()` rather than written as literals so this source file stays
#: plain ASCII - a Python file containing a real null byte does not even parse,
#: and a bidirectional override in a source file is its own security problem.
NULL = chr(0)
UNICODE_EDGE_CASES = [
    NULL + "leading-null",
    "A" + NULL + "B",  # embedded, not leading
    chr(0xFEFF) + "byte-order-mark",
    chr(0x202E) + "right-to-left-override",
    ("e" + chr(0x301)) * 40,  # combining marks: 80 code points
    chr(0x1F600) * 20,  # astral plane
    chr(0x200B) * 50,  # zero-width spaces
]


@pytest.mark.parametrize("payload", UNICODE_EDGE_CASES)
def test_unicode_edge_cases_fail_safely(client: TestClient, payload: str) -> None:
    """None of these should reach a 500.

    Null bytes in particular have historically terminated C strings inside
    database drivers; the rest are the shapes that break naive length limits and
    display logic.
    """
    response = client.get(EXPLORER, params={"search": payload})
    assert response.status_code in (200, 400, 422)


def test_an_unknown_field_is_refused_on_ingestion(client: TestClient) -> None:
    """`extra="forbid"`. A caller must not be able to assert a risk outcome."""
    response = client.post(
        "/api/events/transactions",
        json={
            "transaction_id": "SIM_extra_field_001",
            "amount": "100.00",
            "currency": "INR",
            "customer_id": "SIM_CUS_1",
            "merchant_id": "mrc_0001",
            "payment_method": "card",
            "country": "IN",
            "city": "Mumbai",
            "timestamp": "2026-08-25T10:00:00+00:00",
            "is_fraud": True,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["fraud_probability", "risk_score", "decision", "anomaly_score"])
def test_a_caller_cannot_assert_a_risk_outcome(client: TestClient, field: str) -> None:
    response = client.post(
        "/api/events/transactions",
        json={
            "transaction_id": f"SIM_assert_{field}",
            "amount": "100.00",
            "currency": "INR",
            "customer_id": "SIM_CUS_1",
            "merchant_id": "mrc_0001",
            "payment_method": "card",
            "country": "IN",
            "city": "Mumbai",
            "timestamp": "2026-08-25T10:00:00+00:00",
            field: 0.99,
        },
    )
    assert response.status_code == 422


def test_a_404_does_not_echo_the_requested_id(client: TestClient) -> None:
    """Reflecting user input into an error body is how a JSON API ends up with
    a reflected-XSS finding despite never rendering HTML."""
    response = client.get("/api/transactions/%3Cscript%3Ealert(1)%3C/script%3E")
    assert response.status_code in (400, 404, 422)
    assert "<script>" not in response.text
