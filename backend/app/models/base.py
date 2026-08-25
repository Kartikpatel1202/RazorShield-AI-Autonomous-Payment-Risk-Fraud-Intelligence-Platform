"""Shared column types and mixins for RazorShield ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# SQLite has no autoincrementing BIGINT, so surrogate keys degrade to INTEGER
# there. Tests run on SQLite; PostgreSQL keeps the wider type.
BigIntPk = BigInteger().with_variant(Integer, "sqlite")

# JSONB on PostgreSQL (indexable, typed); plain JSON on SQLite for tests.
JsonDocument = sa.JSON().with_variant(JSONB, "postgresql")


class UtcDateTime(sa.types.TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC in Python.

    PostgreSQL round-trips ``TIMESTAMP WITH TIME ZONE`` as an aware datetime,
    but SQLite (used by the tests) hands back a naive one. Normalising here keeps
    datetime arithmetic identical on both backends instead of leaving a trap for
    every caller.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to a UtcDateTime column")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_now() -> datetime:
    """Timezone-aware current time, used as the Python-side column default."""
    return datetime.now(UTC)


class PkMixin:
    """Surrogate integer primary key."""

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)


class CreatedAtMixin:
    """Row creation timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )


class TimestampMixin(CreatedAtMixin):
    """Row creation and last-modification timestamps."""

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )


EnumT = TypeVar("EnumT", bound=StrEnum)


def enum_column(enum_cls: type[EnumT], length: int = 32) -> sa.Enum:
    """A VARCHAR column that persists the enum *values*, not the names.

    No CHECK constraint is emitted (SQLAlchemy's default); ``validate_strings``
    rejects unknown values in Python at bind time instead.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
