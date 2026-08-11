"""Celery tasks for the `billing` module.

Scope: plans, usage metering, and retention.

Queue: `billing.metering` - registered via `task_routes` in
`app.core.celery_app` (`app.modules.billing.tasks.*`). Celery tasks are
always sync functions (async I/O wrapped in `asyncio.run()`) -
`WorkerAsyncSessionFactory` MUST be used here (not the FastAPI process's
`AsyncSessionFactory`); see `modules/messaging/tasks.py` for the full
explanation of why (event-loop lifecycle).

`record_usage_event` is deliberately NOT made idempotent via a dedup key
(unlike `send_message_to_destination`, which must be idempotent because its
effect is "actually send a message to Telegram") - each call represents ONE
genuinely new metering event (there's no natural dedup key for "1 inbound
update"/"1 message sent" other than `UsageEvent.id` itself), so a
fail-then-succeed retry doesn't cause double counting - only a retry
exhausting `max_retries` causes one event to go unrecorded
(under-counting), accepted as an "okay to be eventual" trade-off for this
low-priority metering task - it must never hold up or fail the
webhook/message send itself.

`purge_expired_usage_data` (Celery beat, daily schedule - see
`beat_schedule` in `app.core.celery_app`): hard-deletes `UsageEvent` +
`messaging.DeliveryLog` rows older than each org's `Plan.retention_days`
(see the design notes in `model.py`). DELIBERATELY does NOT use
`bind=True`/retry like `record_usage_event` - purging is naturally
idempotent (deleting a row that's already gone is a no-op), and tomorrow's
daily beat run automatically "catches up" if one run fails, so a manual
retry inside this task would only add complexity with no benefit. Any
exception is left to propagate as-is (shows up as `FAILURE` in Flower for
observability) rather than being silently swallowed.
"""

from __future__ import annotations

import asyncio
import functools
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from app.core.celery_app import celery_app
from app.core.database import WorkerAsyncSessionFactory
from app.modules.billing import service
from app.modules.billing.model import UsageEventType
from app.modules.messaging import purge_delivery_logs_batch

logger = structlog.get_logger(__name__)

_MAX_METERING_RETRIES = 3
_PURGE_BATCH_SIZE = 1000


async def _record_usage_event_async(
    *,
    tenant_id: str,
    event_type: str,
    bot_id: str,
    destination_id: str | None,
    message_id: str | None,
    delivery_log_id: str | None,
) -> None:
    async with WorkerAsyncSessionFactory() as session:
        await service.create_usage_event(
            session,
            tenant_id=uuid.UUID(tenant_id),
            event_type=UsageEventType(event_type),
            bot_id=uuid.UUID(bot_id),
            destination_id=uuid.UUID(destination_id) if destination_id else None,
            message_id=uuid.UUID(message_id) if message_id else None,
            delivery_log_id=uuid.UUID(delivery_log_id) if delivery_log_id else None,
        )
        await session.commit()


@celery_app.task(
    name="app.modules.billing.tasks.record_usage_event",
    bind=True,
    max_retries=_MAX_METERING_RETRIES,
)
def record_usage_event(  # type: ignore[no-untyped-def]
    self,
    *,
    tenant_id: str,
    event_type: str,
    bot_id: str,
    destination_id: str | None = None,
    message_id: str | None = None,
    delivery_log_id: str | None = None,
) -> None:
    try:
        asyncio.run(
            _record_usage_event_async(
                tenant_id=tenant_id,
                event_type=event_type,
                bot_id=bot_id,
                destination_id=destination_id,
                message_id=message_id,
                delivery_log_id=delivery_log_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        # A DB write can fail transiently for any number of reasons (see
        # the module docstring).
        if self.request.retries >= self.max_retries:
            # Permanent failure: under-counting is accepted, but not
            # silent - log ERROR so it can be investigated if this happens
            # often (unlike `DeliveryLog.failed`, which must be recorded to
            # the DB - metering has no "row placeholder" to mark as failed,
            # and there's no user-facing effect that needs to be surfaced).
            logger.error(
                "usage_event_record_failed_permanently",
                tenant_id=tenant_id,
                event_type=event_type,
                bot_id=bot_id,
                error=str(exc),
            )
            return
        # Exponential backoff - 1s, 2s, 4s.
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


async def _purge_all_batches(batch_fn: functools.partial, session: object) -> int:
    """Calls `batch_fn()` repeatedly until it returns `0`, committing

    `session` between batches (in the task, not the repository/service,
    consistent with this module's commit-in-task pattern) - used for both
    `UsageEvent` and `DeliveryLog`, since both are "delete at most
    `_PURGE_BATCH_SIZE` rows per call" (see each repository's
    `delete_before` docstring). `batch_fn` is deliberately a
    `functools.partial` (not a lambda) - the `tenant_id`/`before` arguments
    are bound EAGERLY when it's created on each loop iteration in the
    caller, avoiding a lambda that could closure-capture the loop variable (ruff B023).
    """
    total = 0
    while True:
        deleted = await batch_fn()
        total += deleted
        if deleted == 0:
            break
        await session.commit()  # type: ignore[attr-defined]
    return total


async def _purge_expired_usage_data_async() -> dict[str, int]:
    async with WorkerAsyncSessionFactory() as session:
        targets = await service.get_purge_targets(session)
        now = datetime.now(UTC)
        summary = {
            "organizations_processed": len(targets),
            "usage_events_deleted": 0,
            "delivery_logs_deleted": 0,
        }
        for tenant_id, retention_days in targets:
            cutoff = now - timedelta(days=retention_days)
            summary["usage_events_deleted"] += await _purge_all_batches(
                functools.partial(
                    service.purge_usage_events_batch,
                    session,
                    tenant_id=tenant_id,
                    before=cutoff,
                    batch_size=_PURGE_BATCH_SIZE,
                ),
                session,
            )
            summary["delivery_logs_deleted"] += await _purge_all_batches(
                functools.partial(
                    purge_delivery_logs_batch,
                    session,
                    tenant_id=tenant_id,
                    before=cutoff,
                    batch_size=_PURGE_BATCH_SIZE,
                ),
                session,
            )
        return summary


@celery_app.task(name="app.modules.billing.tasks.purge_expired_usage_data")
def purge_expired_usage_data() -> None:
    """Hard-deletes expired `UsageEvent`/`DeliveryLog` rows for EVERY org

    (see the module docstring above for the retry/idempotency design).
    Triggered by the daily Celery beat, but can also be called manually
    (`purge_expired_usage_data.delay()`) for backfill/testing.
    """
    summary = asyncio.run(_purge_expired_usage_data_async())
    logger.info("usage_data_purged", **summary)
