"""HTTP middleware: correlation, security headers, body limits, access logging.

Order matters and is set in :func:`app.main.create_app`. Starlette runs
middleware outermost-first on the way in, so the correlation id must be
installed before anything that logs, and the security headers must be added by a
layer that sees every response including the ones other middleware short-circuit.
"""

from __future__ import annotations

import logging
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.core.context import (
    CORRELATION_HEADER,
    correlation_scope,
    sanitize_correlation_id,
)
from app.core.metrics import http_request_latency, http_requests_total
from app.core.observability import LifecycleEvent, log_lifecycle

logger = logging.getLogger(__name__)

#: Paths kept out of the access log and the request-latency histogram. Probes
#: fire every few seconds; including them would bury real traffic and give the
#: latency percentiles a large, meaningless mode near zero.
_QUIET_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/health/db"})


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Bind a correlation id for the request and echo it back.

    A client-supplied ``X-Correlation-ID`` is honoured only if it matches the
    strict pattern in :mod:`app.core.context`; anything else is replaced with a
    fresh id. That check is what stops a caller from writing arbitrary text -
    including CRLF - into our log stream and our response headers.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = sanitize_correlation_id(request.headers.get(CORRELATION_HEADER))
        with correlation_scope(correlation_id):
            request.state.correlation_id = correlation_id
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the response headers a browser needs to defend itself.

    The API serves JSON, not documents, so its own CSP can be maximally strict:
    nothing loads, nothing frames it, no plugins, no form posts. The interactive
    docs are the exception - Swagger UI pulls its bundle from a CDN - and they
    are only served outside production, so they get a narrower policy rather
    than forcing the whole API to loosen.
    """

    #: For JSON endpoints. `default-src 'none'` means a response that somehow
    #: rendered as HTML could still not fetch or execute anything.
    API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

    #: For /docs and /redoc, which legitimately load a script and stylesheet
    #: from jsdelivr and inline a small bootstrap.
    DOCS_CSP = (
        "default-src 'none'; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "img-src 'self' https://fastapi.tiangolo.com data:; "
        "connect-src 'self'; font-src 'self' https://cdn.jsdelivr.net; "
        "frame-ancestors 'none'; base-uri 'none'"
    )

    _DOC_PATHS = ("/docs", "/redoc")

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path

        is_docs = path.startswith(self._DOC_PATHS)
        response.headers["Content-Security-Policy"] = self.DOCS_CSP if is_docs else self.API_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # This API needs no camera, microphone, geolocation or payment handler.
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        # Responses are per-user once authentication is in play; a shared cache
        # holding one analyst's queue and serving it to another is a real bug.
        response.headers.setdefault("Cache-Control", "no-store")
        # Uvicorn advertises its version by default. It is not a vulnerability,
        # it is a free hint about which CVEs to try.
        response.headers["Server"] = "razorshield"

        if self.settings.hsts_enabled and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.settings.hsts_max_age_seconds}; includeSubDomains"
            )

        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse oversized request bodies with 413.

    Checked against ``Content-Length`` before the body is read, so a declared
    100 MB upload costs nothing. A chunked request without a length is not
    rejected here - Uvicorn's own ``--limit-max-requests`` and the reverse proxy
    are the controls for that - which `docs/security.md` records.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            if length > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body exceeds {self.max_bytes} bytes"},
                )
        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured line per request, plus the HTTP metrics.

    The route *template* is used as the metric label, not the raw path: labelling
    by path would mint a new time series for every transaction id ever fetched,
    which is the classic way to make a Prometheus server fall over.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        quiet = path in _QUIET_PATHS
        started = perf_counter()

        if not quiet:
            log_lifecycle(
                LifecycleEvent.REQUEST_STARTED,
                level=logging.DEBUG,
                method=request.method,
                path=path,
            )

        response = await call_next(request)
        elapsed = perf_counter() - started

        if quiet:
            return response

        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"

        http_requests_total.labels(
            method=request.method, route=template, status=str(response.status_code)
        ).inc()
        http_request_latency.labels(method=request.method, route=template).observe(elapsed)

        log_lifecycle(
            LifecycleEvent.REQUEST_COMPLETED,
            level=logging.WARNING if response.status_code >= 500 else logging.INFO,
            method=request.method,
            path=path,
            route=template,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        return response
