"""Data access for the `bots` module.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

The default methods (`get_active`/`list_active`) always exclude soft-deleted
rows and filter by the `tenant_id` context. `get_active_by_telegram_bot_id`
deliberately queries across tenants - marked explicitly `# tenant-agnostic`
because a Telegram bot's uniqueness (a single BotFather token can only be
registered once platform-wide) isn't a per-tenant concept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.bots.model import Bot, BotAssignment


class BotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        telegram_bot_id: int,
        username: str,
        token_encrypted: str,
        token_last_four: str,
        webhook_secret_encrypted: str,
        api_key_hash: str,
        api_key_prefix: str,
        outbound_callback_url: str | None = None,
    ) -> Bot:
        # Takes already-encrypted values, never plaintext - encryption is a
        # service-layer decision (`service.py`'s `_hash_api_key`-style
        # helpers), this layer is pure data access. See
        # `private/specs/2026-08-12-bot-secret-encryption-design.md`.
        bot = Bot(
            tenant_id=tenant_id,
            name=name,
            telegram_bot_id=telegram_bot_id,
            username=username,
            token_encrypted=token_encrypted,
            token_last_four=token_last_four,
            webhook_secret_encrypted=webhook_secret_encrypted,
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            outbound_callback_url=outbound_callback_url,
        )
        self._session.add(bot)
        await self._session.flush()
        return bot

    async def get_active(self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> Bot | None:
        """Default lookup: excludes soft-deleted rows, scoped to the tenant."""
        stmt = select(Bot).where(
            Bot.id == bot_id, Bot.tenant_id == tenant_id, Bot.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_deleted(self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> Bot | None:
        """Explicitly includes soft-deleted rows - for audit/admin use cases."""
        stmt = select(Bot).where(Bot.id == bot_id, Bot.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_id(self, bot_id: uuid.UUID) -> Bot | None:
        # tenant-agnostic: used ONLY by the inbound webhook handler
        # (`modules/webhooks`), which has no tenant context from a JWT (its
        # auth is via `X-Telegram-Bot-Api-Secret-Token`, not a Bearer token)
        # - the tenant is only known from `Bot.tenant_id` once the bot is
        # found. Dashboard/JWT endpoints must still use `get_active`
        # (tenant-scoped).
        stmt = select(Bot).where(Bot.id == bot_id, Bot.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_api_key_hash(self, api_key_hash: str) -> Bot | None:
        # tenant-agnostic: used ONLY by the dual-auth external messaging
        # endpoint (`core.deps.get_bot_from_api_key`, via the
        # `X-Bot-Api-Key` header) - the tenant identity is only known from
        # `Bot.tenant_id` once the bot is found, same pattern as
        # `get_active_by_id` (used by inbound webhooks).
        stmt = select(Bot).where(Bot.api_key_hash == api_key_hash, Bot.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_telegram_bot_id(self, telegram_bot_id: int) -> Bot | None:
        # tenant-agnostic: a Telegram bot's uniqueness (`telegram_bot_id`)
        # applies platform-wide, not per-tenant - see the partial unique
        # index in `modules/bots/model.py`. The result is only used to check
        # for a registration conflict (`BotAlreadyRegisteredError`), never
        # exposed cross-tenant in an API response.
        stmt = select(Bot).where(Bot.telegram_bot_id == telegram_bot_id, Bot.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, *, tenant_id: uuid.UUID, params: PageParams) -> Page[Bot]:
        base = select(Bot).where(Bot.tenant_id == tenant_id, Bot.deleted_at.is_(None))
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = base.order_by(Bot.created_at.desc()).limit(params.limit).offset(params.offset)
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def list_all_active(self, *, tenant_id: uuid.UUID) -> list[Bot]:
        """Every active bot for this tenant, WITHOUT pagination - unlike

        `list_active` (used for dashboard listing). Used by
        `service.cascade_delete_for_organization` (the organization-delete
        cascade, see its docstring there), which needs ALL rows at once to
        soft-delete, not a single page.
        """
        stmt = select(Bot).where(Bot.tenant_id == tenant_id, Bot.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, bot: Bot) -> None:
        bot.deleted_at = datetime.now(UTC)
        await self._session.flush()


class BotAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
    ) -> BotAssignment:
        assignment = BotAssignment(tenant_id=tenant_id, bot_id=bot_id, user_id=user_id)
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    async def get_active(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
    ) -> BotAssignment | None:
        stmt = select(BotAssignment).where(
            BotAssignment.tenant_id == tenant_id,
            BotAssignment.bot_id == bot_id,
            BotAssignment.user_id == user_id,
            BotAssignment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_deleted(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
    ) -> BotAssignment | None:
        """Includes soft-deleted rows - used by `service.assign_bot` to

        reactivate an old row instead of inserting a new one (see the
        `BotAssignment` docstring in `model.py`).
        """
        stmt = select(BotAssignment).where(
            BotAssignment.tenant_id == tenant_id,
            BotAssignment.bot_id == bot_id,
            BotAssignment.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_for_bot(
        self, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, params: PageParams
    ) -> Page[BotAssignment]:
        base = select(BotAssignment).where(
            BotAssignment.tenant_id == tenant_id,
            BotAssignment.bot_id == bot_id,
            BotAssignment.deleted_at.is_(None),
        )
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(BotAssignment.created_at.desc()).limit(params.limit).offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def list_all_active_for_tenant(self, *, tenant_id: uuid.UUID) -> list[BotAssignment]:
        """Every active assignment for this tenant (across all bots),

        WITHOUT pagination - unlike `list_active_for_bot` (scoped to one
        bot, paginated). Used by `service.cascade_delete_for_organization`
        (the organization-delete cascade).
        """
        stmt = select(BotAssignment).where(
            BotAssignment.tenant_id == tenant_id, BotAssignment.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(self, assignment: BotAssignment) -> None:
        assignment.deleted_at = datetime.now(UTC)
        await self._session.flush()
