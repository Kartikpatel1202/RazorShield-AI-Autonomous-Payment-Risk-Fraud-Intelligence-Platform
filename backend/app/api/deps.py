"""Shared route dependencies: authentication, authorisation, rate limiting.

Routes declare *what they need*, never *who is allowed*:

    @router.post("/simulator/start", dependencies=[Depends(require(Permission.SIMULATOR_CONTROL))])

The mapping from permission to role lives in one place,
:mod:`app.core.permissions`, and can be re-read in full in under a minute. That
is the property that matters: an authorisation model you cannot hold in your
head is one you cannot audit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.metrics import authorization_denied_total, rate_limited_total
from app.core.observability import LifecycleEvent, log_lifecycle
from app.core.permissions import Permission, has_permission, permissions_for
from app.core.ratelimit import limiter
from app.core.security import TokenError, bearer_token, decode_access_token
from app.db.session import get_db
from app.models import User
from app.services.auth import load_active_user

logger = logging.getLogger(__name__)

#: Sent with every 401 so a browser client knows what kind of credential to
#: present. The realm is a constant and reveals nothing.
_AUTH_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="razorshield"'}


def _unauthenticated(reason: str) -> HTTPException:
    """A 401 whose body says nothing useful to a prober.

    ``reason`` is for the log, not the wire: "token expired" and "token invalid"
    are worth distinguishing in an incident review and worth hiding from whoever
    is trying tokens.
    """
    logger.info("Rejected request: %s", reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers=_AUTH_CHALLENGE,
    )


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the caller from the ``Authorization`` header.

    Verifying the signature is not enough on its own. The account is re-read
    from the database on every request so that deactivating a user takes effect
    at once rather than whenever their token happens to expire, and so a token
    naming a deleted account is refused rather than trusted for its claims.
    """
    try:
        token = bearer_token(request.headers.get("Authorization"))
        claims = decode_access_token(token, settings)
    except TokenError as exc:
        raise _unauthenticated(str(exc)) from exc

    user = load_active_user(session, claims.subject)
    if user is None:
        raise _unauthenticated("token names an unknown or inactive account")

    # The role is re-read from the row rather than trusted from the token: a
    # demotion must not wait an hour for the old token to expire.
    request.state.user_id = user.id
    request.state.user_role = str(user.role)
    return user


def require(*permissions: Permission) -> Callable[..., User]:
    """Build a dependency demanding every listed permission.

    Multiple permissions are ANDed. No endpoint currently needs OR, and adding
    it speculatively would make the route declarations harder to read than the
    thing they protect.
    """

    def _check(request: Request, user: User = Depends(get_current_user)) -> User:
        missing = [p for p in permissions if not has_permission(user.role, p)]
        if missing:
            required = missing[0]
            authorization_denied_total.labels(permission=str(required)).inc()
            log_lifecycle(
                LifecycleEvent.AUTHORIZATION_DENIED,
                level=logging.WARNING,
                actor_id=user.id,
                actor_role=str(user.role),
                required_permission=str(required),
                path=request.url.path,
            )
            # 403, not 404: the caller authenticated successfully, and hiding
            # the endpoint's existence from an authenticated colleague buys
            # nothing while making the UI impossible to debug.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' lacks the required permission '{required}'",
            )
        return user

    return _check


def current_permissions(user: User = Depends(get_current_user)) -> frozenset[Permission]:
    """Everything the caller may do. Used by ``/api/auth/me``."""
    return permissions_for(user.role)


def client_identity(request: Request) -> str:
    """A stable key for rate limiting.

    ``request.client.host`` is the peer address, which behind a reverse proxy is
    the proxy. Uvicorn is run with ``--proxy-headers`` in the compose stack so
    it rewrites that from ``X-Forwarded-For`` before we see it; parsing the
    header ourselves would mean trusting a value any client can set.
    """
    client = request.client
    return client.host if client else "unknown"


def rate_limit(bucket: str, setting: str) -> Callable[..., None]:
    """Build a per-route, per-client request cap.

    A closure rather than a callable class, and that is not a style preference:
    FastAPI resolves a dependency's annotations through its ``__globals__``,
    which a *class instance* does not have. An instance therefore has its
    ``Request`` annotation left as an unresolved forward reference and gets
    mistaken for a query parameter, which turns every protected endpoint into a
    422. A function has ``__globals__``, so its annotations resolve.

    ``setting`` is read from live settings on every call rather than captured at
    import, so raising a limit needs a restart of the process but not an edit to
    the route.
    """

    def _limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
        limit = int(getattr(settings, setting))
        decision = limiter.check(bucket, client_identity(request), limit)
        if decision.allowed:
            return

        rate_limited_total.labels(bucket=bucket).inc()
        log_lifecycle(
            LifecycleEvent.RATE_LIMITED,
            level=logging.WARNING,
            bucket=bucket,
            path=request.url.path,
            limit=decision.limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Retry later.",
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    return _limit


login_rate_limit = rate_limit("login", "rate_limit_login_per_minute")
signup_rate_limit = rate_limit("signup", "rate_limit_signup_per_minute")
password_reset_rate_limit = rate_limit("password_reset", "rate_limit_password_reset_per_minute")
ingest_rate_limit = rate_limit("ingest", "rate_limit_ingest_per_minute")
simulator_rate_limit = rate_limit("simulator", "rate_limit_simulator_per_minute")
feedback_rate_limit = rate_limit("feedback", "rate_limit_feedback_per_minute")
review_rate_limit = rate_limit("review", "rate_limit_review_per_minute")
