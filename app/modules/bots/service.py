"""Business logic for the `bots` module.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

This is the layer the router calls into - the only place business logic
belongs.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.pagination import Page, PageParams
from app.modules.bots.exceptions import (
    BotAlreadyRegisteredError,
    BotApiKeyInvalidError,
    BotAssignmentAlreadyExistsError,
    BotAssignmentNotFoundError,
    BotAssignmentUserNotMemberError,
    BotNotFoundError,
    BotTokenInvalidError,
    BotWebhookSetupFailedError,
)
from app.modules.bots.model import Bot, BotAssignment
from app.modules.bots.repository import BotAssignmentRepository, BotRepository
from app.modules.organizations import get_membership
from app.shared.telegram_client import TelegramAPIError, get_me, set_webhook

# Format `tgbm_live_xxxxx` so a leaked key is easy to spot in git/logs. The
# plaintext-stored prefix (`api_key_prefix`) takes this prefix + the
# secret's first 4 characters, enough for a user to identify the key in the
# dashboard without exposing the full value.
_API_KEY_PREFIX = "tgbm_live_"
_API_KEY_PREFIX_DISPLAY_LEN = len(_API_KEY_PREFIX) + 4


def _hash_api_key(key: str) -> str:
    """SHA-256 fingerprint - the key is already high-entropy

    (`secrets.token_urlsafe`) and needs a deterministic lookup by value when
    a request comes in (the `X-Bot-Api-Key` header), same as
    `RefreshToken.token_hash` in `modules/auth` - a different case from a
    human password, which needs a salted slow-hash (argon2).
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _generate_api_key() -> tuple[str, str, str]:
    """Return `(plaintext, hash, display_prefix)`."""
    plaintext = f"{_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, _hash_api_key(plaintext), plaintext[:_API_KEY_PREFIX_DISPLAY_LEN]


def _webhook_url(bot_id: uuid.UUID) -> str:
    base = settings.telegram.webhook_base_url.rstrip("/")
    return f"{base}/api/v1/webhooks/telegram/{bot_id}"


async def register_bot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    token: str,
    outbound_callback_url: str | None = None,
) -> tuple[Bot, str]:
    """Registers a bot: validates the token via `getMe`, saves the bot

    record, then automatically `setWebhook`s with a unique secret token.
    Returns `(bot, api_key_plaintext)` - the plaintext API key is ONLY
    available at this point (shown once, at generate/regenerate time).

    If `setWebhook` fails after the bot record has been created, the
    exception is raised before the router gets to `session.commit()` - the
    entire registration (including the bot insert) is automatically rolled
    back when the session closes without a commit, so no "half-finished"
    bot record (webhook never attached) ever gets persisted.
    """
    try:
        me = await get_me(token)
    except TelegramAPIError as exc:
        raise BotTokenInvalidError() from exc

    telegram_bot_id = int(me["id"])
    repository = BotRepository(session)
    if await repository.get_active_by_telegram_bot_id(telegram_bot_id) is not None:
        raise BotAlreadyRegisteredError()

    api_key_plaintext, api_key_hash, api_key_prefix = _generate_api_key()
    webhook_secret = secrets.token_urlsafe(32)

    bot = await repository.create(
        tenant_id=tenant_id,
        name=name,
        telegram_bot_id=telegram_bot_id,
        username=me.get("username", ""),
        token=token,
        webhook_secret=webhook_secret,
        api_key_hash=api_key_hash,
        api_key_prefix=api_key_prefix,
        outbound_callback_url=outbound_callback_url,
    )

    try:
        await set_webhook(token, url=_webhook_url(bot.id), secret_token=webhook_secret)
    except TelegramAPIError as exc:
        raise BotWebhookSetupFailedError() from exc

    return bot, api_key_plaintext


async def get_bot(session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> Bot:
    bot = await BotRepository(session).get_active(tenant_id=tenant_id, bot_id=bot_id)
    if bot is None:
        raise BotNotFoundError()
    return bot


async def list_bots(
    session: AsyncSession, *, tenant_id: uuid.UUID, page_params: PageParams
) -> Page[Bot]:
    return await BotRepository(session).list_active(tenant_id=tenant_id, params=page_params)


async def update_bot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    name: str | None = None,
    outbound_callback_url: str | None = None,
) -> Bot:
    bot = await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    if name is not None:
        bot.name = name
    if outbound_callback_url is not None:
        # `None` (field omitted/`null`) means "leave unchanged"; an explicit
        # `""` means "clear it" - the way to distinguish "not sent" from
        # "sent but meant to be cleared" without a separate sentinel/
        # `exclude_unset`.
        bot.outbound_callback_url = outbound_callback_url or None
    await session.flush()
    return bot


async def regenerate_api_key(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID
) -> tuple[Bot, str]:
    bot = await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    api_key_plaintext, api_key_hash, api_key_prefix = _generate_api_key()
    bot.api_key_hash = api_key_hash
    bot.api_key_prefix = api_key_prefix
    await session.flush()
    return bot, api_key_plaintext


async def update_subscription_policy(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, webhook_enabled: bool
) -> Bot:
    """Toggles the "open/close subscription" flag (`Bot.webhook_enabled`)."""
    bot = await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    bot.webhook_enabled = webhook_enabled
    await session.flush()
    return bot


async def delete_bot(session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> None:
    bot = await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    await BotRepository(session).soft_delete(bot)


async def cascade_delete_for_organization(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Called by `modules/organizations.delete_organization` (the

    organization-delete cascade) - soft-deletes EVERY active `Bot`+
    `BotAssignment` for this tenant. This is REQUIRED (not optional /
    okay-to-orphan) because `get_bot_for_webhook` (the tenant-agnostic
    lookup used by `modules/webhooks` for inbound Telegram) only checks
    `Bot.deleted_at` and never knows the `Organization`'s status - if `Bot`
    were left active, Telegram webhooks would keep being processed (able to
    create new `Destination`/`Subscription`/`UsageEvent` rows) even after
    the org has been deleted.

    Unlike `delete_bot` (a single-bot delete via the dashboard endpoint),
    which does NOT cascade to `BotAssignment` - here it's deliberately
    soft-deleted too, so a deleted tenant is truly fully "dead", not just
    plugged against the webhook gap.
    """
    bots = BotRepository(session)
    assignments = BotAssignmentRepository(session)

    for assignment in await assignments.list_all_active_for_tenant(tenant_id=tenant_id):
        await assignments.soft_delete(assignment)
    for bot in await bots.list_all_active(tenant_id=tenant_id):
        await bots.soft_delete(bot)


async def get_bot_token(session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> str:
    """Used by other modules (e.g. `modules/messaging`) via the public

    service interface (`app.modules.bots`) to call the Telegram API on this
    bot's behalf - no other module is allowed to read `Bot.token` directly.
    """
    bot = await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    return bot.token


async def get_bot_by_api_key(session: AsyncSession, api_key: str) -> Bot:
    """Used by `core.deps.get_bot_from_api_key` - the dual-auth path for the

    external message-send endpoint (per-bot API key via the `X-Bot-Api-Key`
    header). Tenant-agnostic (same pattern as `get_bot_for_webhook` above) -
    the tenant identity is only known from `Bot.tenant_id` once the bot is
    found via its API key's hash fingerprint, not before.
    """
    bot = await BotRepository(session).get_active_by_api_key_hash(_hash_api_key(api_key))
    if bot is None:
        raise BotApiKeyInvalidError()
    return bot


async def get_bot_for_webhook(session: AsyncSession, *, bot_id: uuid.UUID) -> Bot:
    """A tenant-agnostic lookup used ONLY by `modules/webhooks` (inbound

    Telegram webhooks) - that endpoint has no tenant context from a JWT (it
    validates via `X-Telegram-Bot-Api-Secret-Token`, not a Bearer token), so
    the bot is looked up first by `bot_id` from the path, and only then is
    its tenant known from `Bot.tenant_id`. Returns the full `Bot` object
    (not just the token like `get_bot_token`) because the caller needs
    several fields at once (`tenant_id`, `token`, `webhook_secret`,
    `webhook_enabled`) - `Bot` is already part of this module's public
    interface (see `modules/bots/__init__.py`).
    """
    bot = await BotRepository(session).get_active_by_id(bot_id)
    if bot is None:
        raise BotNotFoundError()
    return bot


async def assign_bot(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
) -> BotAssignment:
    """Assigns the MEMBER role's access to a single bot - called by

    owner/admin via `POST /bots/{id}/assignments` (`bot:manage`). `user_id`
    must be a member of this active organization
    (`BotAssignmentUserNotMemberError` otherwise) - `app.modules.organizations`
    is imported at top level (not locally like `auth`<->`organizations`),
    with no cycle here since `organizations` never imports `bots`.

    If an old assignment exists but was unassigned (soft-deleted), that row
    is reactivated (`deleted_at` set back to `None`) instead of inserting a
    new one - the partial unique index in `model.py` requires this so a
    re-assign after an unassign doesn't collide with the index.
    """
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)

    if await get_membership(session, user_id=user_id, organization_id=tenant_id) is None:
        raise BotAssignmentUserNotMemberError()

    assignments = BotAssignmentRepository(session)
    existing = await assignments.get_with_deleted(
        tenant_id=tenant_id, bot_id=bot_id, user_id=user_id
    )
    if existing is not None:
        if existing.deleted_at is None:
            raise BotAssignmentAlreadyExistsError()
        existing.deleted_at = None
        await session.flush()
        return existing

    return await assignments.create(tenant_id=tenant_id, bot_id=bot_id, user_id=user_id)


async def unassign_bot(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    assignments = BotAssignmentRepository(session)
    assignment = await assignments.get_active(tenant_id=tenant_id, bot_id=bot_id, user_id=user_id)
    if assignment is None:
        raise BotAssignmentNotFoundError()
    await assignments.soft_delete(assignment)


async def list_bot_assignments(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, page_params: PageParams
) -> Page[BotAssignment]:
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    return await BotAssignmentRepository(session).list_active_for_bot(
        tenant_id=tenant_id, bot_id=bot_id, params=page_params
    )


async def is_assigned(
    *, session: AsyncSession, tenant_id: uuid.UUID, bot_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Used by `modules/messaging` (via the public interface `app.modules.bots`)

    to scope the MEMBER role's `message:send`/`log:view` to assigned bots.
    Doesn't call `get_bot` first (unlike the other functions above) - the
    caller already knows the bot exists (it was just fetched for another
    purpose), so a single query is enough.
    """
    assignment = await BotAssignmentRepository(session).get_active(
        tenant_id=tenant_id, bot_id=bot_id, user_id=user_id
    )
    return assignment is not None
