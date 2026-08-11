"""Integration tests for retention purge.

`billing.service.get_purge_targets`/`purge_usage_events_batch` +
`app.modules.messaging.purge_delivery_logs_batch` are called DIRECTLY via
`db_session` (not HTTP - purge has no public endpoint, it's a purely
internal Celery beat operation), same pattern as the
`test_organization`/`test_user_owner` fixtures (`tests/support/db.py`),
which also call service functions directly. Actually executing the Celery
task itself (`billing/tasks.py::purge_expired_usage_data`,
`WorkerAsyncSessionFactory`) STAYS out of scope here - live-E2E-only, same
precedent as every other Celery task ("real task execution deliberately
out of scope for tests/integration/feature").

`created_at` is backdated via raw SQL `UPDATE`/set manually via `INSERT`
directly - there's no public endpoint for that, reasonable for simulating
"old data" in a test.
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

from app.modules.billing import service as billing_service
from app.modules.messaging import purge_delivery_logs_batch
from app.modules.messaging import tasks as messaging_tasks
from app.modules.organizations.model import Organization

TOKEN = "123456:purge-integration-token"


async def _create_bot_and_destination(client: AsyncClient) -> tuple[str, str]:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 9191, "username": "purge_test_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    bot_resp = await client.post("/api/v1/bots", json={"name": "Purge Bot", "token": TOKEN})
    bot_id = bot_resp.json()["data"]["id"]

    dest_resp = await client.post(
        "/api/v1/destinations",
        json={"bot_id": bot_id, "type": "personal", "chat_id": 777, "title": "Purge Recipient"},
    )
    destination_id = dest_resp.json()["data"]["id"]
    return bot_id, destination_id


@respx.mock
async def test_purge_only_deletes_rows_older_than_plan_retention(
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    patch_task_delay: Callable[[object], Mock],
) -> None:
    # Assign the org an active Plan first (lazy assign-on-read) -
    # `get_purge_targets` only sees orgs with an active `OrganizationPlan` row.
    usage_resp = await client_as_owner.get("/api/v1/billing/usage")
    assert usage_resp.status_code == 200

    # The two `POST /api/v1/messages` calls below fire a real
    # `send_message_to_destination.delay(...)` (router->DB->Celery pattern) -
    # mocked here same as every other messaging-integration test (see
    # `tests/support/db.py`'s module docstring). Without this, `.delay()`
    # tries to reach a real Celery broker/result backend - this test only
    # needs the resulting `DeliveryLog` rows to exist for the purge logic
    # under test, not actual task dispatch.
    patch_task_delay(messaging_tasks.send_message_to_destination)

    bot_id, destination_id = await _create_bot_and_destination(client_as_owner)

    old_msg_resp = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "Old message - should be purged",
        },
    )
    old_message_id = old_msg_resp.json()["data"]["id"]

    new_msg_resp = await client_as_owner.post(
        "/api/v1/messages",
        json={
            "bot_id": bot_id,
            "destination_ids": [destination_id],
            "content_type": "text",
            "text": "New message - should survive",
        },
    )
    new_message_id = new_msg_resp.json()["data"]["id"]

    now = datetime.now(UTC)
    old_cutoff = now - timedelta(days=40)  # older than Default plan's 30-day retention

    # Backdate the OLD message's `DeliveryLog` (the NEW one keeps its real
    # `created_at`, i.e. "now").
    await db_session.execute(
        text("UPDATE delivery_logs SET created_at = :old WHERE message_id = :message_id"),
        {"old": old_cutoff, "message_id": old_message_id},
    )

    # Two `UsageEvent` rows inserted directly (no endpoint writes this
    # table directly outside the Celery metering task) - one old, one new.
    old_usage_event_id = uuid.uuid4()
    new_usage_event_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO usage_events (id, tenant_id, event_type, bot_id, created_at, updated_at)"
            " VALUES (:id, :tenant_id, 'IN', :bot_id, :created_at, :created_at)"
        ),
        {
            "id": old_usage_event_id,
            "tenant_id": test_organization.id,
            "bot_id": bot_id,
            "created_at": old_cutoff,
        },
    )
    await db_session.execute(
        text(
            "INSERT INTO usage_events (id, tenant_id, event_type, bot_id, created_at, updated_at)"
            " VALUES (:id, :tenant_id, 'IN', :bot_id, :created_at, :created_at)"
        ),
        {
            "id": new_usage_event_id,
            "tenant_id": test_organization.id,
            "bot_id": bot_id,
            "created_at": now,
        },
    )
    await db_session.commit()

    targets = await billing_service.get_purge_targets(db_session)
    target = next(t for t in targets if str(t[0]) == str(test_organization.id))
    assert target[1] == 30  # Default plan's seeded retention_days

    retention_cutoff = now - timedelta(days=target[1])

    usage_deleted = await billing_service.purge_usage_events_batch(
        db_session, tenant_id=test_organization.id, before=retention_cutoff, batch_size=1000
    )
    assert usage_deleted == 1

    delivery_deleted = await purge_delivery_logs_batch(
        db_session, tenant_id=test_organization.id, before=retention_cutoff, batch_size=1000
    )
    assert delivery_deleted == 1

    remaining_usage = await db_session.execute(
        text("SELECT id FROM usage_events WHERE tenant_id = :tid"), {"tid": test_organization.id}
    )
    assert [row[0] for row in remaining_usage.all()] == [new_usage_event_id]

    remaining_logs = await db_session.execute(
        text("SELECT message_id FROM delivery_logs WHERE tenant_id = :tid"),
        {"tid": test_organization.id},
    )
    assert [str(row[0]) for row in remaining_logs.all()] == [new_message_id]

    # Purge must NOT touch `Message` itself - only its `DeliveryLog` ledger
    # (see docstring `messaging.service.purge_delivery_logs_batch`).
    surviving_messages = await db_session.execute(
        text("SELECT id FROM messages WHERE tenant_id = :tid AND deleted_at IS NULL"),
        {"tid": test_organization.id},
    )
    surviving_ids = {str(row[0]) for row in surviving_messages.all()}
    assert old_message_id in surviving_ids
    assert new_message_id in surviving_ids


async def test_purge_targets_excludes_plan_with_null_retention(
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
) -> None:
    unlimited_plan_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO plans (id, name, monthly_event_quota, price, is_default,"
            " retention_days, created_at, updated_at)"
            " VALUES (:id, :name, NULL, NULL, false, NULL, now(), now())"
        ),
        {"id": unlimited_plan_id, "name": f"Unlimited Retention {uuid.uuid4()}"},
    )
    await db_session.commit()

    subscribe_resp = await client_as_owner.post(
        "/api/v1/billing/subscribe-plan", json={"plan_id": str(unlimited_plan_id)}
    )
    assert subscribe_resp.status_code == 200

    targets = await billing_service.get_purge_targets(db_session)
    assert str(test_organization.id) not in {str(t[0]) for t in targets}
