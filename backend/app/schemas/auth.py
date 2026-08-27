"""Request and response models for authentication.

``LoginRequest`` is the one place in the API where a secret crosses the
boundary. It is a normal Pydantic model with two deliberate properties: the
password field is never echoed in any response model, and the length bound
matches what bcrypt can hash intact so an over-long password is refused with a
422 rather than silently truncated to its first 72 bytes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import MAX_PASSWORD_BYTES
from app.models.enums import UserRole


class LoginRequest(BaseModel):
    """Credentials presented to ``POST /api/auth/login``."""

    # Unknown keys are rejected rather than ignored: a caller sending
    # `{"email":..., "password":..., "role":"admin"}` should learn that the
    # field means nothing here, not have it quietly dropped.
    model_config = ConfigDict(extra="forbid")

    # A bounded string, not `EmailStr`. The value is only ever a lookup key -
    # it is compared, folded, against a column, never parsed or sent anywhere -
    # and strict address validation on the *login* form locks out accounts whose
    # address is legitimate but unusual (`ops@company.internal`, a special-use
    # domain, a plus-tag some validator dislikes). Address shape is worth
    # checking when an account is created, which is what `scripts/manage_users.py`
    # is for; at sign-in it only decides who gets told "invalid email or
    # password" by a slower route.
    #
    # The pattern excludes whitespace and control characters, so nothing here
    # can reach a log line or a header as a second line.
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^\s@]{1,64}@[^\s@]{1,190}$")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)


class UserProfile(BaseModel):
    """The caller's own account. Contains no credential material."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool


class LoginResponse(BaseModel):
    """A minted access token and the identity it carries."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserProfile
    role: UserRole
    #: Every permission the role holds, so the console can hide controls it
    #: knows will be refused. The server re-checks all of them regardless: this
    #: list is a usability affordance, never the enforcement point.
    permissions: list[str]


class SessionResponse(BaseModel):
    """``GET /api/auth/me`` - who the bearer token says I am, right now."""

    user: UserProfile
    role: UserRole
    permissions: list[str]


class LogoutResponse(BaseModel):
    """``POST /api/auth/logout`` - acknowledgement, plus what it does not do."""

    status: Literal["ok"] = "ok"
    detail: str


# --------------------------------------------------------------------------
# Self-service registration
# --------------------------------------------------------------------------

#: Shared by every address field. Same shape as `LoginRequest.email` - see the
#: note there for why this is a bounded pattern rather than `EmailStr`.
EMAIL_PATTERN = r"^[^\s@]{1,64}@[^\s@]{1,190}$"


class SignupRequest(BaseModel):
    """A public registration.

    Note what is *absent*: there is no ``role`` field, and ``extra="forbid"``
    means adding one is a 422 rather than a silently ignored key. The service
    that consumes this does not take a role parameter either, so there are two
    independent reasons a self-service signup cannot produce an administrator.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    #: Bounded at the bcrypt limit here; the strength policy is applied in the
    #: endpoint so its message can name the specific rule that failed.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)


class SignupResponse(BaseModel):
    """Confirmation of a created account.

    Carries no token. Registration and authentication are separate steps on
    purpose: a signup that silently signs you in makes "create an account for
    someone else's address" a way to obtain a live session, and it hides from
    the user that a password is a thing they now have to remember.
    """

    status: Literal["created"] = "created"
    detail: str
    user: UserProfile


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)


class ForgotPasswordResponse(BaseModel):
    """Always the same, whether or not the address is registered.

    ``detail`` is a fixed string. Not "we sent you an email" - which would be a
    claim the server cannot make about an address it has never seen - but the
    conditional phrasing that is true either way.
    """

    status: Literal["ok"] = "ok"
    detail: str

    #: **Local development only.** Populated when `AUTH_EXPOSE_DEV_RESET_TOKEN`
    #: is on, which the configuration refuses to allow in production. There is
    #: no SMTP integration in this project, so without this the reset flow could
    #: not be exercised at all; it is the documented substitute for an inbox,
    #: not a feature.
    #:
    #: It is also, unavoidably, an account-existence oracle: a registered
    #: address gets a link and an unregistered one does not. That is precisely
    #: why it cannot be switched on in the deployment that matters.
    dev_reset_url: str | None = None
    dev_expires_at: datetime | None = None


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The raw token from the link. Bounded so an enormous body cannot reach the
    #: hash function, and pattern-constrained to the URL-safe base64 alphabet
    #: `secrets.token_urlsafe` produces.
    token: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)


class ResetPasswordResponse(BaseModel):
    status: Literal["ok"] = "ok"
    detail: str


class PasswordPolicyResponse(BaseModel):
    """What the console shows beside the password field.

    Served rather than duplicated in the frontend so the rule the form advertises
    and the rule the server enforces cannot drift apart.
    """

    min_length: int
    max_bytes: int
    guidance: list[str]
