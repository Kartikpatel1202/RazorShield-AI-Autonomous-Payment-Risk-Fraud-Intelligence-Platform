"""Centralised HTTP error handling."""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ml.anomaly.predictor import AnomalyContractError, AnomalyModelNotAvailableError
from ml.inference.predictor import ModelContractError, ModelNotAvailableError

logger = logging.getLogger(__name__)


#: Identifiers safe to quote back to the caller. Every real reference in this
#: system matches it; anything that does not is, by construction, not a
#: reference this API ever issued.
_SAFE_REFERENCE = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")


class EntityNotFoundError(LookupError):
    """A requested record does not exist.

    Raised by the service layer so query code never needs to know about HTTP.

    The message quotes the reference only when it is recognisably one of ours.
    Echoing arbitrary input back is how a JSON API acquires a reflected-input
    finding despite never rendering HTML: the body lands in a log viewer, an
    error-tracking dashboard or a `curl | grep` pipeline, none of which promised
    to treat it as inert. Since a reference that fails this pattern cannot match
    a record anyway, saying so precisely costs the caller nothing.
    """

    def __init__(self, entity: str, reference: str) -> None:
        self.entity = entity
        self.reference = reference
        quoted = f"'{reference}'" if _SAFE_REFERENCE.match(reference) else "the requested id"
        super().__init__(f"{entity} {quoted} was not found")


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that keep unexpected failures off the wire."""

    @app.exception_handler(EntityNotFoundError)
    async def entity_not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ModelNotAvailableError)
    async def model_unavailable_handler(
        request: Request, exc: ModelNotAvailableError
    ) -> JSONResponse:
        # The exception text can name artifacts and commands, so it is logged
        # rather than returned; the client only learns the capability is down.
        logger.error("Risk model unavailable on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "The risk model is not available. Try again later."},
        )

    @app.exception_handler(ModelContractError)
    async def model_contract_handler(request: Request, exc: ModelContractError) -> JSONResponse:
        logger.error("Risk model contract mismatch on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "The risk model is not available. Try again later."},
        )

    @app.exception_handler(AnomalyModelNotAvailableError)
    async def anomaly_unavailable_handler(
        request: Request, exc: AnomalyModelNotAvailableError
    ) -> JSONResponse:
        logger.error("Anomaly model unavailable on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "The anomaly model is not available. Try again later."},
        )

    @app.exception_handler(AnomalyContractError)
    async def anomaly_contract_handler(request: Request, exc: AnomalyContractError) -> JSONResponse:
        logger.error("Anomaly model contract mismatch on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "The anomaly model is not available. Try again later."},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
