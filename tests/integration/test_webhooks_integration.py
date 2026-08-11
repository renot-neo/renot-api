"""Integration tests for the `app.modules.webhooks` router.

Real Postgres, no mocked repository. This endpoint is public (secret-token
auth, not JWT) - uses the plain `client` fixture, never `client_as_*`.
Telegram API calls (`getMe`/`setWebhook` during bot creation, `sendMessage`
command replies) mocked via `respx`; billing's Celery dispatch
(`record_usage_event.delay(...)`) mocked via `patch_task_delay`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import Mock

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.modules.billing import tasks as billing_tasks
from app.modules.organizations.model import Organization

TOKEN = "123456:webhooks-integration-token"


async def _create_bot(client_as_owner: AsyncClient, db_session: AsyncSession) -> tuple[str, str]:
    """Returns `(bot_id, webhook_secret)` - `webhook_secret` isn't exposed via

    `BotResponse` (never leaves the platform to the dashboard), so it's read
    straight from the DB (encrypted at rest since 2026-08-12, decrypted here
    the same way `bots.service.reveal_webhook_secret` does - see
    `private/specs/2026-08-12-bot-secret-encryption-design.md`), same as
    Telegram itself would receive it once (at `setWebhook` time) and echo
    back on every subsequent inbound call.
    """
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 9001, "username": "webhook_test_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    bot_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Webhook Bot", "token": TOKEN}
    )
    bot_id = bot_resp.json()["data"]["id"]

    row = await db_session.execute(
        text("SELECT webhook_secret_encrypted FROM bots WHERE id = :id"), {"id": bot_id}
    )
    return bot_id, decrypt_secret(row.scalar_one())


def _mock_send_message() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )


def _update(
    update_id: int = 1, *, text_body: str, chat_id: int = 111, chat_type: str = "private"
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": chat_type},
            "text": text_body,
        },
    }


@respx.mock
async def test_start_command_creates_active_subscription(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, secret = await _create_bot(client_as_owner, db_session)
    # Registration is closed by default (`webhook_enabled=False`) - open it.
    await client_as_owner.patch(
        f"/api/v1/bots/{bot_id}/subscription-policy", json={"webhook_enabled": True}
    )
    _mock_send_message()
    mock_delay = patch_task_delay(billing_tasks.record_usage_event)

    response = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json=_update(text_body="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    mock_delay.assert_called_once()
    # `UsageEventType.IN.value` is `"in"`, not `"event_in"` - the DB enum
    # member name is `IN`/`OUT`, the wire value is the short form.
    assert mock_delay.call_args.kwargs["event_type"] == "in"

    subs_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/destinations")
    assert len(subs_resp.json()["data"]["items"]) == 1
    assert subs_resp.json()["data"]["items"][0]["subscription_status"] == "active"


@respx.mock
async def test_start_command_rejected_when_registration_closed(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, secret = await _create_bot(client_as_owner, db_session)
    _mock_send_message()
    patch_task_delay(billing_tasks.record_usage_event)

    response = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json=_update(text_body="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 200  # Telegram still gets 200, no side effect

    subs_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/destinations")
    assert subs_resp.json()["data"]["items"] == []


@respx.mock
async def test_stop_then_start_again_toggles_subscription(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, secret = await _create_bot(client_as_owner, db_session)
    await client_as_owner.patch(
        f"/api/v1/bots/{bot_id}/subscription-policy", json={"webhook_enabled": True}
    )
    _mock_send_message()
    patch_task_delay(billing_tasks.record_usage_event)
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret}

    await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}", json=_update(1, text_body="/start"), headers=headers
    )
    stop_resp = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}", json=_update(2, text_body="/stop"), headers=headers
    )
    assert stop_resp.status_code == 200

    subs_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/destinations")
    assert subs_resp.json()["data"]["items"][0]["subscription_status"] == "unsubscribed"

    restart_resp = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}", json=_update(3, text_body="/start"), headers=headers
    )
    assert restart_resp.status_code == 200
    subs_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/destinations")
    assert subs_resp.json()["data"]["items"][0]["subscription_status"] == "active"


@respx.mock
async def test_wrong_secret_token_is_rejected_and_records_nothing(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, _secret = await _create_bot(client_as_owner, db_session)
    mock_delay = patch_task_delay(billing_tasks.record_usage_event)

    response = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json=_update(text_body="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WEBHOOK_SECRET_INVALID"
    mock_delay.assert_not_called()


async def test_unknown_bot_id_returns_404(
    client: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    patch_task_delay(billing_tasks.record_usage_event)
    response = await client.post(
        f"/api/v1/webhooks/telegram/{uuid.uuid4()}",
        json=_update(text_body="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOT_NOT_FOUND"


@respx.mock
async def test_webhook_rejected_after_organization_deleted(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    """Covers the bypass path that's the reason `modules.bots.

    cascade_delete_for_organization` is REQUIRED (not okay-to-orphan): this
    endpoint does NOT go through JWT/`require_permission` at all (its auth
    is a per-bot secret token, tenant-agnostic - see the module docstring
    above), so if a `Bot` were left active when its org is deleted,
    Telegram could keep triggering `handle_telegram_update` forever. Tested
    end-to-end via a real HTTP request (unlike the scheduled dispatcher in
    `test_messaging_integration.py`, which has to stop at the service level
    - this webhook never touches Celery/`WorkerAsyncSessionFactory` at all,
    so it's safe to test fully through `client`/`client_as_owner`).
    """
    bot_id, secret = await _create_bot(client_as_owner, db_session)
    patch_task_delay(billing_tasks.record_usage_event)

    delete_resp = await client_as_owner.delete(f"/api/v1/organizations/{test_organization.id}")
    assert delete_resp.status_code == 204

    response = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json=_update(text_body="/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOT_NOT_FOUND"


@respx.mock
async def test_non_command_text_is_ignored_but_still_counted_as_event_in(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, secret = await _create_bot(client_as_owner, db_session)
    mock_delay = patch_task_delay(billing_tasks.record_usage_event)

    response = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json=_update(text_body="just chatting, not a command"),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 200
    mock_delay.assert_called_once()  # Flow 6.4: every update counts, not just commands
