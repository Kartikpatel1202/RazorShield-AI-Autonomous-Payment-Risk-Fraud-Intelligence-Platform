"""Enforced append-only tables.

A comment saying "do not update this row" is a convention. This is a control:
a ``before_flush`` listener on every session rejects any modification or
deletion of a registered model, so an accidental ``row.action = ...`` fails
loudly at flush time rather than quietly rewriting history.

It runs in-process, which is the right level for this codebase - the
application is the only writer - and it works identically on SQLite and
PostgreSQL, so the tests exercise the same guard that production runs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.decision import RiskDecision

#: Models whose rows are written once and never changed.
APPEND_ONLY_MODELS: tuple[type[Any], ...] = (RiskDecision,)


class ImmutableRecordError(RuntimeError):
    """An append-only row was modified or deleted."""


def _guard(session: Session, _flush_context: Any, _instances: Any) -> None:
    for instance in session.dirty:
        if isinstance(instance, APPEND_ONLY_MODELS) and session.is_modified(instance):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are append-only and cannot be modified; "
                f"record a new decision instead of amending the existing one"
            )
    for instance in session.deleted:
        if isinstance(instance, APPEND_ONLY_MODELS):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are append-only and cannot be deleted"
            )


def install_immutability_guard() -> None:
    """Register the guard for every session in this process.

    Idempotent, so importing it from several entry points is safe.
    """
    if not event.contains(Session, "before_flush", _guard):
        event.listen(Session, "before_flush", _guard)


install_immutability_guard()
