"""Aggregates every route module into a single router tree."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    analytics,
    auth,
    catalog,
    feedback,
    health,
    investigations,
    live,
    metrics,
    operations,
    reviews,
    risk,
    transactions,
)

# Operational endpoints live at the root so probes stay stable across API versions.
root_router = APIRouter()
root_router.include_router(health.router)

# Data-access, scoring, investigation and decision endpoints, mounted under
# Settings.api_prefix.
api_router = APIRouter()

# Authentication first: it is the only router with no auth dependency of its
# own, and keeping it at the top makes that obvious to anyone reading the tree.
api_router.include_router(auth.router)
api_router.include_router(metrics.router)

api_router.include_router(analytics.router)
api_router.include_router(feedback.router)
api_router.include_router(live.router)

# `operations` MUST be registered before `transactions`. FastAPI matches routes
# in registration order, and `transactions` declares `/transactions/{id}`, which
# would otherwise swallow `/transactions/explorer` and answer it with a 404 for
# a transaction literally named "explorer".
api_router.include_router(operations.router)

api_router.include_router(catalog.router)
api_router.include_router(transactions.router)
api_router.include_router(risk.router)
api_router.include_router(investigations.router)
api_router.include_router(reviews.router)
