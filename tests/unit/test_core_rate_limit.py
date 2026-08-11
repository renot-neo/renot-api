"""Unit tests for `app.core.middleware.rate_limit`.

Redis is mocked (`_FakeRedis`, same pattern as `test_messaging_tasks.py`'s
`_FakeRedis` for Telegram throttling) - no real Redis/DB needed, purely
testing the window counter logic, group/key classification, and the
middleware's 429 behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.core.middleware import rate_limit
from app.core.middleware.rate_limit import RateLimitMiddleware, classify_request, try_consume_window
from app.core.security import create_access_token


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


def _make_request(
    *,
    method: str = "GET",
    path: str = "/api/v1/x",
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("203.0.113.1", 12345),
) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": b"",
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


# --- try_consume_window (fixed-window Redis counter) ---


@pytest.mark.asyncio
async def test_try_consume_window_allows_up_to_limit() -> None:
    redis_client = _FakeRedis()
    for _ in range(3):
        allowed, _ = await try_consume_window(redis_client, "k", 3, 60)
        assert allowed is True


@pytest.mark.asyncio
async def test_try_consume_window_rejects_beyond_limit() -> None:
    redis_client = _FakeRedis()
    for _ in range(3):
        await try_consume_window(redis_client, "k", 3, 60)

    allowed, retry_after = await try_consume_window(redis_client, "k", 3, 60)
    assert allowed is False
    assert 0 <= retry_after <= 60


@pytest.mark.asyncio
async def test_try_consume_window_different_keys_independent() -> None:
    redis_client = _FakeRedis()
    for _ in range(3):
        await try_consume_window(redis_client, "a", 3, 60)

    # Other keys aren't affected even though `a` has hit its limit.
    allowed, _ = await try_consume_window(redis_client, "b", 3, 60)
    assert allowed is True


# --- classify_request (grouping + key) ---


def test_classify_auth_path_keys_by_ip() -> None:
    request = _make_request(method="POST", path="/api/v1/auth/login", client=("198.51.100.9", 1))
    group, limit, key = classify_request(request)
    assert group == "auth"
    assert limit == rate_limit._AUTH_LIMIT
    assert key == "ratelimit:auth:198.51.100.9"


def test_classify_messaging_send_keys_by_tenant_when_jwt_valid() -> None:
    token = create_access_token(subject="user-1", tenant_id="tenant-abc")
    request = _make_request(
        method="POST",
        path="/api/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    group, limit, key = classify_request(request)
    assert group == "messaging_send"
    assert limit == rate_limit._MESSAGING_SEND_LIMIT
    assert key == "ratelimit:messaging_send:tenant-abc"


def test_classify_messaging_send_falls_back_to_ip_without_jwt() -> None:
    request = _make_request(method="POST", path="/api/v1/messages", client=("192.0.2.5", 1))
    group, _, key = classify_request(request)
    assert group == "messaging_send"
    assert key == "ratelimit:messaging_send:192.0.2.5"


def test_classify_messaging_send_falls_back_to_ip_with_invalid_jwt() -> None:
    request = _make_request(
        method="POST",
        path="/api/v1/messages",
        headers={"Authorization": "Bearer not-a-real-token"},
        client=("192.0.2.6", 1),
    )
    group, _, key = classify_request(request)
    assert group == "messaging_send"
    assert key == "ratelimit:messaging_send:192.0.2.6"


def test_classify_messages_get_is_general_not_messaging_send() -> None:
    # GET /messages/{id} isn't "sending a message" - only POST /messages
    # falls into the messaging_send group.
    request = _make_request(method="GET", path="/api/v1/messages/some-id")
    group, limit, _ = classify_request(request)
    assert group == "general"
    assert limit == rate_limit._GENERAL_LIMIT


def test_classify_general_keys_by_user_id_when_jwt_valid() -> None:
    token = create_access_token(subject="user-42", tenant_id=None)
    request = _make_request(
        method="GET",
        path="/api/v1/bots",
        headers={"Authorization": f"Bearer {token}"},
    )
    group, limit, key = classify_request(request)
    assert group == "general"
    assert limit == rate_limit._GENERAL_LIMIT
    assert key == "ratelimit:general:user-42"


def test_classify_general_falls_back_to_ip_without_jwt() -> None:
    request = _make_request(method="GET", path="/api/v1/bots", client=("192.0.2.7", 1))
    group, _, key = classify_request(request)
    assert group == "general"
    assert key == "ratelimit:general:192.0.2.7"


def test_classify_unknown_client_falls_back_to_unknown() -> None:
    request = _make_request(method="GET", path="/api/v1/bots", client=None)
    _, _, key = classify_request(request)
    assert key == "ratelimit:general:unknown"


# --- RateLimitMiddleware end-to-end (429 + Retry-After + envelope) ---


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/v1/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_middleware_allows_requests_within_limit() -> None:
    app = _build_app()
    with (
        patch("app.core.middleware.rate_limit.get_redis", AsyncMock(return_value=_FakeRedis())),
        patch.object(rate_limit, "_GENERAL_LIMIT", 2),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_middleware_returns_429_with_retry_after_beyond_limit() -> None:
    app = _build_app()
    fake_redis = _FakeRedis()
    with (
        patch("app.core.middleware.rate_limit.get_redis", AsyncMock(return_value=fake_redis)),
        patch.object(rate_limit, "_GENERAL_LIMIT", 2),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/v1/ping")
            second = await client.get("/api/v1/ping")
            third = await client.get("/api/v1/ping")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "Retry-After" in third.headers
    body = third.json()
    assert body["success"] is False
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_middleware_uses_localized_message_from_accept_language() -> None:
    app = _build_app()
    with (
        patch("app.core.middleware.rate_limit.get_redis", AsyncMock(return_value=_FakeRedis())),
        patch.object(rate_limit, "_GENERAL_LIMIT", 0),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/ping", headers={"Accept-Language": "id-ID,id;q=0.9"}
            )

    assert response.status_code == 429
    assert (
        response.json()["error"]["message"] == "Terlalu banyak permintaan. Silakan coba lagi nanti."
    )
