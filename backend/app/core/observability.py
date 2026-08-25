"""The lifecycle log: one structured record per stage of a transaction's life.

A transaction is touched by four services and one policy engine on its way to a
decision. Reconstructing that path from ad-hoc ``logger.info`` calls means
grepping five modules and hoping. Instead every stage emits the same shape,
under the same logger, with the same identity fields:

    {"event": "risk_scored", "correlation_id": ..., "transaction_id": ...,
     "decision_id": ..., "investigation_id": ..., "duration_ms": ...}

so ``jq 'select(.transaction_id == "SIM_...")'`` returns the whole story in
order. The correlation id is attached by the JSON formatter from the ambient
context, not passed in - see :mod:`app.core.context`.

Field values are business identifiers only. Nothing here is given a password, a
token or a connection string; :class:`~app.core.logging.RedactingFilter` is the
backstop for the day someone forgets.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

logger = logging.getLogger("razorshield.lifecycle")


class LifecycleEvent(StrEnum):
    """The stages worth a log line. Ordered as they occur."""

    REQUEST_STARTED = "request_started"
    REQUEST_COMPLETED = "request_completed"
    TRANSACTION_RECEIVED = "transaction_received"
    TRANSACTION_DUPLICATE = "transaction_duplicate"
    RISK_SCORED = "risk_scored"
    ANOMALY_SCORED = "anomaly_scored"
    INVESTIGATION_STARTED = "investigation_started"
    INVESTIGATION_COMPLETED = "investigation_completed"
    DECISION_CREATED = "decision_created"
    FEEDBACK_CREATED = "feedback_created"
    PIPELINE_FAILED = "pipeline_failed"
    AUTH_SUCCEEDED = "auth_succeeded"
    AUTH_FAILED = "auth_failed"
    AUTHORIZATION_DENIED = "authorization_denied"
    RATE_LIMITED = "rate_limited"


def log_lifecycle(
    event: LifecycleEvent,
    *,
    transaction_id: str | None = None,
    decision_id: str | None = None,
    investigation_id: str | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one lifecycle record.

    The three identity arguments are named rather than left to ``**fields`` so
    that a typo produces a missing argument at the call site instead of a log
    line that quietly fails to join up with the rest of the trace.
    """
    payload: dict[str, Any] = {"event": str(event)}
    if transaction_id is not None:
        payload["transaction_id"] = transaction_id
    if decision_id is not None:
        payload["decision_id"] = decision_id
    if investigation_id is not None:
        payload["investigation_id"] = investigation_id
    payload.update({key: value for key, value in fields.items() if value is not None})

    logger.log(level, str(event), extra=payload)
