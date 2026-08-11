"""Data access for the `messaging` module.

Scope: sending messages, templates, scheduling, and delivery tracking.

`DeliveryLogRepository.get_by_id` & `MessageRepository.list_due_for_dispatch`
are deliberately tenant-agnostic (marked `# tenant-agnostic`) - both are
called from the Celery worker (`tasks.py`) before/without a tenant context:
`get_by_id` is the send-task entrypoint (it only has a `delivery_log_id`,
the tenant is only known AFTER the row is loaded, same pattern as
`bots.get_bot_for_webhook`), and `list_due_for_dispatch` deliberately scans
across all tenants (the scheduled dispatcher on `messaging.scheduled` must
see scheduled messages for every tenant). Every other method always takes
an explicit `tenant_id` once the caller already knows the tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.messaging.model import (
    DeliveryLog,
    DeliveryStatus,
    MediaType,
    Message,
    MessageContentType,
    MessageTemplate,
    ParseMode,
)


class MessageTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, tenant_id: uuid.UUID, name: str, body: str, parse_mode: ParseMode | None
    ) -> MessageTemplate:
        template = MessageTemplate(tenant_id=tenant_id, name=name, body=body, parse_mode=parse_mode)
        self._session.add(template)
        await self._session.flush()
        return template

    async def get_active(
        self, *, tenant_id: uuid.UUID, template_id: uuid.UUID
    ) -> MessageTemplate | None:
        stmt = select(MessageTemplate).where(
            MessageTemplate.id == template_id,
            MessageTemplate.tenant_id == tenant_id,
            MessageTemplate.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(
        self, *, tenant_id: uuid.UUID, params: PageParams
    ) -> Page[MessageTemplate]:
        base = select(MessageTemplate).where(
            MessageTemplate.tenant_id == tenant_id, MessageTemplate.deleted_at.is_(None)
        )
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(MessageTemplate.created_at.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def soft_delete(self, template: MessageTemplate) -> None:
        template.deleted_at = datetime.now(UTC)
        await self._session.flush()


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        bot_id: uuid.UUID,
        template_id: uuid.UUID | None,
        content_type: MessageContentType,
        text: str | None,
        parse_mode: ParseMode | None,
        media_type: MediaType | None,
        media_url: str | None,
        inline_keyboard: dict | None,
        poll: dict | None,
        scheduled_at: datetime | None,
    ) -> Message:
        message = Message(
            tenant_id=tenant_id,
            bot_id=bot_id,
            template_id=template_id,
            content_type=content_type,
            text=text,
            parse_mode=parse_mode,
            media_type=media_type,
            media_url=media_url,
            inline_keyboard=inline_keyboard,
            poll=poll,
            scheduled_at=scheduled_at,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def get_active(self, *, tenant_id: uuid.UUID, message_id: uuid.UUID) -> Message | None:
        stmt = select(Message).where(
            Message.id == message_id, Message.tenant_id == tenant_id, Message.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_dispatched(self, message: Message) -> None:
        message.dispatched_at = datetime.now(UTC)
        await self._session.flush()

    async def soft_delete(self, message: Message) -> None:
        message.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def list_pending_for_tenant(self, *, tenant_id: uuid.UUID) -> list[Message]:
        """Active messages for this tenant that haven't been dispatched

        (`dispatched_at IS NULL`), WITHOUT pagination. Used by
        `service.cascade_delete_pending_messages` (the organization-delete
        cascade, see its docstring there) - unlike `get_active`/regular
        dashboard listing, the cascade needs ALL pending rows at once, not
        historical already-sent messages.
        """
        stmt = select(Message).where(
            Message.tenant_id == tenant_id,
            Message.dispatched_at.is_(None),
            Message.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_due_for_dispatch(self, *, now: datetime) -> list[Message]:
        # tenant-agnostic: the scheduled dispatcher scans across all tenants
        # (see the module docstring above).
        stmt = select(Message).where(
            Message.scheduled_at.isnot(None),
            Message.scheduled_at <= now,
            Message.dispatched_at.is_(None),
            Message.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class DeliveryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create_queued(
        self, *, tenant_id: uuid.UUID, message_id: uuid.UUID, destination_ids: list[uuid.UUID]
    ) -> list[DeliveryLog]:
        logs = [
            DeliveryLog(
                tenant_id=tenant_id,
                message_id=message_id,
                destination_id=destination_id,
                status=DeliveryStatus.QUEUED,
            )
            for destination_id in destination_ids
        ]
        self._session.add_all(logs)
        await self._session.flush()
        return logs

    async def get_by_id(self, delivery_log_id: uuid.UUID) -> DeliveryLog | None:
        # tenant-agnostic: worker entrypoint, see the module docstring above.
        stmt = select(DeliveryLog).where(DeliveryLog.id == delivery_log_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_message(
        self, *, tenant_id: uuid.UUID, message_id: uuid.UUID
    ) -> list[DeliveryLog]:
        stmt = select(DeliveryLog).where(
            DeliveryLog.tenant_id == tenant_id, DeliveryLog.message_id == message_id
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_sent(self, log: DeliveryLog, *, telegram_message_id: int | None) -> None:
        log.status = DeliveryStatus.SENT
        log.telegram_message_id = telegram_message_id
        log.error_reason = None
        log.sent_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_failed(self, log: DeliveryLog, *, error_reason: str) -> None:
        log.status = DeliveryStatus.FAILED
        log.error_reason = error_reason[:2000]
        await self._session.flush()

    async def delete_before(
        self, *, tenant_id: uuid.UUID, before: datetime, batch_size: int
    ) -> int:
        """Hard-deletes ONE batch (at most `batch_size` rows) of `DeliveryLog`

        for this tenant with `created_at < before` (retention purge - the
        policy is centralized in `modules/billing.Plan.retention_days`,
        which calls in via `service.purge_delivery_logs_batch`, exposed
        through `__init__.py`). Same batch-via-subquery pattern as
        `billing.UsageEventRepository.delete_before` - see its docstring
        for why `WHERE id IN (SELECT ... LIMIT)` is used.
        """
        subquery = (
            select(DeliveryLog.id)
            .where(DeliveryLog.tenant_id == tenant_id, DeliveryLog.created_at < before)
            .limit(batch_size)
        )
        stmt = delete(DeliveryLog).where(DeliveryLog.id.in_(subquery))
        result = await self._session.execute(stmt)
        return result.rowcount or 0  # type: ignore[attr-defined]
