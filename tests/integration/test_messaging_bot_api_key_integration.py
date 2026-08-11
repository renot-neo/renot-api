"""Integration tests for dual-auth `X-Bot-Api-Key` on the external

messaging endpoints - `POST /messages`, `GET /messages/{id}`,
`GET /messages/{id}/status`. Real Postgres, real HTTP end-to-end (no mocked
repository) - the Telegram API is mocked via `respx` (same pattern as
`test_messaging_integration.py`), Celery dispatch is mocked via
`patch_task_delay`. Message-template CRUD is DELIBERATELY not tested here -
it stays dashboard-only and never accepts `X-Bot-Api-Key` (see the
`modules/messaging/router.py` docstring).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import Mock

import httpx
import respx
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.modules.messaging import tasks as messaging_tasks

TOKEN = "123456:bot-api-key-integration-token"


def _mock_bot_registration(telegram_bot_id: int) -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": telegram_bot_id, "username": "apikey_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )


async def _register_bot_destination_and_key(
    client_as_owner: AsyncClient, *, telegram_bot_id: int, chat_id: int
) -> tuple[str, str, str]:
    """Registers a bot + destination via the regular dashboard JWT

    (`client_as_owner`), returning `(bot_id, destination_id,
    api_key_plaintext)` - `api_key` is ONLY present in the registration
    response (shown once), exactly like `_secret_response` in `bots/router.py`.
    """
    _mock_bot_registration(telegram_bot_id)
    bot_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "API Key Bot", "token": TOKEN}
    )
    assert bot_resp.status_code == 201
    bot_data = bot_resp.json()["data"]

    dest_resp = await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_data["id"], "type": "personal", "chat_id": chat_id, "title": "R"},
    )
    assert dest_resp.status_code == 201

    return bot_data["id"], dest_resp.json()["data"]["id"], bot_data["api_key"]


def _api_key_client(api_key: str) -> AsyncClient:
    """A client with NO `Authorization` header at all - purely `X-Bot-Api-Key`,

    proving this path doesn't silently also need a JWT.
    """
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Bot-Api-Key": api_key},
    )


@respx.mock
async def test_send_and_poll_message_via_bot_api_key(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id, api_key = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910001, chat_id=7001
    )
    mock_delay = patch_task_delay(messaging_tasks.send_message_to_destination)

    async with _api_key_client(api_key) as bot_client:
        send_resp = await bot_client.post(
            "/api/v1/messages",
            json={
                "bot_id": bot_id,
                "destination_ids": [destination_id],
                "content_type": "text",
                "text": "Hello from external app!",
            },
        )
        assert send_resp.status_code == 201
        message_id = send_resp.json()["data"]["id"]
        mock_delay.assert_called_once()

        get_resp = await bot_client.get(f"/api/v1/messages/{message_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["bot_id"] == bot_id

        status_resp = await bot_client.get(f"/api/v1/messages/{message_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["data"]["counts"]["queued"] == 1


@respx.mock
async def test_send_message_via_bot_api_key_rejects_mismatched_bot_id(
    client_as_owner: AsyncClient,
) -> None:
    """`bot_id` in the body MUST match the API key's owner - a bot key must

    not be usable to send on behalf of ANOTHER bot that genuinely exists in
    the same tenant (see `messaging.service._assert_bot_access`'s
    `restrict_to_bot_id`). Deliberately uses a SECOND bot that's genuinely
    registered (not a random UUID) so what's actually being tested is a
    real mismatch (403 `BOT_NOT_ASSIGNED`), not a 404 `BOT_NOT_FOUND`
    because `data.bot_id` itself never existed.
    """
    _bot_a_id, destination_id, api_key_a = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910002, chat_id=7002
    )
    bot_b_id, _dest_b_id, _api_key_b = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910006, chat_id=7006
    )

    async with _api_key_client(api_key_a) as bot_client:
        response = await bot_client.post(
            "/api/v1/messages",
            json={
                "bot_id": bot_b_id,
                "destination_ids": [destination_id],
                "content_type": "text",
                "text": "Should be rejected",
            },
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BOT_NOT_ASSIGNED"


async def test_messages_endpoint_rejects_invalid_bot_api_key(client_as_owner: AsyncClient) -> None:
    async with _api_key_client("tgbm_live_this-key-does-not-exist") as bot_client:
        response = await bot_client.post(
            "/api/v1/messages",
            json={
                "bot_id": str(uuid.uuid4()),
                "destination_ids": [str(uuid.uuid4())],
                "content_type": "text",
                "text": "Invalid key",
            },
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "BOT_API_KEY_INVALID"


async def test_messages_endpoint_rejects_no_credentials_at_all(client: AsyncClient) -> None:
    """Without `X-Bot-Api-Key` OR `Authorization` at all -

    `TokenInvalidError` (401), same as FastAPI's built-in
    `HTTPBearer(auto_error=True)` when a regular single-auth endpoint is
    called without any header at all.
    """
    response = await client.post(
        "/api/v1/messages",
        json={
            "bot_id": str(uuid.uuid4()),
            "destination_ids": [str(uuid.uuid4())],
            "content_type": "text",
            "text": "No credentials",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


@respx.mock
async def test_get_message_via_bot_api_key_cannot_see_another_bots_message(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    """Bot A must not see Bot B's message via its own API key, even within

    the same tenant - reuses `BotNotAssignedError` (403), the same
    exception used for a member who isn't assigned (`_assert_bot_access`).
    """
    patch_task_delay(messaging_tasks.send_message_to_destination)
    bot_a_id, dest_a_id, _api_key_a = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910003, chat_id=7003
    )
    _bot_b_id, _dest_b_id, api_key_b = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910004, chat_id=7004
    )

    send_resp = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_a_id,
            "destination_ids": [dest_a_id],
            "content_type": "text",
            "text": "Bot A's message",
        },
    )
    assert send_resp.status_code == 201
    message_id = send_resp.json()["data"]["id"]

    async with _api_key_client(api_key_b) as bot_b_client:
        get_resp = await bot_b_client.get(f"/api/v1/messages/{message_id}")
        assert get_resp.status_code == 403
        assert get_resp.json()["error"]["code"] == "BOT_NOT_ASSIGNED"

        status_resp = await bot_b_client.get(f"/api/v1/messages/{message_id}/status")
        assert status_resp.status_code == 403
        assert status_resp.json()["error"]["code"] == "BOT_NOT_ASSIGNED"


@respx.mock
async def test_message_templates_still_dashboard_only_not_bot_api_key(
    client_as_owner: AsyncClient,
) -> None:
    """Template CRUD is DELIBERATELY not part of dual-auth (see the

    `modules/messaging/router.py` docstring) - even a valid API key still
    gets 401 here, because `templates_router` still uses the regular
    `require_permission` (JWT-only) and knows nothing about `X-Bot-Api-Key`.
    """
    _bot_id, _dest_id, api_key = await _register_bot_destination_and_key(
        client_as_owner, telegram_bot_id=910005, chat_id=7005
    )

    async with _api_key_client(api_key) as bot_client:
        response = await bot_client.get("/api/v1/message-templates")
    assert response.status_code == 401
