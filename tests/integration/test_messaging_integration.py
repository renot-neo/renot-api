"""Integration tests for the `app.modules.messaging` router.

Real Postgres, no mocked repository. Telegram API calls mocked via `respx`;
Celery dispatch (`send_message_to_destination.delay(...)`, fired by the
router right after `session.commit()`) mocked via `patch_task_delay` - see
`tests/support/db.py`'s module docstring for why actual task execution is
out of scope here. Needs a real `Bot` + `Destination` (with an `active`
subscription) first, same helper pattern as
`test_destinations_integration.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import User
from app.modules.messaging import service as messaging_service
from app.modules.messaging import tasks as messaging_tasks
from app.modules.organizations.model import Organization

TOKEN = "123456:messaging-integration-token"


async def _create_bot_and_destination(client: AsyncClient) -> tuple[str, str]:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 4242, "username": "messaging_test_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    bot_resp = await client.post("/api/v1/bots", json={"name": "Messaging Bot", "token": TOKEN})
    bot_id = bot_resp.json()["data"]["id"]

    dest_resp = await client.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 555, "title": "Recipient"},
    )
    destination_id = dest_resp.json()["data"]["id"]
    return bot_id, destination_id


@respx.mock
async def test_send_immediate_message_enqueues_one_delivery_per_destination(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    mock_delay = patch_task_delay(messaging_tasks.send_message_to_destination)

    response = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Hello there!",
        },
    )
    assert response.status_code == 201
    message_id = response.json()["data"]["id"]

    mock_delay.assert_called_once()
    assert mock_delay.call_args.kwargs["delivery_log_id"]

    status_resp = await client_as_owner.get(f"/api/v1/messages/{message_id}/status")
    assert status_resp.status_code == 200
    body = status_resp.json()["data"]
    assert body["total_destinations"] == 1
    assert body["counts"]["queued"] == 1


@respx.mock
async def test_scheduled_message_does_not_enqueue_immediately(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    mock_delay = patch_task_delay(messaging_tasks.send_message_to_destination)

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    response = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Scheduled hello",
            "scheduled_at": future,
        },
    )
    assert response.status_code == 201
    mock_delay.assert_not_called()


@respx.mock
async def test_send_message_rejects_unsubscribed_destination(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    await client_as_owner.patch(
        f"/api/v1/destinations/{destination_id}/subscription",
        json={"bot_id": bot_id, "status": "unsubscribed"},
    )

    response = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Should fail",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DESTINATION_NOT_SUBSCRIBED"


async def test_member_can_send_message(
    client_as_member: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    # `message:send` is granted to member - use a real unknown message id
    # to avoid re-doing full bot/destination setup here, just prove the
    # permission gate lets member through to the service layer (404
    # MESSAGE_NOT_FOUND on GET, not 403) - `_assert_bot_access` ("MEMBER
    # can only access assigned bots") is only reached AFTER the message is
    # found, so a not-found message never triggers it - see
    # `test_member_without_bot_assignment_...` below for that scoping.
    response = await client_as_member.get(f"/api/v1/messages/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MESSAGE_NOT_FOUND"


@respx.mock
async def test_member_without_bot_assignment_cannot_send_message(
    client_as_owner: AsyncClient,
    client_as_member: AsyncClient,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    # "MEMBER can only access assigned bots" - member NOT assigned to
    # this bot gets 403 `BOT_NOT_ASSIGNED`, distinct from `INSUFFICIENT_PERMISSION`
    # (member DOES have the `message:send` permission generically, just not
    # for this specific bot).
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    response = await client_as_member.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Should be blocked",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BOT_NOT_ASSIGNED"


@respx.mock
async def test_member_with_bot_assignment_can_send_message(
    client_as_owner: AsyncClient,
    client_as_member: AsyncClient,
    test_user_member: User,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    assign_resp = await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    assert assign_resp.status_code == 201

    response = await client_as_member.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Should go through",
        },
    )
    assert response.status_code == 201
    message_id = response.json()["data"]["id"]

    status_resp = await client_as_member.get(f"/api/v1/messages/{message_id}/status")
    assert status_resp.status_code == 200


@respx.mock
async def test_member_loses_access_after_unassignment(
    client_as_owner: AsyncClient,
    client_as_member: AsyncClient,
    test_user_member: User,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    await client_as_owner.post(
        f"/api/v1/bots/{bot_id}/assignments", json={"user_id": str(test_user_member.id)}
    )
    unassign_resp = await client_as_owner.delete(
        f"/api/v1/bots/{bot_id}/assignments/{test_user_member.id}"
    )
    assert unassign_resp.status_code == 204

    response = await client_as_member.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Should be blocked again",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BOT_NOT_ASSIGNED"


@respx.mock
async def test_message_template_crud(
    client_as_owner: AsyncClient,
) -> None:
    create_resp = await client_as_owner.post(
        "/api/v1/message-templates",
        json={"name": "Welcome", "body": "Hi {{name}}, welcome to {{product}}!"},
    )
    assert create_resp.status_code == 201
    template_id = create_resp.json()["data"]["id"]

    # Detail fetch success - the only other GET on this URL below only
    # ever exercises the 404-after-delete branch.
    detail_resp = await client_as_owner.get(f"/api/v1/message-templates/{template_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["name"] == "Welcome"

    list_resp = await client_as_owner.get("/api/v1/message-templates")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["data"]["items"]) == 1

    update_resp = await client_as_owner.patch(
        f"/api/v1/message-templates/{template_id}", json={"body": "Hey {{name}}!"}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["body"] == "Hey {{name}}!"

    delete_resp = await client_as_owner.delete(f"/api/v1/message-templates/{template_id}")
    assert delete_resp.status_code == 204

    get_resp = await client_as_owner.get(f"/api/v1/message-templates/{template_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "MESSAGE_TEMPLATE_NOT_FOUND"


@respx.mock
async def test_send_message_with_template_renders_variables(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    template_resp = await client_as_owner.post(
        "/api/v1/message-templates",
        json={"name": "Greeting", "body": "Hi {{name}}, welcome to Renot!"},
    )
    template_id = template_resp.json()["data"]["id"]

    response = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "template_id": template_id,
            "template_variables": {"name": "Alice"},
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["text"] == "Hi Alice, welcome to Renot!"


@respx.mock
async def test_send_message_with_missing_template_variable_fails(
    client_as_owner: AsyncClient, patch_task_delay: Callable[[object], Mock]
) -> None:
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)
    patch_task_delay(messaging_tasks.send_message_to_destination)

    template_resp = await client_as_owner.post(
        "/api/v1/message-templates",
        json={"name": "Greeting", "body": "Hi {{name}}, welcome to Renot!"},
    )
    template_id = template_resp.json()["data"]["id"]

    response = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "template_id": template_id,
            "template_variables": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TEMPLATE_VARIABLE_MISSING"


@respx.mock
async def test_pending_scheduled_message_not_dispatched_after_organization_deleted(
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    """Covers the bypass path that's the reason

    `modules.messaging.cascade_delete_pending_messages` is REQUIRED (not
    okay-to-orphan): `dispatch_due_scheduled_messages` (called by Celery
    beat `messaging.scheduled`, `tasks.dispatch_scheduled_message`) scans
    `Message` ACROSS ALL TENANTS with no active-organization filter - if a
    scheduled message were left active, it would still get sent even after
    its org has been deleted. This proves that does NOT happen after the cascade.

    Tested at the SERVICE level (`dispatch_due_scheduled_messages` called
    directly against `db_session`), NOT via the real Celery task
    `tasks.dispatch_scheduled_message()` - that task uses
    `WorkerAsyncSessionFactory` (a separate engine from this test's
    `test_engine` container, see the `tests/support/db.py` module
    docstring: running a real Celery task needs its own `asyncio.run()`/
    `WorkerAsyncSessionFactory` plumbing, deliberately out of scope for a
    router/service-level test) - if called directly here, that task would
    never see this test's data at all (a different DB connection). The
    service level that task calls into (`list_due_for_dispatch`) is the
    deepest point that can still be tested against a real Postgres here -
    its behavior is identical to what beat would actually execute.
    """
    mock_delay = patch_task_delay(messaging_tasks.send_message_to_destination)
    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)

    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    create_resp = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Scheduled hello",
            "scheduled_at": future,
        },
    )
    assert create_resp.status_code == 201
    message_id = create_resp.json()["data"]["id"]
    mock_delay.assert_not_called()  # scheduled, not dispatched immediately

    # Make the message due (push it into the past) directly in the DB -
    # the API itself rejects a past `scheduled_at` at create time
    # (`service.py::create_message`), so this uses the same trick as the
    # `is_organization_active` guard test (raw SQL, not through the endpoint).
    await db_session.execute(
        text("UPDATE messages SET scheduled_at = now() - interval '1 minute' WHERE id = :id"),
        {"id": message_id},
    )
    await db_session.commit()

    delete_resp = await client_as_owner.delete(f"/api/v1/organizations/{test_organization.id}")
    assert delete_resp.status_code == 204

    due = await messaging_service.dispatch_due_scheduled_messages(db_session)

    assert due == []
    mock_delay.assert_not_called()

    row = await db_session.execute(
        text("SELECT deleted_at FROM messages WHERE id = :id"), {"id": message_id}
    )
    assert row.scalar_one() is not None
