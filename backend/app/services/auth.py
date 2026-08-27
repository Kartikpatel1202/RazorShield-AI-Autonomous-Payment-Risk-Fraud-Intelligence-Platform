"""Credential verification and account lookup.

The service layer owns the rule that a login can fail for exactly one reason as
far as the caller is concerned. "No such account", "wrong password", "account
disabled" and "account has no password set" are four different situations
internally and one identical 401 on the wire, because telling them apart is how
an attacker enumerates who works here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models import User

logger = logging.getLogger(__name__)


class AuthFailure(StrEnum):
    """Why a login failed. Logged and counted; never returned to the client."""

    UNKNOWN_ACCOUNT = "unknown_account"
    NO_CREDENTIAL = "no_credential"
    BAD_PASSWORD = "bad_password"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class AuthResult:
    """Either a user, or a reason there is not one."""

    user: User | None
    failure: AuthFailure | None

    @property
    def succeeded(self) -> bool:
        return self.user is not None


def find_user_by_email(session: Session, email: str) -> User | None:
    """Look up an account by email, case-insensitively.

    Email is compared folded because humans type ``Ops.Admin@example.com`` and
    expect it to work, and because storing one casing while accepting another
    would otherwise let two accounts exist for what a person considers one
    address.
    """
    normalized = email.strip().lower()
    if not normalized:
        return None
    return session.scalars(
        select(User).where(func.lower(User.email) == normalized).limit(1)
    ).first()


def authenticate(session: Session, email: str, password: str) -> AuthResult:
    """Verify a credential pair.

    Every path performs a bcrypt comparison - including the one where the
    account does not exist - so response time does not reveal whether an address
    is registered. See :func:`app.core.security.verify_password`.
    """
    user = find_user_by_email(session, email)

    if user is None:
        verify_password(password, None)
        return AuthResult(user=None, failure=AuthFailure.UNKNOWN_ACCOUNT)

    if not user.password_hash:
        # Seeded accounts land here: the seed generator deliberately writes no
        # credentials, so a seeded user cannot be logged into until an operator
        # sets a password with `scripts/create_user.py`.
        verify_password(password, None)
        return AuthResult(user=None, failure=AuthFailure.NO_CREDENTIAL)

    if not verify_password(password, user.password_hash):
        return AuthResult(user=None, failure=AuthFailure.BAD_PASSWORD)

    if not user.is_active:
        # Checked after the password so a deactivated account is not detectable
        # by trying a wrong password against it.
        return AuthResult(user=None, failure=AuthFailure.INACTIVE)

    return AuthResult(user=user, failure=None)


def load_active_user(session: Session, user_id: int) -> User | None:
    """Fetch the account a verified token names, if it is still usable.

    A token is a bearer credential valid until it expires; this lookup is what
    makes deactivating an account take effect immediately rather than an hour
    later. It costs one indexed primary-key read per request.
    """
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class EmailAlreadyRegisteredError(Exception):
    """An account already exists for this address.

    Surfaced to the caller as a 409. That is an account-enumeration vector and
    a deliberate one: a signup form that accepts an address it will not create
    an account for, and says nothing, produces a user who cannot sign in and
    cannot tell why. Every consumer product makes this trade. What contains it
    is that the endpoint is rate limited, and that *login* and *password reset*
    - the two places an attacker would actually use the knowledge - reveal
    nothing at all.
    """


def register_viewer(session: Session, *, email: str, full_name: str, password: str) -> User:
    """Create a self-service account.

    Always ``VIEWER``. The role is not a parameter, so there is no code path -
    not a forgotten default, not a mass-assignment bug in a schema, not a future
    caller passing something through - by which public signup produces a
    privileged account. Elevating one is an administrator action through
    ``scripts/manage_users.py``.

    The caller is responsible for having validated password strength; this
    raises :class:`~app.core.security.PasswordTooLongError` on the bcrypt limit
    but does not re-run the policy.
    """
    from app.core.security import hash_password
    from app.models.enums import UserRole

    normalized = email.strip().lower()
    if find_user_by_email(session, normalized) is not None:
        raise EmailAlreadyRegisteredError(normalized)

    user = User(
        email=normalized,
        full_name=full_name.strip() or None,
        role=UserRole.VIEWER,
        is_active=True,
        password_hash=hash_password(password),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        # Two signups for the same address in the same instant. The unique index
        # on `users.email` is the real guard; this turns its error into the same
        # 409 the pre-check produces, rather than a 500.
        session.rollback()
        raise EmailAlreadyRegisteredError(normalized) from exc
    return user


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedReset:
    """A freshly minted reset capability.

    ``token`` is the raw value, held only long enough to put it in an email (or,
    in local development, in the response). It is never persisted and never
    logged - the database has its digest and nothing else.
    """

    token: str
    expires_at: datetime
    user: User


def request_password_reset(session: Session, email: str) -> IssuedReset | None:
    """Issue a reset token, or ``None`` if there is no eligible account.

    ``None`` covers three cases the caller must treat identically: no such
    address, a deactivated account, and an account that never had a password.
    The endpoint answers the same way regardless, so this function returning
    ``None`` is not an error condition - it is the ordinary path for an address
    nobody registered.

    Any outstanding tokens for the account are marked used first. A user who
    clicks the link three times should end with one live key, not three.
    """
    from app.core.security import (
        RESET_TOKEN_TTL_MINUTES,
        generate_reset_token,
        hash_reset_token,
    )
    from app.models import PasswordResetToken

    user = find_user_by_email(session, email)
    if user is None or not user.is_active:
        return None

    now = datetime.now(UTC)

    # Supersede anything still live for this account.
    outstanding = session.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for token_row in outstanding:
        token_row.used_at = now

    raw = generate_reset_token()
    expires_at = now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_reset_token(raw),
            expires_at=expires_at,
        )
    )
    session.flush()
    return IssuedReset(token=raw, expires_at=expires_at, user=user)


class ResetTokenError(Exception):
    """A reset token is unknown, expired or already spent."""


def consume_password_reset(session: Session, *, token: str, new_password: str) -> User:
    """Redeem a reset token and set the account's password.

    Raises :class:`ResetTokenError` for every rejection - unknown, expired,
    already used, or belonging to an account that has since been deactivated.
    One exception type, because the endpoint returns one message: telling a
    caller that their token is *expired* rather than *unknown* confirms the
    token was real, and a token is a bearer credential like any other.

    The token is marked used in the same transaction that writes the new hash,
    so there is no window in which a password change has landed and the token
    is still redeemable.
    """
    from app.core.security import hash_password, hash_reset_token
    from app.models import PasswordResetToken

    if not token:
        raise ResetTokenError("empty token")

    # Looked up by digest, so the raw token never reaches a query log.
    row = session.scalars(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(token))
    ).first()

    now = datetime.now(UTC)
    if row is None or not row.is_usable(now=now):
        raise ResetTokenError("token not usable")

    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ResetTokenError("account not usable")

    user.password_hash = hash_password(new_password)
    row.used_at = now

    # Any other live token for this account dies with it: resetting a password
    # should invalidate every outstanding way to reset it again.
    siblings = session.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all()
    for sibling in siblings:
        sibling.used_at = now

    session.flush()
    return user
