"""Shared API schema building blocks."""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class ORMModel(BaseModel):
    """Base for schemas read directly from SQLAlchemy instances."""

    model_config = ConfigDict(from_attributes=True)


class PageMeta(BaseModel):
    """Where the caller is within a paginated collection."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    has_next: bool
    has_previous: bool


class Page(BaseModel, Generic[T]):
    """One page of results plus its navigation metadata."""

    items: list[T]
    meta: PageMeta


#: The highest page number that can be requested. Not a product limit - at the
#: maximum page size it is far past the end of any table here - but an upper
#: bound: without one, `?page=99999999999999999999` reaches the database as an
#: OFFSET the driver cannot represent, and the request 500s with an
#: OverflowError instead of failing validation like every other bad input.
MAX_PAGE_NUMBER = 1_000_000

PageNumber = Annotated[
    int,
    Query(ge=1, le=MAX_PAGE_NUMBER, description="1-based page number"),
]
PageSize = Annotated[
    int,
    Query(ge=1, le=MAX_PAGE_SIZE, description=f"Results per page (max {MAX_PAGE_SIZE})"),
]
