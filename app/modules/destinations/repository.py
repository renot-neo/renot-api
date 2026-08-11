"""Data access for the `destinations` module.

Scope: message destinations (chats/groups/channels) and their bot subscriptions.

The default methods (`get_active`/`list_active`) always exclude soft-deleted
rows and filter by the `tenant_id` context.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.destinations.model import (
    BotDestinationSubscription,
    Destination,
    DestinationType,
    SubscriptionStatus,
)


class DestinationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        type: DestinationType,
        chat_id: int,
        thread_id: int | None,
        title: str,
    ) -> Destination:
        destination = Destination(
            tenant_id=tenant_id, type=type, chat_id=chat_id, thread_id=thread_id, title=title
        )
        self._session.add(destination)
        await self._session.flush()
        return destination

    async def get_active(
        self, *, tenant_id: uuid.UUID, destination_id: uuid.UUID
    ) -> Destination | None:
        stmt = select(Destination).where(
            Destination.id == destination_id,
            Destination.tenant_id == tenant_id,
            Destination.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_chat(
        self, *, tenant_id: uuid.UUID, chat_id: int, thread_id: int | None
    ) -> Destination | None:
        """Used to check for a duplicate before inserting (the partial unique

        index in `model.py` is the last line of defense at the DB level).
        """
        stmt = select(Destination).where(
            Destination.tenant_id == tenant_id,
            Destination.chat_id == chat_id,
            Destination.thread_id == thread_id,
            Destination.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, *, tenant_id: uuid.UUID, params: PageParams) -> Page[Destination]:
        base = select(Destination).where(
            Destination.tenant_id == tenant_id, Destination.deleted_at.is_(None)
        )
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(Destination.created_at.desc()).limit(params.limit).offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def soft_delete(self, destination: Destination) -> None:
        destination.deleted_at = datetime.now(UTC)
        await self._session.flush()


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        bot_id: uuid.UUID,
        destination_id: uuid.UUID,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    ) -> BotDestinationSubscription:
        subscription = BotDestinationSubscription(
            tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id, status=status
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    async def get_active(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, destination_id: uuid.UUID
    ) -> BotDestinationSubscription | None:
        stmt = select(BotDestinationSubscription).where(
            BotDestinationSubscription.tenant_id == tenant_id,
            BotDestinationSubscription.bot_id == bot_id,
            BotDestinationSubscription.destination_id == destination_id,
            BotDestinationSubscription.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_for_bot(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, params: PageParams
    ) -> Page[BotDestinationSubscription]:
        base = select(BotDestinationSubscription).where(
            BotDestinationSubscription.tenant_id == tenant_id,
            BotDestinationSubscription.bot_id == bot_id,
            BotDestinationSubscription.deleted_at.is_(None),
        )
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(BotDestinationSubscription.created_at.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def list_active_for_destination(
        self, *, tenant_id: uuid.UUID, destination_id: uuid.UUID
    ) -> list[BotDestinationSubscription]:
        stmt = select(BotDestinationSubscription).where(
            BotDestinationSubscription.tenant_id == tenant_id,
            BotDestinationSubscription.destination_id == destination_id,
            BotDestinationSubscription.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, subscription: BotDestinationSubscription) -> None:
        subscription.deleted_at = datetime.now(UTC)
        await self._session.flush()
