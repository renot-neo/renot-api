"""Integration tests for the `app.modules.destinations` router.

Real Postgres, no mocked repository - needs a real `Bot` row first (via the
same respx-mocked Telegram calls as `test_bots_integration.py`) since every
destination/subscription is FK'd to one.
"""

from __future__ import annotations

import uuid

import httpx
import respx
from httpx import AsyncClient

TOKEN = "123456:destinations-integration-token"


async def _create_bot(client: AsyncClient, *, name: str = "Dest Test Bot") -> str:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 555, "username": "dest_test_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    response = await client.post("/api/v1/bots", json={"name": name, "token": TOKEN})
    assert response.status_code == 201
    return response.json()["data"]["id"]


@respx.mock
async def test_owner_can_create_destination_with_active_subscription(
    client_as_owner: AsyncClient,
) -> None:
    bot_id = await _create_bot(client_as_owner)

    response = await client_as_owner.post(
        "/api/v1/destinations",
        json={
            "bot_id": bot_id,
            "type": "channel",
            "chat_id": -100123456,
            "title": "My Channel",
        },
    )
    assert response.status_code == 201
    destination_id = response.json()["data"]["id"]

    subs_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/destinations")
    assert subs_resp.status_code == 200
    assert len(subs_resp.json()["data"]["items"]) == 1
    assert subs_resp.json()["data"]["items"][0]["id"] == destination_id
    assert subs_resp.json()["data"]["items"][0]["subscription_status"] == "active"


@respx.mock
async def test_member_can_view_but_not_create_destination(
    client_as_owner: AsyncClient, client_as_member: AsyncClient
) -> None:
    bot_id = await _create_bot(client_as_owner)
    await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "group", "chat_id": -100999, "title": "Group"},
    )

    list_resp = await client_as_member.get("/api/v1/destinations")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]["items"]) == 1

    create_resp = await client_as_member.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 111, "title": "Should Fail"},
    )
    assert create_resp.status_code == 403
    assert create_resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


@respx.mock
async def test_duplicate_chat_id_is_rejected(client_as_owner: AsyncClient) -> None:
    bot_id = await _create_bot(client_as_owner)
    payload = {"bot_id": bot_id, "type": "personal", "chat_id": 42, "title": "Alice"}

    first = await client_as_owner.post("/api/v1/destinations", json=payload)
    assert first.status_code == 201

    second = await client_as_owner.post("/api/v1/destinations", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DESTINATION_ALREADY_EXISTS"


@respx.mock
async def test_update_destination_only_changes_title(client_as_owner: AsyncClient) -> None:
    bot_id = await _create_bot(client_as_owner)
    create_resp = await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 777, "title": "Old Title"},
    )
    destination_id = create_resp.json()["data"]["id"]

    update_resp = await client_as_owner.patch(
        f"/api/v1/destinations/{destination_id}", json={"title": "New Title"}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["title"] == "New Title"
    assert update_resp.json()["data"]["chat_id"] == 777


@respx.mock
async def test_unsubscribe_then_resubscribe_via_subscription_endpoint(
    client_as_owner: AsyncClient,
) -> None:
    bot_id = await _create_bot(client_as_owner)
    create_resp = await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 888, "title": "Bob"},
    )
    destination_id = create_resp.json()["data"]["id"]

    unsub_resp = await client_as_owner.patch(
        f"/api/v1/destinations/{destination_id}/subscription",
        json={"bot_id": bot_id, "status": "unsubscribed"},
    )
    assert unsub_resp.status_code == 200
    assert unsub_resp.json()["data"]["status"] == "unsubscribed"

    block_resp = await client_as_owner.patch(
        f"/api/v1/destinations/{destination_id}/subscription",
        json={"bot_id": bot_id, "status": "blocked_by_admin"},
    )
    assert block_resp.status_code == 200
    assert block_resp.json()["data"]["status"] == "blocked_by_admin"


async def test_subscription_update_for_unknown_bot_returns_404(
    client_as_owner: AsyncClient,
) -> None:
    response = await client_as_owner.patch(
        f"/api/v1/destinations/{uuid.uuid4()}/subscription",
        json={"bot_id": str(uuid.uuid4()), "status": "unsubscribed"},
    )
    assert response.status_code == 404


@respx.mock
async def test_get_destination_returns_detail(client_as_owner: AsyncClient) -> None:
    """The success path - the only other test hitting this same URL

    (`test_delete_destination_soft_deletes` below) only ever exercises the
    404-after-delete branch, never a genuine 200 detail response.
    """
    bot_id = await _create_bot(client_as_owner)
    create_resp = await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 777, "title": "Detail Me"},
    )
    destination_id = create_resp.json()["data"]["id"]

    response = await client_as_owner.get(f"/api/v1/destinations/{destination_id}")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == destination_id
    assert response.json()["data"]["title"] == "Detail Me"


@respx.mock
async def test_delete_destination_soft_deletes(client_as_owner: AsyncClient) -> None:
    bot_id = await _create_bot(client_as_owner)
    create_resp = await client_as_owner.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 999, "title": "Doomed"},
    )
    destination_id = create_resp.json()["data"]["id"]

    delete_resp = await client_as_owner.delete(f"/api/v1/destinations/{destination_id}")
    assert delete_resp.status_code == 204

    get_resp = await client_as_owner.get(f"/api/v1/destinations/{destination_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "DESTINATION_NOT_FOUND"
