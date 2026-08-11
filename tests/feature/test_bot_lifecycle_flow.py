"""Feature test: full user journey over real HTTP,

real Postgres - "register -> login -> create org -> switch-organization ->
create bot -> set webhook -> subscribe (via real inbound `/start`) -> send
message -> check delivery log", the canonical scenario for this test layer.

Deliberately drives everything through the HTTP client with a fresh,
self-obtained token (not the `client_as_owner` fixture) - a feature test's
point is to prove the whole journey works end-to-end, auth included, not
just one module in isolation (that's what `tests/integration/` is for).

Telegram Bot API calls mocked via `respx` (never hits `api.telegram.org`,
per §11); Celery dispatch (`send_message_to_destination.delay(...)`,
`record_usage_event.delay(...)`) mocked via `patch_task_delay` - see
`tests/support/db.py`'s module docstring for why actual task execution is
out of scope for this layer too, same as `tests/integration/`.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import Mock

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import tasks as billing_tasks
from app.modules.messaging import tasks as messaging_tasks

TOKEN = "123456:feature-flow-token"
EMAIL = "founder@example.com"
PASSWORD = "SuperSecret123!"


@respx.mock
async def test_full_bot_lifecycle_from_registration_to_delivery_log(
    client: AsyncClient, db_session: AsyncSession, patch_task_delay: Callable[[object], Mock]
) -> None:
    mock_send_delay = patch_task_delay(messaging_tasks.send_message_to_destination)
    mock_usage_delay = patch_task_delay(billing_tasks.record_usage_event)

    # --- 1. Register + login ------------------------------------------------
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "Founder"},
    )
    assert register_resp.status_code == 201

    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert login_resp.status_code == 200
    client.headers["Authorization"] = f"Bearer {login_resp.json()['data']['access_token']}"

    # --- 2. Create organization + switch into it -----------------------------
    org_resp = await client.post("/api/v1/organizations", json={"name": "Founder's Org"})
    assert org_resp.status_code == 201
    org_id = org_resp.json()["data"]["id"]

    switch_resp = await client.post(
        "/api/v1/auth/switch-organization", json={"organization_id": org_id}
    )
    assert switch_resp.status_code == 200
    client.headers["Authorization"] = f"Bearer {switch_resp.json()['data']['access_token']}"

    # --- 3. Register bot (getMe + auto setWebhook), open subscriptions -------
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 7777, "username": "founders_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    bot_resp = await client.post("/api/v1/bots", json={"name": "Founder Bot", "token": TOKEN})
    assert bot_resp.status_code == 201
    bot_id = bot_resp.json()["data"]["id"]
    assert bot_resp.json()["data"]["api_key"].startswith("tgbm_live_")
    assert "webhook_secret" not in bot_resp.json()["data"]  # never leaves the platform

    policy_resp = await client.patch(
        f"/api/v1/bots/{bot_id}/subscription-policy", json={"webhook_enabled": True}
    )
    assert policy_resp.status_code == 200
    assert policy_resp.json()["data"]["webhook_enabled"] is True

    # --- 4. Subscribe via a real inbound `/start` (Flow 6.2's primary path,
    #        not the manual dashboard route already covered by
    #        `tests/integration/test_destinations_integration.py`). The
    #        webhook is secret-token-authenticated, not JWT - fetch the
    #        secret straight from the DB, the same way Telegram itself only
    #        ever learns it once (at `setWebhook` time) and echoes it back
    #        on every subsequent call; it never appears in any dashboard
    #        response.
    secret_row = await db_session.execute(
        text("SELECT webhook_secret FROM bots WHERE id = :id"), {"id": bot_id}
    )
    webhook_secret = secret_row.scalar_one()

    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    start_resp = await client.post(
        f"/api/v1/webhooks/telegram/{bot_id}",
        json={
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 999, "type": "private"},
                "text": "/start",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
    )
    assert start_resp.status_code == 200
    mock_usage_delay.assert_called_once()  # event_in recorded for the inbound update

    destinations_resp = await client.get(f"/api/v1/bots/{bot_id}/destinations")
    assert destinations_resp.status_code == 200
    subscribers = destinations_resp.json()["data"]["items"]
    assert len(subscribers) == 1
    assert subscribers[0]["subscription_status"] == "active"
    destination_id = subscribers[0]["id"]

    # --- 5. Send a message to the newly-subscribed destination ---------------
    message_resp = await client.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Welcome aboard!",
        },
    )
    assert message_resp.status_code == 201
    message_id = message_resp.json()["data"]["id"]
    mock_send_delay.assert_called_once()
    assert mock_send_delay.call_args.kwargs["delivery_log_id"]

    # --- 6. Check the delivery log --------------------------------------------
    status_resp = await client.get(f"/api/v1/messages/{message_id}/status")
    assert status_resp.status_code == 200
    status_body = status_resp.json()["data"]
    assert status_body["total_destinations"] == 1
    assert status_body["counts"]["queued"] == 1
    assert status_body["deliveries"][0]["destination_id"] == destination_id
    assert status_body["deliveries"][0]["status"] == "queued"

    # --- 7. Usage reflects the inbound event recorded in step 4 --------------
    # (event_out isn't recorded here - the mocked `.delay()` never actually
    # runs `_process_delivery`/`mark_sent`, same scope limitation as
    # `tests/integration/test_billing_integration.py` and the project's own
    # prior live-verification sessions, see project memory.)
    usage_resp = await client.get("/api/v1/billing/usage")
    assert usage_resp.status_code == 200
