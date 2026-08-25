"""SQLAlchemy declarative base.

Business tables are intentionally NOT defined in Phase 1. Models added in later
phases must subclass :class:`Base` and be imported in ``app.models`` so that
Alembic autogeneration can discover them.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names keep Alembic migrations stable and reviewable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every RazorShield ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
