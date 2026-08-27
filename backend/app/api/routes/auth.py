"""Authentication endpoints.

The whole account lifecycle: register, sign in, inspect the session, sign out,
request a password reset, redeem one.

Two rules run through all of it.

**One answer per question.** ``login`` returns the same 401 for an unknown
address, a wrong password and a disabled account. ``forgot-password`` returns
the same 200 whether or not the address is registered. ``reset-password``
returns the same 400 for an unknown, expired and already-spent token. Each of
those is a place where a more helpful message would be an oracle.

The one deliberate exception is ``signup``, which reports a duplicate address.
That is an enumeration vector and an accepted one - a signup form that quietly
refuses to create an account produces a user who cannot sign in and cannot find
out why. It is rate limited, and the two endpoints an attacker would actually
use the answer against reveal nothing.

**No secret is returned or logged.** No response model carries a password or a
reset token; the reset URL appears only under a development flag the
configuration will not accept in production, and the lifecycle log records ids
and timestamps, never credentials.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    current_permissions,
    get_current_user,
    login_rate_limit,
    password_reset_rate_limit,
    signup_rate_limit,
)
from app.core.config import Settings, get_settings
from app.core.metrics import auth_attempts_total
from app.core.observability import LifecycleEvent, log_lifecycle
from app.core.permissions import Permission, permissions_for
from app.core.security import (
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    PasswordTooLongError,
    create_access_token,
    validate_password_strength,
)
from app.db.session import get_db
from app.models import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PasswordPolicyResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SessionResponse,
    SignupRequest,
    SignupResponse,
    UserProfile,
)
from app.services.auth import (
    EmailAlreadyRegisteredError,
    ResetTokenError,
    authenticate,
    consume_password_reset,
    register_viewer,
    request_password_reset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _sorted(permissions: frozenset[Permission]) -> list[str]:
    """Permissions as a stable, sorted list so responses are comparable."""
    return sorted(str(permission) for permission in permissions)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Exchange credentials for an access token",
    dependencies=[Depends(login_rate_limit)],
)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    """Verify a password and mint a short-lived bearer token.

    Rate limited per client address. Every failure - unknown address, wrong
    password, disabled account - returns the same 401 with the same body; the
    real reason goes to the log, where it is useful and not adversary-readable.
    """
    result = authenticate(session, payload.email, payload.password)

    if not result.succeeded:
        auth_attempts_total.labels(outcome="failure").inc()
        log_lifecycle(
            LifecycleEvent.AUTH_FAILED,
            level=logging.WARNING,
            # The address is logged; the password is not, and is not even in
            # scope here beyond the call above.
            email=payload.email,
            reason=str(result.failure),
            client=request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": 'Bearer realm="razorshield"'},
        )

    user = result.user
    assert user is not None  # narrowed by `succeeded`; kept for the type checker
    issued = create_access_token(
        user_id=user.id, email=user.email, role=str(user.role), settings=settings
    )
    permissions = permissions_for(user.role)

    auth_attempts_total.labels(outcome="success").inc()
    log_lifecycle(
        LifecycleEvent.AUTH_SUCCEEDED,
        actor_id=user.id,
        actor_role=str(user.role),
        expires_at=issued.expires_at.isoformat(),
    )

    return LoginResponse(
        access_token=issued.token,
        expires_at=issued.expires_at,
        user=UserProfile.model_validate(user),
        role=user.role,
        permissions=_sorted(permissions),
    )


@router.get("/me", response_model=SessionResponse, summary="The current session")
def me(
    user: User = Depends(get_current_user),
    permissions: frozenset[Permission] = Depends(current_permissions),
) -> SessionResponse:
    """Re-resolve the bearer token against the live account.

    The console calls this on load to decide which navigation entries to render.
    It reflects the database, not the token, so a role changed by an
    administrator is visible on the next page load.
    """
    return SessionResponse(
        user=UserProfile.model_validate(user),
        role=user.role,
        permissions=_sorted(permissions),
    )


@router.post("/logout", response_model=LogoutResponse, summary="End the client session")
def logout(user: User = Depends(get_current_user)) -> LogoutResponse:
    """Acknowledge a logout. Deliberately does not revoke the token.

    A JWT is self-contained: nothing server-side is consulted to validate one,
    which is what makes it cheap and what makes it un-revocable without adding
    exactly the shared state this design avoids. Logging out therefore means the
    client discards the token, and the token remains technically valid until it
    expires.

    That is a real, bounded weakness and the honest mitigations are the ones
    already in place: a one-hour lifetime, and a per-request account lookup so a
    *deactivated* user is refused immediately regardless of what token they
    hold. A deployment that needs true revocation adds a token-id denylist -
    which is a shared-state decision, not a code-shape one.
    """
    log_lifecycle(LifecycleEvent.AUTH_SUCCEEDED, actor_id=user.id, action="logout")
    return LogoutResponse(
        detail=(
            "Discard the access token on the client. It stays valid until it expires; "
            "deactivating the account revokes access immediately."
        )
    )


# --------------------------------------------------------------------------
# Self-service registration
# --------------------------------------------------------------------------
@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a viewer account",
    dependencies=[Depends(signup_rate_limit)],
    responses={
        409: {"description": "An account already exists for this address"},
        422: {"description": "The password does not meet the policy"},
        429: {"description": "Rate limit exceeded"},
    },
)
def signup(
    payload: SignupRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> SignupResponse:
    """Register a new account, always as a ``VIEWER``.

    Three separate things stop this from producing a privileged account: the
    request schema has no role field and forbids unknown keys, the service
    function takes no role parameter, and ``test_security_rbac`` asserts the
    resulting role. Elevation is an administrator action through
    ``scripts/manage_users.py``.

    No token is returned. Registration and authentication are separate steps: a
    signup that silently signs you in makes "register someone else's address" a
    way to obtain a live session.
    """
    try:
        validate_password_strength(payload.password, email=payload.email)
    except PasswordPolicyError as exc:
        # 422 naming the specific rule. Safe to be specific: this describes a
        # password the caller just typed, not anything about another account.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        user = register_viewer(
            session,
            email=payload.email,
            full_name=payload.full_name,
            password=payload.password,
        )
    except EmailAlreadyRegisteredError as exc:
        log_lifecycle(
            LifecycleEvent.AUTH_FAILED,
            level=logging.INFO,
            action="signup_duplicate",
            email=payload.email,
            client=request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists. Try signing in instead.",
        ) from exc
    except PasswordTooLongError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    session.commit()

    # The address and the assigned role; never the password, and never anything
    # derived from it.
    log_lifecycle(
        LifecycleEvent.AUTH_SUCCEEDED,
        action="signup",
        actor_id=user.id,
        actor_role=str(user.role),
        email=user.email,
    )
    return SignupResponse(
        detail="Account created successfully. Please sign in.",
        user=UserProfile.model_validate(user),
    )


@router.get(
    "/password-policy",
    response_model=PasswordPolicyResponse,
    summary="The password rules the server enforces",
)
def password_policy() -> PasswordPolicyResponse:
    """What the signup and reset forms should tell the user.

    Served rather than restated in the frontend, so the rule the form advertises
    and the rule the server enforces cannot drift apart.
    """
    return PasswordPolicyResponse(
        min_length=MIN_PASSWORD_LENGTH,
        max_bytes=MAX_PASSWORD_BYTES,
        guidance=[
            f"At least {MIN_PASSWORD_LENGTH} characters",
            "At least 5 different characters",
            "Not a commonly used password",
            "Does not contain your email address",
        ],
    )


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------

#: The single answer `forgot-password` gives, whatever the address turns out to
#: be. A module constant so no future edit can make one branch say something the
#: other does not.
RESET_ACKNOWLEDGEMENT = (
    "If an account exists for this email, you will receive instructions to reset your password."
)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request a password reset link",
    dependencies=[Depends(password_reset_rate_limit)],
    responses={429: {"description": "Rate limit exceeded"}},
)
def forgot_password(
    payload: ForgotPasswordRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ForgotPasswordResponse:
    """Issue a reset token for the address, if it belongs to a live account.

    Answers identically either way. An unknown address, a deactivated account
    and a successful issue all produce the same 200 and the same sentence -
    otherwise this endpoint becomes the account enumerator that ``login`` was
    carefully built not to be.

    The raw token is returned only under `AUTH_EXPOSE_DEV_RESET_TOKEN`, which
    the configuration refuses to accept in production. It is never logged.
    """
    issued = request_password_reset(session, payload.email)
    session.commit()

    if issued is not None:
        # The user id and the expiry; never the token, not even truncated.
        log_lifecycle(
            LifecycleEvent.AUTH_SUCCEEDED,
            action="password_reset_requested",
            actor_id=issued.user.id,
            expires_at=issued.expires_at.isoformat(),
        )

    response = ForgotPasswordResponse(detail=RESET_ACKNOWLEDGEMENT)

    if issued is not None and settings.dev_reset_token_enabled:
        base = settings.frontend_base_url.rstrip("/")
        response.dev_reset_url = f"{base}/reset-password?token={issued.token}"
        response.dev_expires_at = issued.expires_at

    return response


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Set a new password using a reset token",
    dependencies=[Depends(password_reset_rate_limit)],
    responses={
        400: {"description": "The link is invalid, expired or already used"},
        422: {"description": "The password does not meet the policy"},
        429: {"description": "Rate limit exceeded"},
    },
)
def reset_password(
    payload: ResetPasswordRequest,
    session: Session = Depends(get_db),
) -> ResetPasswordResponse:
    """Redeem a reset token and set the account\'s password.

    Unknown, expired and already-used tokens produce one identical 400. Telling
    a caller which of the three applies confirms the token was real, and a reset
    token is a bearer credential like any other.

    The policy check runs before the token is consumed, so a user who fumbles
    the password requirement still has a live link to try again with.
    """
    try:
        validate_password_strength(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        user = consume_password_reset(session, token=payload.token, new_password=payload.password)
    except ResetTokenError as exc:
        session.rollback()
        log_lifecycle(
            LifecycleEvent.AUTH_FAILED,
            level=logging.WARNING,
            action="password_reset_rejected",
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This password reset link is no longer valid. "
                "Request a new one from the sign-in page."
            ),
        ) from exc
    except PasswordTooLongError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    session.commit()
    log_lifecycle(
        LifecycleEvent.AUTH_SUCCEEDED,
        action="password_reset_completed",
        actor_id=user.id,
    )
    return ResetPasswordResponse(
        detail="Password updated. You can now sign in with your new password."
    )
