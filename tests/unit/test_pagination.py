"""Unit tests for `app.core.pagination`.

Pure logic, no DB/network - `PageParams`/`Page` are plain dataclasses;
`pagination_params`/`PaginatedResponse` only touch FastAPI's `Query`
default/Pydantic validation, still no DB/network involved.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PageParams,
    PaginatedResponse,
    pagination_params,
)


def test_page_params_defaults() -> None:
    params = PageParams()
    assert params.page == 1
    assert params.page_size == DEFAULT_PAGE_SIZE
    assert params.offset == 0
    assert params.limit == DEFAULT_PAGE_SIZE


def test_page_params_computes_offset_from_page_and_size() -> None:
    params = PageParams(page=3, page_size=10)
    assert params.offset == 20
    assert params.limit == 10


def test_page_params_clamps_page_below_one() -> None:
    params = PageParams(page=0)
    assert params.page == 1
    params = PageParams(page=-5)
    assert params.page == 1


def test_page_params_clamps_page_size_to_valid_range() -> None:
    assert PageParams(page_size=0).page_size == 1
    assert PageParams(page_size=-10).page_size == 1
    assert PageParams(page_size=MAX_PAGE_SIZE + 50).page_size == MAX_PAGE_SIZE
    assert PageParams(page_size=MAX_PAGE_SIZE).page_size == MAX_PAGE_SIZE


def test_page_total_pages_rounds_up() -> None:
    page = Page(items=[1, 2, 3], total=25, page=1, page_size=10)
    assert page.total_pages == 3


def test_page_total_pages_exact_division() -> None:
    page = Page(items=[], total=20, page=1, page_size=10)
    assert page.total_pages == 2


def test_page_total_pages_zero_total() -> None:
    page = Page(items=[], total=0, page=1, page_size=10)
    assert page.total_pages == 0


def test_page_total_pages_zero_page_size_does_not_divide_by_zero() -> None:
    page = Page(items=[], total=10, page=1, page_size=0)
    assert page.total_pages == 0


def test_pagination_params_builds_page_params_from_query_values() -> None:
    # `page`/`page_size` are called explicitly here (not via FastAPI's DI
    # cycle) - the `Query(...)` default is only used for OpenAPI
    # metadata/real request validation, not relevant to test here (already
    # covered by integration/feature tests that hit a real list endpoint).
    result = pagination_params(page=3, page_size=15)
    assert result == PageParams(page=3, page_size=15)


class _ItemResponse(BaseModel):
    value: int


def test_paginated_response_from_page_maps_items_and_pagination_meta() -> None:
    page = Page(items=[1, 2, 3], total=25, page=2, page_size=3)

    result = PaginatedResponse[_ItemResponse].from_page(page, lambda v: _ItemResponse(value=v))

    assert [item.value for item in result.items] == [1, 2, 3]
    assert result.pagination.page == 2
    assert result.pagination.page_size == 3
    assert result.pagination.total == 25
    assert result.pagination.total_pages == 9


def test_paginated_response_from_page_handles_empty_page() -> None:
    page: Page[int] = Page(items=[], total=0, page=1, page_size=DEFAULT_PAGE_SIZE)

    result = PaginatedResponse[_ItemResponse].from_page(page, lambda v: _ItemResponse(value=v))

    assert result.items == []
    assert result.pagination.total_pages == 0
