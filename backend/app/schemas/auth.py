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
