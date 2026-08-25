"""What each role is allowed to do.

Routes ask for a :class:`Permission`, never for a role. That indirection is the
point: the day a fourth role appears, it is added to :data:`ROLE_PERMISSIONS`
and every route keeps working. Scattering ``role == ADMIN`` checks through the
route modules is how authorisation drifts.

The grants below follow the Phase 10 brief:

* **ADMIN** - all read access, simulator control, system administration.
* **ANALYST** (``UserRole.RISK_ANALYST``) - transactions, investigations,
  reviews, feedback.
* **VIEWER** - dashboard, transactions, monitoring, audit.

ADMIN is a strict superset. That is a deliberate choice for a platform with one
operations team: an administrator who can restart the simulator and read every
record is not meaningfully restrained by being unable to resolve a review. A
deployment that wants separation of duties removes ``REVIEWS_RESOLVE`` and
``FEEDBACK_WRITE`` from the admin grant below - nothing else has to change.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from app.models.enums import UserRole


class Permission(StrEnum):
    """A single capability a request may require."""

    # --- read -------------------------------------------------------------
    DASHBOARD_READ = "dashboard:read"
    TRANSACTIONS_READ = "transactions:read"
    MONITORING_READ = "monitoring:read"
    AUDIT_READ = "audit:read"
    INVESTIGATIONS_READ = "investigations:read"
    REVIEWS_READ = "reviews:read"
    EVENTS_READ = "events:read"

    # --- write ------------------------------------------------------------
    INVESTIGATIONS_RUN = "investigations:run"
    RISK_SCORE = "risk:score"
    REVIEWS_RESOLVE = "reviews:resolve"
    FEEDBACK_WRITE = "feedback:write"
    TRANSACTIONS_INGEST = "transactions:ingest"

    # --- administration ---------------------------------------------------
    SIMULATOR_CONTROL = "simulator:control"
    SYSTEM_ADMIN = "system:admin"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.DASHBOARD_READ,
        Permission.TRANSACTIONS_READ,
        Permission.MONITORING_READ,
        Permission.AUDIT_READ,
        Permission.INVESTIGATIONS_READ,
        Permission.REVIEWS_READ,
        Permission.EVENTS_READ,
    }
)

#: An analyst is a viewer who can also act: run an investigation, resolve a
#: review, record an outcome, submit a transaction to the pipeline.
_ANALYST: frozenset[Permission] = _VIEWER | {
    Permission.INVESTIGATIONS_RUN,
    Permission.RISK_SCORE,
    Permission.REVIEWS_RESOLVE,
    Permission.FEEDBACK_WRITE,
    Permission.TRANSACTIONS_INGEST,
}

_ADMIN: frozenset[Permission] = _ANALYST | {
    Permission.SIMULATOR_CONTROL,
    Permission.SYSTEM_ADMIN,
}

ROLE_PERMISSIONS: Mapping[UserRole, frozenset[Permission]] = MappingProxyType(
    {
        # A merchant is a party *described by* the risk platform, not an
        # operator of it. Such an account can authenticate - it is a real user -
        # and is then refused by every console endpoint.
        UserRole.MERCHANT: frozenset(),
        UserRole.VIEWER: _VIEWER,
        UserRole.RISK_ANALYST: _ANALYST,
        UserRole.ADMIN: _ADMIN,
    }
)


def permissions_for(role: UserRole) -> frozenset[Permission]:
    """Every permission held by ``role``.

    An unmapped role yields nothing rather than raising: a role added to the
    enum but forgotten here must fail closed, not open. ``test_security_rbac``
    asserts no such gap currently exists.
    """
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in permissions_for(role)
