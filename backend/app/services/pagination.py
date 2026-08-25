"""Offset pagination for read-only list endpoints."""

from __future__ import annotations

from math import ceil
from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.schemas.common import Page, PageMeta

RowT = TypeVar("RowT")


def paginate(
    session: Session, statement: Select[tuple[RowT]], page: int, page_size: int
) -> Page[RowT]:
    """Run ``statement`` for one page and report the total row count.

    A separate COUNT keeps the payload small; list endpoints must never return
    the whole table.
    """
    total = int(
        session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    )
    rows = list(session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all())
    total_pages = ceil(total / page_size) if total else 0

    return Page(
        items=rows,
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        ),
    )
