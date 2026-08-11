"""Generic offset/limit pagination helper shared across modules.

- `PageParams`/`Page`: plain dataclasses, used at the repository/service
  layer (no dependency on FastAPI/Pydantic, so they stay easy to unit-test).
- `pagination_params`: a router `Depends()` - the one way list endpoints
  accept the `page`/`page_size` query params. This lives here rather than in
  `core/deps.py` because `core/deps.py` is reserved for actual resource
  access (DB session, auth, Redis); this dependency purely parses query
  params, so it stays in the same file as the `PageParams`/`Page` types it
  builds - consistent with `core/response.py`, which also defines its own
  `Depends`-independent helpers outside `core/deps.py`.
- `PaginationMeta`/`PaginatedResponse`: the shape of `data` for every list
  endpoint (the success envelope from `core/response.py` still wraps it -
  routers set `response_model=Envelope[PaginatedResponse[XResponse]]`, same
  pattern as the plain `Envelope[XResponse]` case).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from pydantic import BaseModel

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageParams:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.page < 1:
            object.__setattr__(self, "page", 1)
        if self.page_size < 1 or self.page_size > MAX_PAGE_SIZE:
            object.__setattr__(self, "page_size", min(max(self.page_size, 1), MAX_PAGE_SIZE))

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        if self.page_size == 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


def pagination_params(
    page: int = Query(1, ge=1, description="Page number, starting at 1."),
    page_size: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Items per page (maximum {MAX_PAGE_SIZE}).",
    ),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedResponse[T](BaseModel):
    items: list[T]
    pagination: PaginationMeta

    @classmethod
    def from_page(cls, page: Page[Any], item_factory: Callable[[Any], T]) -> PaginatedResponse[T]:
        return cls(
            items=[item_factory(item) for item in page.items],
            pagination=PaginationMeta(
                page=page.page,
                page_size=page.page_size,
                total=page.total,
                total_pages=page.total_pages,
            ),
        )
