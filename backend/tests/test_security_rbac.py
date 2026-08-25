"""Authorisation: who may do what, and the invariant that nothing is unguarded.

Two kinds of test here, and the second is the one that will still be earning its
keep in a year.

**Case tests** exercise the brief's scenarios directly: a viewer attempting an
analyst action, an analyst attempting an admin action, an administrator
succeeding. They prove the wiring is right today.

**The inventory test** asserts that *every* route under the API prefix except
login carries a permission dependency. That one catches the failure that
actually happens - a new endpoint added in six months with the dependency
forgotten - which no amount of case-by-case testing would notice.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permissions_for,
)
from app.models.enums import UserRole

# --------------------------------------------------------------------------
# The permission table itself
# --------------------------------------------------------------------------


def test_every_role_in_the_enum_has_a_grant() -> None:
    """A role added to the enum but forgotten in the table must be caught here.

    `permissions_for` fails closed on an unmapped role, so the failure mode is a
    user who can do nothing rather than a user who can do everything - but a
    silently powerless role is still a bug, and this is where it surfaces.
    """
    missing = [role for role in UserRole if role not in ROLE_PERMISSIONS]
    assert missing == []


def test_the_viewer_grant_is_read_only() -> None:
    write_permissions = {
        Permission.INVESTIGATIONS_RUN,
        Permission.RISK_SCORE,
        Permission.REVIEWS_RESOLVE,
        Permission.FEEDBACK_WRITE,
        Permission.TRANSACTIONS_INGEST,
        Permission.SIMULATOR_CONTROL,
        Permission.SYSTEM_ADMIN,
    }
    assert permissions_for(UserRole.VIEWER) & write_permissions == set()


def test_an_analyst_can_act_but_not_administer() -> None:
    analyst = permissions_for(UserRole.RISK_ANALYST)
    assert Permission.REVIEWS_RESOLVE in analyst
    assert Permission.FEEDBACK_WRITE in analyst
    assert Permission.INVESTIGATIONS_RUN in analyst
    # The line the brief draws: simulator control and system administration are
    # not analyst powers.
    assert Permission.SIMULATOR_CONTROL not in analyst
    assert Permission.SYSTEM_ADMIN not in analyst


def test_roles_are_nested_viewer_inside_analyst_inside_admin() -> None:
    viewer = permissions_for(UserRole.VIEWER)
    analyst = permissions_for(UserRole.RISK_ANALYST)
    admin = permissions_for(UserRole.ADMIN)
    assert viewer < analyst < admin
    assert admin == set(Permission)


def test_a_merchant_holds_no_console_permission() -> None:
    """A merchant is described by this platform, not an operator of it.

    Such an account can authenticate - it is a real user - and is then refused
    everywhere, which keeps "who you are" separate from "what you may do".
    """
    assert permissions_for(UserRole.MERCHANT) == frozenset()
    for permission in Permission:
        assert not has_permission(UserRole.MERCHANT, permission)


# --------------------------------------------------------------------------
# The inventory invariant
# --------------------------------------------------------------------------

#: Endpoints that must stay reachable without a credential, and why.
PUBLIC_ROUTES = {
    # The only way to obtain a credential.
    "/api/auth/login",
}

#: Endpoints that authenticate but hold no permission requirement, and why.
#: Both answer questions about the caller's *own* session, so gating them on a
#: permission would mean a merchant account - which holds none - could not even
#: discover that it holds none, or sign out.
SELF_SERVICE_ROUTES = {
    "/api/auth/me",
    "/api/auth/logout",
}


def _has_dependency(route: object, qualname: str) -> bool:
    """True when ``qualname`` appears anywhere in a route's dependency tree."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False

    stack = list(dependant.dependencies)
    while stack:
        dependency = stack.pop()
        call = getattr(dependency, "call", None)
        if getattr(call, "__qualname__", "") == qualname:
            return True
        stack.extend(dependency.dependencies)
    return False


def _permission_guarded(route: object) -> bool:
    """True when a route runs the permission check.

    `require()` returns a closure named `_check`; matching on the qualified name
    is how this recognises it without reaching into FastAPI internals.
    """
    return _has_dependency(route, "require.<locals>._check")


def _authenticated(route: object) -> bool:
    return _has_dependency(route, "get_current_user")


def test_every_api_route_requires_a_permission(app: FastAPI) -> None:
    """The invariant: no endpoint under /api is reachable without authorisation.

    This is the test that survives contact with future development. A new route
    added without a `dependencies=[Depends(require(...))]` fails here on the
    first run, before anyone has to notice it in review.
    """
    unguarded: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in PUBLIC_ROUTES:
            continue
        label = f"{sorted(getattr(route, 'methods', ['?']))[0]} {path}"
        if path in SELF_SERVICE_ROUTES:
            # Still must authenticate, even without a permission requirement.
            if not _authenticated(route):
                unguarded.append(label)
            continue
        if not _permission_guarded(route):
            unguarded.append(label)

    assert unguarded == [], f"routes with no permission check: {unguarded}"


def test_the_api_surface_is_not_accidentally_empty(app: FastAPI) -> None:
    """Guards the guard: a bug that made the loop above find no routes would
    otherwise make the inventory test pass vacuously."""
    api_routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/")]
    assert len(api_routes) > 40


# --------------------------------------------------------------------------
# Case tests, over HTTP
# --------------------------------------------------------------------------

#: (method, path, body) for one representative endpoint per permission.
VIEWER_READS = [
    ("GET", "/api/analytics/overview", None),
    ("GET", "/api/transactions", None),
    ("GET", "/api/audit", None),
    ("GET", "/api/monitoring/models", None),
    ("GET", "/api/reviews", None),
    ("GET", "/api/events", None),
    ("GET", "/api/policy", None),
]

ANALYST_ACTIONS = [
    ("POST", "/api/reviews/1/resolve", {"resolution": "rejected"}),
    (
        "POST",
        "/api/feedback",
        {
            "transaction_id": "TXN_SCENARIO_C_CURRENT_1",
            "outcome": "confirmed_fraud",
            "reason_code": "coordinated_activity",
        },
    ),
    ("POST", "/api/risk/predict", {"transaction_id": "TXN_SCENARIO_C_CURRENT_1"}),
    ("POST", "/api/investigations", {"transaction_id": "TXN_SCENARIO_C_CURRENT_1"}),
]

ADMIN_ACTIONS = [
    ("POST", "/api/simulator/stop", {}),
    ("POST", "/api/simulator/pause", {}),
    ("POST", "/api/simulator/reset", {}),
    ("GET", "/api/simulator/status", None),
    ("GET", "/api/metrics", None),
]


def _call(client: TestClient, method: str, path: str, body: object) -> int:
    if method == "GET":
        return client.get(path).status_code
    return client.post(path, json=body).status_code


@pytest.mark.parametrize(("method", "path", "body"), VIEWER_READS)
def test_a_viewer_can_read(viewer_client: TestClient, method: str, path: str, body: object) -> None:
    assert _call(viewer_client, method, path, body) not in (401, 403)


@pytest.mark.parametrize(("method", "path", "body"), ANALYST_ACTIONS)
def test_a_viewer_is_refused_an_analyst_action(
    viewer_client: TestClient, method: str, path: str, body: object
) -> None:
    """403, not 404 and not 500: the caller is known and simply not permitted."""
    assert _call(viewer_client, method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ACTIONS)
def test_an_analyst_is_refused_an_admin_action(
    analyst_client: TestClient, method: str, path: str, body: object
) -> None:
    assert _call(analyst_client, method, path, body) == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ACTIONS)
def test_an_admin_is_allowed(client: TestClient, method: str, path: str, body: object) -> None:
    assert _call(client, method, path, body) not in (401, 403)


@pytest.mark.parametrize(("method", "path", "body"), VIEWER_READS + ANALYST_ACTIONS + ADMIN_ACTIONS)
def test_nothing_is_reachable_unauthenticated(
    anonymous_client: TestClient, method: str, path: str, body: object
) -> None:
    """401, and specifically not 403: without a credential we do not know who
    the caller is, so "you may not" would be the wrong answer."""
    assert _call(anonymous_client, method, path, body) == 401


@pytest.mark.parametrize(("method", "path", "body"), VIEWER_READS + ANALYST_ACTIONS + ADMIN_ACTIONS)
def test_a_merchant_account_is_refused_everything(
    merchant_client: TestClient, method: str, path: str, body: object
) -> None:
    assert _call(merchant_client, method, path, body) == 403


def test_the_403_names_the_missing_permission(viewer_client: TestClient) -> None:
    """A denial has to be debuggable by the person who hit it.

    The caller already authenticated, so naming the permission they lack tells
    them nothing about the system they could not have learned from the docs -
    and saves an afternoon of guessing.
    """
    response = viewer_client.post("/api/simulator/stop")
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "simulator:control" in detail
    assert "viewer" in detail


def test_an_analyst_can_actually_resolve_a_review(analyst_client: TestClient, decided: int) -> None:
    """The mirror of the refusal tests: the permission grants real access.

    Without this, every 403 above would still pass if `require` refused
    everyone, and the RBAC suite would be asserting that the console is broken.
    """
    queue = analyst_client.get("/api/reviews", params={"status": "open", "page_size": 1}).json()
    assert queue["items"], "the decided fixture should leave open review cases"

    case_id = queue["items"][0]["review_case_id"]
    response = analyst_client.post(
        f"/api/reviews/{case_id}/resolve",
        json={"resolution": "rejected", "reason": "Shared device confirmed."},
    )
    assert response.status_code == 200
    assert response.json()["resolution"] == "rejected"
