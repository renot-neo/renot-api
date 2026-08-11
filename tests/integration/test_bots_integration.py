"""Integration tests for the `app.modules.bots` router.

Real Postgres, no mocked repository - Telegram Bot API calls mocked via
`respx` (never hits `api.telegram.org` for real).
"""

from __future__ import annotations

import uuid

import httpx
import respx
from httpx import AsyncClient

from app.modules.auth import User

TOKEN = "123456:integration-test-token"


def _mock_get_me(telegram_bot_id: int = 987654321, username: str = "integration_test_bot") -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": telegram_bot_id, "username": username}}
        )
    )


def _mock_set_webhook(ok: bool = True) -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(
            200 if ok else 400,
            json=(
                {"ok": ok, "result": True}
                if ok
                else {"ok": False, "error_code": 400, "description": "bad webhook url"}
            ),
        )
    )


@respx.mock
async def test_owner_can_register_bot_and_receives_api_key_once(
    client_as_owner: AsyncClient,
) -> None:
    _mock_get_me()
    _mock_set_webhook()

    response = await client_as_owner.post("/api/v1/bots", json={"name": "My Bot", "token": TOKEN})
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["name"] == "My Bot"
    assert body["username"] == "integration_test_bot"
    assert body["api_key"].startswith("tgbm_live_")
    assert body["token_last_four"] == TOKEN[-4:]


@respx.mock
async def test_member_cannot_register_bot(client_as_member: AsyncClient) -> None:
    _mock_get_me()
    _mock_set_webhook()

    response = await client_as_member.post("/api/v1/bots", json={"name": "My Bot", "token": TOKEN})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


@respx.mock
async def test_register_bot_rejects_invalid_token(client_as_owner: AsyncClient) -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )
    )

    response = await client_as_owner.post("/api/v1/bots", json={"name": "My Bot", "token": TOKEN})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BOT_TOKEN_INVALID"


@respx.mock
async def test_register_bot_rolls_back_when_webhook_setup_fails(
    client_as_owner: AsyncClient,
) -> None:
    _mock_get_me()
    _mock_set_webhook(ok=False)

    response = await client_as_owner.post("/api/v1/bots", json={"name": "My Bot", "token": TOKEN})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "BOT_WEBHOOK_SETUP_FAILED"

    # The bot must not have been left half-registered - list should be empty.
    list_resp = await client_as_owner.get("/api/v1/bots")
    assert list_resp.json()["data"]["items"] == []


@respx.mock
async def test_member_can_view_but_not_manage_bot(
    client_as_owner: AsyncClient, client_as_member: AsyncClient
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Viewable Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    view_resp = await client_as_member.get(f"/api/v1/bots/{bot_id}")
    assert view_resp.status_code == 200
    assert view_resp.json()["data"]["name"] == "Viewable Bot"

    update_resp = await client_as_member.patch(f"/api/v1/bots/{bot_id}", json={"name": "Hijacked"})
    assert update_resp.status_code == 403


@respx.mock
async def test_update_bot_can_clear_outbound_callback_url(client_as_owner: AsyncClient) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots",
        json={
            "name": "Callback Bot",
            "token": TOKEN,
            "outbound_callback_url": "https://example.com/callback",
        },
    )
    bot_id = create_resp.json()["data"]["id"]
    assert create_resp.json()["data"]["outbound_callback_url"] == "https://example.com/callback"

    clear_resp = await client_as_owner.patch(
        f"/api/v1/bots/{bot_id}", json={"outbound_callback_url": ""}
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["data"]["outbound_callback_url"] is None


@respx.mock
async def test_regenerate_api_key_returns_new_key(client_as_owner: AsyncClient) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Key Bot", "token": TOKEN}
    )
    old_key = create_resp.json()["data"]["api_key"]
    bot_id = create_resp.json()["data"]["id"]

    regen_resp = await client_as_owner.post(f"/api/v1/bots/{bot_id}/regenerate-key")
    assert regen_resp.status_code == 200
    new_key = regen_resp.json()["data"]["api_key"]
    assert new_key != old_key
    assert new_key.startswith("tgbm_live_")


@respx.mock
async def test_toggle_subscription_policy(client_as_owner: AsyncClient) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Policy Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]
    # `Bot.webhook_enabled` defaults to `False` (registration doesn't open
    # subscriptions automatically - an explicit toggle is required).
    assert create_resp.json()["data"]["webhook_enabled"] is False

    open_resp = await client_as_owner.patch(
        f"/api/v1/bots/{bot_id}/subscription-policy", json={"webhook_enabled": True}
    )
    assert open_resp.status_code == 200
    assert open_resp.json()["data"]["webhook_enabled"] is True


@respx.mock
async def test_delete_bot_soft_deletes_and_hides_it_from_subsequent_reads(
    client_as_owner: AsyncClient,
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Doomed Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    delete_resp = await client_as_owner.delete(f"/api/v1/bots/{bot_id}")
    assert delete_resp.status_code == 204

    get_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "BOT_NOT_FOUND"


async def test_get_unknown_bot_returns_404(client_as_owner: AsyncClient) -> None:
    response = await client_as_owner.get(f"/api/v1/bots/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOT_NOT_FOUND"


# --- BotAssignment ("MEMBER can only access assigned bots") ---


@respx.mock
async def test_owner_can_assign_and_list_bot_assignments(
    client_as_owner: AsyncClient, test_user_member: User
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Assignable Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    assign_resp = await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    assert assign_resp.status_code == 201
    assert assign_resp.json()["data"]["user_id"] == str(test_user_member.id)
    assert assign_resp.json()["data"]["bot_id"] == bot_id

    list_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/assignments")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]["items"]) == 1
    assert list_resp.json()["data"]["items"][0]["user_id"] == str(test_user_member.id)


@respx.mock
async def test_assign_bot_twice_returns_conflict(
    client_as_owner: AsyncClient, test_user_member: User
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Double Assign Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    second_resp = await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    assert second_resp.status_code == 409
    assert second_resp.json()["error"]["code"] == "BOT_ASSIGNMENT_ALREADY_EXISTS"


@respx.mock
async def test_assign_bot_rejects_user_not_in_organization(client_as_owner: AsyncClient) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Stranger Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    response = await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOT_ASSIGNMENT_USER_NOT_MEMBER"


@respx.mock
async def test_unassign_bot_removes_assignment(
    client_as_owner: AsyncClient, test_user_member: User
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Unassignable Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    unassign_resp = await client_as_owner.delete(
        f"/api/v1/bots/{bot_id}/assignments/{test_user_member.id}"
    )
    assert unassign_resp.status_code == 204

    list_resp = await client_as_owner.get(f"/api/v1/bots/{bot_id}/assignments")
    assert list_resp.json()["data"]["items"] == []


async def test_unassign_bot_raises_when_not_assigned(client_as_owner: AsyncClient) -> None:
    response = await client_as_owner.delete(
        f"/api/v1/bots/{uuid.uuid4()}/assignments/{uuid.uuid4()}"
    )
    assert response.status_code == 404
    # Bot itself doesn't exist either in this case - `BOT_NOT_FOUND` wins
    # (service checks bot existence before assignment existence).
    assert response.json()["error"]["code"] == "BOT_NOT_FOUND"


@respx.mock
async def test_member_cannot_manage_bot_assignments(
    client_as_owner: AsyncClient, client_as_member: AsyncClient, test_user_member: User
) -> None:
    _mock_get_me()
    _mock_set_webhook()
    create_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Protected Bot", "token": TOKEN}
    )
    bot_id = create_resp.json()["data"]["id"]

    response = await client_as_member.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"
