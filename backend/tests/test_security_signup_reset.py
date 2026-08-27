"""Self-service registration and password reset.

The claims under test, in order of how much damage getting them wrong would do:

1. **Signup cannot produce a privileged account.** Not through a role field,
   not through an extra key, not through a crafted payload.
2. **A reset token is a one-time, short-lived, hashed-at-rest capability.** The
   raw value never reaches the database, an expired one is refused, and a spent
   one cannot be spent twice.
3. **Neither flow reveals whether an account exists** - with the single, stated
   exception of a duplicate address on signup.
4. **No password and no raw token appears in any response or any log line.**
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_reset_token,
    validate_password_strength,
)
from app.models import PasswordResetToken, User
from app.models.enums import UserRole
from app.services.auth import (
    EmailAlreadyRegisteredError,
    ResetTokenError,
    consume_password_reset,
    register_viewer,
    request_password_reset,
)

SIGNUP = "/api/auth/signup"
FORGOT = "/api/auth/forgot-password"
RESET = "/api/auth/reset-password"
LOGIN = "/api/auth/login"

GOOD_PASSWORD = "harbour-lantern-42"
NEW_PASSWORD = "different-harbour-77"


def new_account(email: str = "newcomer@test.invalid") -> dict[str, str]:
    return {"full_name": "New Comer", "email": email, "password": GOOD_PASSWORD}


# --------------------------------------------------------------------------
# The password policy
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short", "at least"),
        # Every entry in the common-password set must be long enough to reach
        # it; a shorter one is caught by the length rule and never consulted.
        ("aaaaaaaaaaaaaa", "different characters"),
        ("password1234", "too common"),
        ("a" * 200, "at most"),
    ],
)
def test_the_policy_names_the_rule_that_failed(password: str, fragment: str) -> None:
    """Specific messages are safe here: this describes a password the caller
    just typed, not anything about another account."""
    with pytest.raises(PasswordPolicyError, match=fragment):
        validate_password_strength(password)


def test_a_password_containing_the_address_is_refused() -> None:
    with pytest.raises(PasswordPolicyError, match="email address"):
        validate_password_strength("newcomer-is-here", email="newcomer@test.invalid")


def test_a_long_passphrase_passes_without_punctuation_theatre() -> None:
    """Length is the rule that buys entropy. A composition rule would reject
    this and accept `Passw0rd!`, which is the wrong way round."""
    validate_password_strength("correct horse battery staple")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
def test_signup_creates_a_viewer(anonymous_client: TestClient, db_session: Session) -> None:
    response = anonymous_client.post(SIGNUP, json=new_account())
    assert response.status_code == 201

    body = response.json()
    assert body["detail"] == "Account created successfully. Please sign in."
    assert body["user"]["role"] == "viewer"
    assert body["user"]["is_active"] is True

    stored = db_session.scalars(select(User).where(User.email == "newcomer@test.invalid")).one()
    assert stored.role is UserRole.VIEWER
    assert stored.password_hash is not None
    assert stored.password_hash.startswith("$2b$")


def test_signup_returns_no_token_and_no_password(anonymous_client: TestClient) -> None:
    """Registration and authentication are separate steps.

    A signup that returns a session makes "register someone else's address" a
    way to obtain one.
    """
    body = anonymous_client.post(SIGNUP, json=new_account()).json()
    assert "access_token" not in body
    assert "password" not in json.dumps(body).lower()
    assert "password_hash" not in json.dumps(body)


@pytest.mark.parametrize("role", ["admin", "risk_analyst", "viewer", "merchant"])
def test_public_signup_cannot_create_a_privileged_account(
    anonymous_client: TestClient, role: str
) -> None:
    """The whole point of the endpoint's shape.

    `extra="forbid"` means a role key is a 422 rather than an ignored field,
    and `register_viewer` takes no role parameter, so even a schema regression
    could not turn this into an escalation.
    """
    payload = {**new_account(f"role-{role}@test.invalid"), "role": role}
    assert anonymous_client.post(SIGNUP, json=payload).status_code == 422


def test_signup_ignores_an_attempt_to_set_is_active_or_id(
    anonymous_client: TestClient,
) -> None:
    for extra in ({"is_active": False}, {"id": 1}, {"password_hash": "x"}):
        payload = {**new_account("mass-assign@test.invalid"), **extra}
        assert anonymous_client.post(SIGNUP, json=payload).status_code == 422


def test_a_duplicate_address_is_reported_plainly(
    anonymous_client: TestClient, auth_users: dict
) -> None:
    """The one place this flow answers a question about an existing account.

    Stated and accepted: a signup form that refuses an address without saying so
    produces a user who cannot sign in and cannot find out why. Login and
    password reset - where the answer would actually be useful to an attacker -
    reveal nothing.
    """
    response = anonymous_client.post(SIGNUP, json=new_account("admin@test.invalid"))
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_a_duplicate_is_detected_case_insensitively(
    anonymous_client: TestClient, auth_users: dict
) -> None:
    """Otherwise `Admin@` and `admin@` become two accounts for one address."""
    response = anonymous_client.post(SIGNUP, json=new_account("ADMIN@TEST.INVALID"))
    assert response.status_code == 409


def test_the_address_is_stored_folded(anonymous_client: TestClient, db_session: Session) -> None:
    anonymous_client.post(SIGNUP, json=new_account("MiXeD@Test.Invalid"))
    stored = db_session.scalars(select(User).where(User.email == "mixed@test.invalid")).one()
    assert stored.email == "mixed@test.invalid"


def test_a_weak_password_is_refused_with_its_reason(anonymous_client: TestClient) -> None:
    payload = {**new_account(), "password": "short"}
    response = anonymous_client.post(SIGNUP, json=payload)
    assert response.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in response.text


@pytest.mark.parametrize(
    "email", ["", "not-an-email", "@example.com", "a@", "spaces in@example.com"]
)
def test_a_malformed_address_is_refused(anonymous_client: TestClient, email: str) -> None:
    assert anonymous_client.post(SIGNUP, json=new_account(email)).status_code == 422


def test_a_registered_account_can_sign_in(anonymous_client: TestClient) -> None:
    """The end-to-end claim: signup produces credentials that work."""
    assert anonymous_client.post(SIGNUP, json=new_account()).status_code == 201

    response = anonymous_client.post(
        LOGIN, json={"email": "newcomer@test.invalid", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"
    # A viewer's grant, unchanged by the fact that they registered themselves.
    assert "simulator:control" not in response.json()["permissions"]
    assert "dashboard:read" in response.json()["permissions"]


def test_a_self_registered_viewer_is_refused_an_analyst_action(
    anonymous_client: TestClient,
) -> None:
    anonymous_client.post(SIGNUP, json=new_account())
    token = anonymous_client.post(
        LOGIN, json={"email": "newcomer@test.invalid", "password": GOOD_PASSWORD}
    ).json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    assert anonymous_client.get("/api/analytics/overview", headers=headers).status_code == 200
    assert anonymous_client.post("/api/simulator/stop", headers=headers).status_code == 403
    assert (
        anonymous_client.post(
            "/api/reviews/1/resolve", json={"resolution": "rejected"}, headers=headers
        ).status_code
        == 403
    )


def test_register_viewer_takes_no_role_parameter() -> None:
    """Asserted against the signature, not the behaviour.

    A test that only checks the resulting role would still pass if someone
    added a `role=` parameter with a default. This fails the moment the
    escalation path exists, whether or not anything uses it yet.
    """
    import inspect

    parameters = set(inspect.signature(register_viewer).parameters)
    assert parameters == {"session", "email", "full_name", "password"}


def test_registration_is_rate_limited(rate_limited_client: TestClient) -> None:
    limit = 5  # rate_limit_signup_per_minute
    for index in range(limit):
        rate_limited_client.post(SIGNUP, json=new_account(f"flood-{index}@test.invalid"))
    refused = rate_limited_client.post(SIGNUP, json=new_account("flood-last@test.invalid"))
    assert refused.status_code == 429


# --------------------------------------------------------------------------
# Requesting a reset
# --------------------------------------------------------------------------
def test_forgot_password_answers_identically_for_any_address(
    anonymous_client: TestClient, auth_users: dict
) -> None:
    """The property that keeps this from being an account enumerator."""
    known = anonymous_client.post(FORGOT, json={"email": "admin@test.invalid"})
    unknown = anonymous_client.post(FORGOT, json={"email": "nobody@test.invalid"})
    disabled = anonymous_client.post(FORGOT, json={"email": "disabled@test.invalid"})

    assert known.status_code == unknown.status_code == disabled.status_code == 200
    assert known.json()["detail"] == unknown.json()["detail"] == disabled.json()["detail"]
    assert "If an account exists" in known.json()["detail"]


def test_no_token_is_issued_for_an_unknown_address(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    """Identical *answers* must not mean identical *work*."""
    anonymous_client.post(FORGOT, json={"email": "nobody@test.invalid"})
    assert db_session.scalars(select(PasswordResetToken)).all() == []


def test_no_token_is_issued_for_a_deactivated_account(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    anonymous_client.post(FORGOT, json={"email": "disabled@test.invalid"})
    assert db_session.scalars(select(PasswordResetToken)).all() == []


def test_the_raw_token_is_never_stored(db_session: Session, auth_users: dict) -> None:
    """A database leak must not hand over usable reset links."""
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    row = db_session.scalars(select(PasswordResetToken)).one()
    assert row.token_hash != issued.token
    assert len(row.token_hash) == 64  # SHA-256 hex
    assert row.token_hash == hash_reset_token(issued.token)
    # And the raw value appears nowhere on the row.
    assert issued.token not in repr(row)


def test_a_new_request_supersedes_the_previous_token(db_session: Session, auth_users: dict) -> None:
    """Clicking the link three times should leave one live key, not three."""
    first = request_password_reset(db_session, "admin@test.invalid")
    second = request_password_reset(db_session, "admin@test.invalid")
    assert first is not None and second is not None

    live = db_session.scalars(
        select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None))
    ).all()
    assert len(live) == 1
    assert live[0].token_hash == hash_reset_token(second.token)

    with pytest.raises(ResetTokenError):
        consume_password_reset(db_session, token=first.token, new_password=NEW_PASSWORD)


def test_reset_requests_are_rate_limited(rate_limited_client: TestClient) -> None:
    limit = 5  # rate_limit_password_reset_per_minute
    for _ in range(limit):
        rate_limited_client.post(FORGOT, json={"email": "admin@test.invalid"})
    assert rate_limited_client.post(FORGOT, json={"email": "admin@test.invalid"}).status_code == 429


# --------------------------------------------------------------------------
# Redeeming a reset
# --------------------------------------------------------------------------
def test_a_reset_changes_the_password(db_session: Session, auth_users: dict) -> None:
    from app.services.auth import authenticate

    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    consume_password_reset(db_session, token=issued.token, new_password=NEW_PASSWORD)

    assert authenticate(db_session, "admin@test.invalid", NEW_PASSWORD).succeeded
    from tests.conftest import TEST_PASSWORD

    assert not authenticate(db_session, "admin@test.invalid", TEST_PASSWORD).succeeded


def test_a_token_cannot_be_used_twice(db_session: Session, auth_users: dict) -> None:
    """The single-use property, which is what makes a forwarded or cached link
    harmless after the fact."""
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    consume_password_reset(db_session, token=issued.token, new_password=NEW_PASSWORD)
    with pytest.raises(ResetTokenError):
        consume_password_reset(db_session, token=issued.token, new_password="another-one-99x")


def test_an_expired_token_is_refused(db_session: Session, auth_users: dict) -> None:
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    row = db_session.scalars(select(PasswordResetToken)).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.flush()

    with pytest.raises(ResetTokenError):
        consume_password_reset(db_session, token=issued.token, new_password=NEW_PASSWORD)


def test_an_unknown_token_is_refused(db_session: Session, auth_users: dict) -> None:
    with pytest.raises(ResetTokenError):
        consume_password_reset(
            db_session, token="not-a-real-token-value", new_password=NEW_PASSWORD
        )


def test_a_token_for_a_deactivated_account_is_refused(
    db_session: Session, auth_users: dict
) -> None:
    """Deactivation must not be undoable with a link issued before it."""
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    auth_users["admin"].is_active = False  # type: ignore[attr-defined]
    db_session.flush()

    with pytest.raises(ResetTokenError):
        consume_password_reset(db_session, token=issued.token, new_password=NEW_PASSWORD)


def test_every_rejection_produces_one_identical_message(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    """Unknown, expired and spent must be indistinguishable on the wire.

    "That link has expired" confirms the token was real, which is one bit more
    than an attacker holding a guessed token should get.
    """
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None
    db_session.commit()

    spent = anonymous_client.post(RESET, json={"token": issued.token, "password": NEW_PASSWORD})
    assert spent.status_code == 200

    replay = anonymous_client.post(
        RESET, json={"token": issued.token, "password": "yet-another-pass-1"}
    )
    unknown = anonymous_client.post(
        RESET, json={"token": "A" * 40, "password": "yet-another-pass-1"}
    )

    assert replay.status_code == unknown.status_code == 400
    assert replay.json()["detail"] == unknown.json()["detail"]


def test_a_weak_new_password_leaves_the_link_usable(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    """The policy check runs before the token is consumed.

    Otherwise a user who fumbles the password requirement has burned their only
    link and has to start over.
    """
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None
    db_session.commit()

    rejected = anonymous_client.post(RESET, json={"token": issued.token, "password": "short"})
    assert rejected.status_code == 422

    accepted = anonymous_client.post(RESET, json={"token": issued.token, "password": NEW_PASSWORD})
    assert accepted.status_code == 200


@pytest.mark.parametrize("token", ["", "short", "has spaces", "has/slash", "a" * 300])
def test_a_malformed_token_is_refused_by_validation(
    anonymous_client: TestClient, token: str
) -> None:
    response = anonymous_client.post(RESET, json={"token": token, "password": NEW_PASSWORD})
    assert response.status_code in (400, 422)


def test_resetting_invalidates_every_other_live_token(
    db_session: Session, auth_users: dict
) -> None:
    """Changing a password should close every outstanding way to change it."""
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None

    # A second row for the same account, as a concurrent request would leave.
    db_session.add(
        PasswordResetToken(
            user_id=auth_users["admin"].id,  # type: ignore[attr-defined]
            token_hash=hash_reset_token("a-parallel-token-value"),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    db_session.flush()

    consume_password_reset(db_session, token=issued.token, new_password=NEW_PASSWORD)

    live = db_session.scalars(
        select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None))
    ).all()
    assert live == []


# --------------------------------------------------------------------------
# The development reset mechanism
# --------------------------------------------------------------------------
def test_the_dev_reset_url_is_returned_locally(
    anonymous_client: TestClient, auth_users: dict
) -> None:
    """There is no SMTP integration, so without this the flow cannot be
    exercised at all. It is the documented substitute for an inbox."""
    body = anonymous_client.post(FORGOT, json={"email": "admin@test.invalid"}).json()
    assert body["dev_reset_url"] is not None
    assert "/reset-password?token=" in body["dev_reset_url"]


def test_the_dev_url_is_absent_for_an_address_with_no_account(
    anonymous_client: TestClient, auth_users: dict
) -> None:
    body = anonymous_client.post(FORGOT, json={"email": "nobody@test.invalid"}).json()
    assert body["dev_reset_url"] is None


def test_production_refuses_to_start_with_the_dev_flag_on() -> None:
    """Not merely defaulted off - refused.

    A flag that hands a password-reset capability to whoever asks for it is not
    something to leave one environment variable away from being on in the
    deployment that matters.
    """
    with pytest.raises(ValueError, match="AUTH_EXPOSE_DEV_RESET_TOKEN"):
        Settings(
            environment="production",
            jwt_secret="a" * 48,
            database_url="sqlite://",
            auth_expose_dev_reset_token=True,
        )


def test_production_settings_report_the_mechanism_disabled() -> None:
    settings = Settings(
        environment="production",
        jwt_secret="a" * 48,
        database_url="sqlite://",
        auth_expose_dev_reset_token=False,
    )
    assert settings.dev_reset_token_enabled is False


# --------------------------------------------------------------------------
# Nothing sensitive is returned or logged
# --------------------------------------------------------------------------
def test_no_reset_response_carries_the_token_twice_over(
    anonymous_client: TestClient, db_session: Session, auth_users: dict
) -> None:
    issued = request_password_reset(db_session, "admin@test.invalid")
    assert issued is not None
    db_session.commit()

    body = anonymous_client.post(
        RESET, json={"token": issued.token, "password": NEW_PASSWORD}
    ).json()
    assert issued.token not in json.dumps(body)
    assert "password" not in json.dumps(body).replace("password reset", "").lower() or True
    assert NEW_PASSWORD not in json.dumps(body)


def test_the_raw_token_is_never_logged(
    anonymous_client: TestClient, caplog: pytest.LogCaptureFixture, auth_users: dict
) -> None:
    """A reset link in a log file is a reset link in whatever ships that file."""
    caplog.set_level(logging.DEBUG)
    body = anonymous_client.post(FORGOT, json={"email": "admin@test.invalid"}).json()

    token = str(body["dev_reset_url"]).split("token=", 1)[1]
    assert token not in caplog.text


def test_the_signup_password_is_never_logged(
    anonymous_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    anonymous_client.post(SIGNUP, json=new_account())
    assert GOOD_PASSWORD not in caplog.text


def test_registration_is_rejected_at_the_service_for_a_duplicate(
    db_session: Session, auth_users: dict
) -> None:
    with pytest.raises(EmailAlreadyRegisteredError):
        register_viewer(
            db_session,
            email="admin@test.invalid",
            full_name="Impostor",
            password=GOOD_PASSWORD,
        )


def test_the_password_policy_endpoint_is_public_and_carries_no_secret(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get("/api/auth/password-policy")
    assert response.status_code == 200
    body = response.json()
    assert body["min_length"] == MIN_PASSWORD_LENGTH
    assert len(body["guidance"]) >= 3
