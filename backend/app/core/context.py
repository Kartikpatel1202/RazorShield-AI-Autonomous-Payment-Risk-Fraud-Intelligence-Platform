"""Request-scoped correlation state.

A :class:`~contextvars.ContextVar` rather than a parameter threaded through every
call: the service layer already takes a ``Session`` and a set of domain
arguments, and adding a correlation id to forty signatures to satisfy the
logger would be the tail wagging the dog.

Context variables are the right tool here specifically because they follow
``await`` boundaries and are copied into ``anyio.to_thread.run_sync`` workers -
which is how the Phase 9 ingestion pipeline runs - so an id set in the request
handler is still visible in the thread that scores the transaction.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: Header a client may send to join its own trace to ours.
CORRELATION_HEADER = "X-Correlation-ID"

#: Deliberately narrow. A correlation id is echoed back in a response header and
#: written into logs, so it is an injection vector in both directions if it is
#: allowed to carry CR, LF, or anything a log parser might treat as structure.
#: Anything failing this pattern is replaced, never sanitised in place.
_VALID_CORRELATION_ID = re.compile(r"\A[A-Za-z0-9_.:-]{8,64}\Z")

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(candidate: str | None) -> str:
    """Return ``candidate`` if it is a safe correlation id, else a fresh one.

    Rejecting rather than stripping is the whole point: a caller who sends
    ``abc\\r\\nX-Admin: true`` gets a brand new id, not a cleaned-up version of
    the one they chose.
    """
    if candidate and _VALID_CORRELATION_ID.match(candidate):
        return candidate
    return new_correlation_id()


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


@contextmanager
def correlation_scope(value: str) -> Iterator[str]:
    """Bind ``value`` for the duration of the block, then restore what was there.

    Used by the simulator workers, which are long-lived and process one
    transaction after another: each needs its own id, and none may leak into the
    next.
    """
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)
