"""Authentication: password storage, token minting, and every way in that fails.

The claim under test is narrow and checkable: **the only way to obtain a usable
token is to present a correct password for an active account**, and every other
path - no header, wrong scheme, expired token, forged token, token for a deleted
or deactivated account, token minted for a different audience - ends in 401 with
a body that tells the caller nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    TokenError,
    bearer_token,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models import User
from app.models.enums import UserRole
from app.services.auth import AuthFailure, authenticate
from tests.conftest import TEST_PASSWORD, token_for

LOGIN = "/api/auth/login"
ME = "/api/auth/me"
PROTECTED = "/api/analytics/overview"


# --------------------------------------------------------------------------
# Password storage
# --------------------------------------------------------------------------
def test_a_password_is_never_stored_in_plaintext() -> None:
    hashed = hash_password("hunter2-hunter2")
    assert hashed != "hunter2-hunter2"
    assert "hunter2" not in hashed
    # bcrypt's own prefix, so this asserts the algorithm and not just "changed".
    assert hashed.startswith("$2b$")


def test_the_same_password_hashes_differently_every_time() -> None:
    """Salted. Two accounts with the same password must not share a hash.

    Otherwise a single glance at the table tells an attacker which users to
    attack together, and one cracked hash unlocks all of them.
    """
    first = hash_password("shared-password-value")
    second = hash_password("shared-password-value")
    assert first != second
    assert verify_password("shared-password-value", first)
    assert verify_password("shared-password-value", second)


def test_verification_rejects_the_wrong_password() -> None:
    hashed = hash_password("correct-password-here")
    assert verify_password("correct-password-here", hashed)
    assert not verify_password("correct-password-heRe", hashed)
    assert not verify_password("", hashed)


def test_an_over_long_password_is_refused_rather_than_truncated() -> None:
    """bcrypt ignores everything past 72 bytes. Truncating would mean two
    different passwords opening the same account, so both hashing and
    verification refuse instead."""
    too_long = "a" * (MAX_PASSWORD_BYTES + 1)
    with pytest.raises(PasswordTooLongError):
        hash_password(too_long)

    hashed = hash_password("a" * MAX_PASSWORD_BYTES)
    # The 73-byte string shares its first 72 bytes with the stored password and
    # must still not authenticate.
    assert not verify_password(too_long, hashed)


def test_verification_against_a_missing_hash_is_false_not_an_error() -> None:
    """Seeded accounts have no credential; they must not crash the login path."""
    assert not verify_password("anything", None)
    assert not verify_password("anything", "")
    assert not verify_password("anything", "not-a-bcrypt-hash")


# --------------------------------------------------------------------------
# The service layer
# --------------------------------------------------------------------------
def test_authenticate_distinguishes_failures_internally(
    db_session: Session, auth_users: dict[str, object]
) -> None:
    """Four different reasons exist for the log; the API collapses them to one.

    Keeping them apart here is what lets an operator tell "someone is guessing
    passwords for a real account" from "someone is enumerating addresses".
    """
    assert authenticate(db_session, "nobody@test.invalid", TEST_PASSWORD).failure is (
        AuthFailure.UNKNOWN_ACCOUNT
    )
    assert authenticate(db_session, "admin@test.invalid", "wrong").failure is (
        AuthFailure.BAD_PASSWORD
    )
    assert authenticate(db_session, "disabled@test.invalid", TEST_PASSWORD).failure is (
        AuthFailure.INACTIVE
    )
    assert authenticate(db_session, "admin@test.invalid", TEST_PASSWORD).succeeded


def test_a_seeded_account_cannot_be_logged_into(db_session: Session) -> None:
    """The seed writes no credentials, so seeded users have no way in.

    This is the property that stops a demo database from shipping with working
    accounts nobody chose the password for.
    """
    seeded = db_session.query(User).filter(User.password_hash.is_(None)).first()
    assert seeded is not None, "the seed should create credential-less accounts"

    result = authenticate(db_session, seeded.email, TEST_PASSWORD)
    assert not result.succeeded
    assert result.failure is AuthFailure.NO_CREDENTIAL


def test_email_matching_is_case_insensitive(db_session: Session, auth_users: dict) -> None:
    assert authenticate(db_session, "ADMIN@TEST.INVALID", TEST_PASSWORD).succeeded
    assert authenticate(db_session, "  Admin@Test.Invalid  ", TEST_PASSWORD).succeeded


# --------------------------------------------------------------------------
# Token minting and verification
# --------------------------------------------------------------------------
def test_a_minted_token_round_trips() -> None:
    issued = create_access_token(user_id=7, email="a@b.invalid", role="admin")
    claims = decode_access_token(issued.token)
    assert claims.subject == 7
    assert claims.email == "a@b.invalid"
    assert claims.role == "admin"
    assert claims.expires_at == issued.expires_at


def test_an_expired_token_is_refused() -> None:
    issued = create_access_token(
        user_id=1,
        email="a@b.invalid",
        role="admin",
        now=datetime.now(UTC) - timedelta(hours=2),
        ttl=timedelta(minutes=1),
    )
    with pytest.raises(TokenError):
        decode_access_token(issued.token)


def test_a_token_signed_with_another_key_is_refused() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": "1",
            "email": "a@b.invalid",
            "role": "admin",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        "a-completely-different-signing-key",
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_an_unsigned_token_is_refused() -> None:
    """`alg: none` is the oldest JWT attack there is.

    It works against any verifier that reads the algorithm from the token's own
    header. Ours passes a fixed one-element list to PyJWT, so the header is
    never consulted.
    """
    settings = get_settings()
    unsigned = jwt.encode(
        {
            "sub": "1",
            "email": "a@b.invalid",
            "role": "admin",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_access_token(unsigned)


def test_a_token_for_another_audience_is_refused() -> None:
    settings = get_settings()
    other = jwt.encode(
        {
            "sub": "1",
            "email": "a@b.invalid",
            "role": "admin",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
            "aud": "some-other-service",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(other)


def test_a_token_from_another_issuer_is_refused() -> None:
    settings = get_settings()
    other = jwt.encode(
        {
            "sub": "1",
            "email": "a@b.invalid",
            "role": "admin",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iss": "someone-else",
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(other)


@pytest.mark.parametrize(
    "value",
    ["", "not-a-jwt", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.xxxx", "Bearer Bearer x"],
)
def test_malformed_tokens_are_refused(value: str) -> None:
    with pytest.raises(TokenError):
        decode_access_token(value)


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic abc123", "Token abc123", "abc123"],
)
def test_unsupported_authorization_schemes_are_refused(header: str | None) -> None:
    with pytest.raises(TokenError):
        bearer_token(header)


def test_the_bearer_scheme_is_case_insensitive() -> None:
    """RFC 7235 says the scheme is case-insensitive; some clients send `bearer`."""
    assert bearer_token("bearer abc123") == "abc123"
    assert bearer_token("BEARER abc123") == "abc123"


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------
def test_login_returns_a_token_and_the_documented_shape(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        LOGIN, json={"email": "analyst@test.invalid", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"access_token", "token_type", "expires_at", "user", "role", "permissions"}
    assert body["token_type"] == "bearer"
    assert body["role"] == "risk_analyst"
    assert "reviews:resolve" in body["permissions"]
    assert "simulator:control" not in body["permissions"]

    # The response describes the account without ever carrying a credential.
    assert set(body["user"]) == {"id", "email", "full_name", "role", "is_active"}
    assert "password" not in response.text.lower()


def test_the_returned_token_actually_works(anonymous_client: TestClient) -> None:
    token = anonymous_client.post(
        LOGIN, json={"email": "viewer@test.invalid", "password": TEST_PASSWORD}
    ).json()["access_token"]

    response = anonymous_client.get(PROTECTED, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("admin@test.invalid", "wrong-password"),
        ("nobody@test.invalid", TEST_PASSWORD),
        ("disabled@test.invalid", TEST_PASSWORD),
    ],
)
def test_every_login_failure_looks_identical(
    anonymous_client: TestClient, email: str, password: str
) -> None:
    """Wrong password, unknown address and disabled account are one response.

    Any difference here - a distinct message, a distinct status, even a distinct
    body length - turns this endpoint into an account enumerator.
    """
    response = anonymous_client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_rejects_an_unknown_field(anonymous_client: TestClient) -> None:
    """`extra="forbid"`: a caller must not be able to smuggle a role in."""
    response = anonymous_client.post(
        LOGIN,
        json={"email": "viewer@test.invalid", "password": TEST_PASSWORD, "role": "admin"},
    )
    assert response.status_code == 422


def test_login_rejects_an_over_long_password(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        LOGIN, json={"email": "admin@test.invalid", "password": "a" * 200}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("email", ["", "not-an-email", "@example.com", "a@", "a b@c.d"])
def test_login_rejects_a_malformed_address(anonymous_client: TestClient, email: str) -> None:
    response = anonymous_client.post(LOGIN, json={"email": email, "password": TEST_PASSWORD})
    assert response.status_code == 422


def test_me_reflects_the_live_account(client: TestClient, auth_users: dict) -> None:
    response = client.get(ME)
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "admin@test.invalid"
    assert body["role"] == "admin"
    assert "system:admin" in body["permissions"]


def test_a_deactivated_account_is_refused_immediately(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    """Deactivation must not wait for the token to expire.

    This is the whole reason the account is re-read on every request rather than
    trusted from the token's claims: without it, revoking access would take up
    to a full token lifetime.
    """
    user = auth_users["admin"]
    token = token_for(user)
    headers = {"Authorization": f"Bearer {token}"}
    assert anonymous_client.get(PROTECTED, headers=headers).status_code == 200

    user.is_active = False  # type: ignore[attr-defined]
    db_session.flush()

    assert anonymous_client.get(PROTECTED, headers=headers).status_code == 401


def test_a_role_change_takes_effect_without_a_new_token(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    """The role comes from the row, not the token, so a demotion is immediate."""
    user = auth_users["admin"]
    headers = {"Authorization": f"Bearer {token_for(user)}"}
    assert anonymous_client.post("/api/simulator/stop", headers=headers).status_code == 200

    user.role = UserRole.VIEWER  # type: ignore[attr-defined]
    db_session.flush()

    # The token still says "admin". The database says otherwise, and it wins.
    assert anonymous_client.post("/api/simulator/stop", headers=headers).status_code == 403


def test_a_token_naming_a_nonexistent_account_is_refused(anonymous_client: TestClient) -> None:
    issued = create_access_token(user_id=999_999, email="ghost@test.invalid", role="admin")
    response = anonymous_client.get(PROTECTED, headers={"Authorization": f"Bearer {issued.token}"})
    assert response.status_code == 401


def test_an_unauthenticated_request_gets_401_with_a_challenge(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get(PROTECTED)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.json() == {"detail": "Not authenticated"}


def test_the_401_body_never_explains_why(anonymous_client: TestClient) -> None:
    """ "expired" and "invalid" are worth logging and worth hiding.

    Telling a prober which of their forged tokens was *nearly* right is free
    help they should not get.
    """
    expired = create_access_token(
        user_id=1,
        email="a@b.invalid",
        role="admin",
        now=datetime.now(UTC) - timedelta(days=1),
        ttl=timedelta(minutes=1),
    ).token

    for token in (expired, "garbage", "a.b.c"):
        response = anonymous_client.get(PROTECTED, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}


def test_logout_acknowledges_without_claiming_to_revoke(client: TestClient) -> None:
    """The endpoint must not lie about what it does.

    A JWT cannot be revoked without shared state this design deliberately does
    not have, so the response says so rather than implying the token is dead.
    """
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    detail = response.json()["detail"].lower()
    assert "discard" in detail
    assert "until it expires" in detail


def test_the_token_still_works_after_logout(client: TestClient) -> None:
    """Documents the real behaviour rather than the comfortable one."""
    client.post("/api/auth/logout")
    assert client.get(ME).status_code == 200


def test_health_endpoints_stay_open(anonymous_client: TestClient) -> None:
    """An orchestrator has no credential. Gating liveness behind one would make
    a token problem present as a dead process."""
    for path in ("/health", "/health/live", "/health/ready"):
        assert anonymous_client.get(path).status_code in (200, 503)
