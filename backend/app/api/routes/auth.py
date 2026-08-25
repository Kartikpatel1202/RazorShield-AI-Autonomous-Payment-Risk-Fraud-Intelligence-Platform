"""Authentication endpoints.

Three endpoints, one of which does almost nothing on purpose - see
:func:`logout`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import current_permissions, get_current_user, login_rate_limit
from app.core.config import Settings, get_settings
from app.core.metrics import auth_attempts_total
from app.core.observability import LifecycleEvent, log_lifecycle
from app.core.permissions import Permission, permissions_for
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    SessionResponse,
    UserProfile,
)
from app.services.auth import authenticate

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
