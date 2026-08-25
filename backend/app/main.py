"""RazorShield AI backend application entrypoint.

Run locally with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router, root_router
from app.core.config import Settings, get_settings
from app.core.context import CORRELATION_HEADER
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    AccessLogMiddleware,
    BodySizeLimitMiddleware,
    CorrelationIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import limiter

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Autonomous payment risk and fraud management platform. "
    "Hackathon simulation - not real Razorpay production infrastructure."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and, more importantly, orderly shutdown.

    On the way down the simulator is stopped before the engine is disposed. The
    order is not cosmetic: simulator workers hold sessions, and disposing the
    pool underneath a running worker turns a clean shutdown into a burst of
    connection errors in the logs of an otherwise healthy deployment.
    """
    yield

    from app.db.session import engine
    from app.simulator.engine import engine as simulator

    try:
        await simulator.stop()
    except Exception:  # noqa: BLE001 - shutdown must not raise past this point
        logger.exception("Simulator did not stop cleanly")

    engine.dispose()
    logger.info("Database connection pool disposed")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, service=settings.service_name)
    limiter.enabled = settings.rate_limit_enabled

    app = FastAPI(
        title="RazorShield AI",
        description=DESCRIPTION,
        version="0.1.0",
        # The schema is a map of the attack surface. Published everywhere it is
        # useful; withheld from the deployment where it is only useful to
        # someone else.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # Middleware is applied bottom-up: the last one added is the outermost, and
    # therefore the first to see a request and the last to touch a response.
    # The correlation id must be installed before anything that logs, so it is
    # added last.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Browsers hide non-safelisted response headers from JavaScript unless
        # they are named here; without this the console cannot read back the
        # correlation id it needs to quote in a bug report.
        expose_headers=[CORRELATION_HEADER],
    )
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(root_router)
    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
