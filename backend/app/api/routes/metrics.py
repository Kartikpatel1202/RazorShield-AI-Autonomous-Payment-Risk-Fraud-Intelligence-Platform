"""Prometheus metrics exposition.

Authenticated, and restricted to ``SYSTEM_ADMIN``. Metrics are not secrets, but
they are an excellent map: request rates per route, decision mix, login failure
counts and pipeline error rates together tell an observer a great deal about
what this system does and when it is struggling.

Prometheus can present a bearer token (``authorization`` in the scrape config),
so requiring one costs nothing operationally. A deployment that scrapes over a
private network instead can drop the dependency below - that is a network-policy
decision, and this file is the single place it would be made.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.deps import require
from app.core.metrics import CONTENT_TYPE, render_metrics
from app.core.permissions import Permission

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=PlainTextResponse,
    dependencies=[Depends(require(Permission.SYSTEM_ADMIN))],
    responses={200: {"content": {CONTENT_TYPE: {}}}},
)
def metrics() -> PlainTextResponse:
    """The process metric registry in text exposition format.

    Served from a private registry, so it carries only the families declared in
    :mod:`app.core.metrics` - notably *not* the default collectors, which would
    publish the exact Python build this server runs on.
    """
    return PlainTextResponse(content=render_metrics(), media_type=CONTENT_TYPE)
