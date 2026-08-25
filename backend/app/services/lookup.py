"""Resolving path parameters to database records."""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import EntityNotFoundError
from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


def resolve_by_reference(
    session: Session,
    model: type[ModelT],
    business_key: InstrumentedAttribute[str],
    reference: str,
    entity_name: str,
) -> ModelT:
    """Look a record up by its business key, falling back to its primary key.

    Business keys (``CUSTOMER_NORMAL_001``, ``dev_scn_fraud_shared_001``,
    ``198.18.100.31``) are what demos, later agent tools and humans actually
    quote, so they are tried first. A purely numeric reference also matches the
    surrogate primary key.
    """
    record = session.scalar(select(model).where(business_key == reference))
    if record is None and reference.isdigit():
        record = session.get(model, int(reference))
    if record is None:
        raise EntityNotFoundError(entity_name, reference)
    return record
