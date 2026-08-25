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
from enum import StrEnum

from sqlalchemy import func, select
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
