"""Business logic for the `webhooks` module.

Scope: processing inbound Telegram webhooks, including the `/start`/`/stop`
side of the subscribe flow that Telegram triggers (the manual dashboard
path already lives in `modules/destinations`).

This is the layer the router calls into - the only place business logic
belongs.

`handle_telegram_update` flow:
1. Look up `Bot` via `bot_id` in the path (tenant-agnostic, see
   `app.modules.bots.get_bot_for_webhook`) - raises `BotNotFoundError` if
   it doesn't exist/is soft-deleted.
2. Validate `secret_token` (the `X-Telegram-Bot-Api-Secret-Token` header)
   against `Bot.webhook_secret` - raises `WebhookSecretInvalidError` on a mismatch.
3. If the update isn't a text `message`, or isn't a recognized command
   (`/start`, `/stop`, `/status`, `/help`), or its chat is a `channel`
   (channel posts have no user command flow - channels are registered
   manually via the dashboard instead), it's simply ignored - nothing is
   forwarded anywhere except for the core commands.
4. Processes the command via its handler, replying via `send_message`.

The Telegram reply (`send_message`) is wrapped in `_reply`, which swallows
`TelegramAPIError` (logs a warning, doesn't raise) - a failure to SEND THE
REPLY must not fail the whole request (the DB subscription change is
already committed regardless), and this also keeps Telegram from
retry-storming this endpoint over an error that's actually outside our
control (the Telegram API's network).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.bots import Bot, get_bot_for_webhook, reveal_token, reveal_webhook_secret
from app.modules.destinations import (
    DestinationType,
    SubscriptionStatus,
    subscribe_via_start,
    unsubscribe_via_stop,
)
from app.modules.webhooks.exceptions import WebhookSecretInvalidError
from app.shared.telegram_client import TelegramAPIError, send_message
from app.shared.telegram_types import TelegramMessage, TelegramUpdate

logger = structlog.get_logger(__name__)

_KNOWN_COMMANDS = {"start", "stop", "status", "help"}

_HELP_TEXT_TEMPLATE = "Need help? See the setup guide: {help_url}"
_REGISTRATION_CLOSED_TEXT = "Registration is currently closed for this bot."
_SUBSCRIBED_TEXT = "You're subscribed! You'll now receive messages from this bot."
_BLOCKED_TEXT = "You have been blocked from this bot by an administrator."
_UNSUBSCRIBED_TEXT = "You've been unsubscribed. Send /start to subscribe again."
_NOT_SUBSCRIBED_TEXT = "You weren't subscribed to this bot."


async def handle_telegram_update(
    session: AsyncSession,
    *,
    bot_id: uuid.UUID,
    secret_token: str | None,
    update: TelegramUpdate,
) -> uuid.UUID:
    """Returns `bot.tenant_id` - used by `router.py` for

    `billing.enqueue_event_in` AFTER `session.commit()` (the
    router->DB->Celery pattern). Every update from Telegram is recorded as
    a UsageEvent (event_in) - so event_in is enqueued for EVERY update that
    passes the secret token validation below, regardless of whether that
    update ends up as a recognized command (`/start` etc.) or is silently
    ignored (non-text, unrecognized command, `webhook_enabled=false`, etc.)
    - the router does the enqueueing, not this function, to keep the same
    single point of commit->enqueue responsibility as other modules.
    """
    bot = await get_bot_for_webhook(session, bot_id=bot_id)
    if not secret_token or secret_token != reveal_webhook_secret(bot):
        raise WebhookSecretInvalidError()

    message = update.message
    if message is None or message.text is None:
        return bot.tenant_id

    command = _extract_command(message.text)
    if command is None:
        return bot.tenant_id

    context = _resolve_destination_context(message)
    if context is None:
        return bot.tenant_id
    destination_type, chat_id, thread_id, title = context

    if command == "start":
        await _handle_start(
            session,
            bot=bot,
            type=destination_type,
            chat_id=chat_id,
            thread_id=thread_id,
            title=title,
        )
    elif command == "stop":
        await _handle_stop(session, bot=bot, chat_id=chat_id, thread_id=thread_id)
    elif command == "status":
        await _handle_status(bot=bot, chat_id=chat_id, thread_id=thread_id)
    elif command == "help":
        await _handle_help(bot=bot, chat_id=chat_id, thread_id=thread_id)

    return bot.tenant_id


async def _handle_start(
    session: AsyncSession,
    *,
    bot: Bot,
    type: DestinationType,
    chat_id: int,
    thread_id: int | None,
    title: str,
) -> None:
    """Policy open -> auto-create + subscribe; policy closed -> reject,

    creating nothing.
    """
    if not bot.webhook_enabled:
        await _reply(bot, chat_id=chat_id, thread_id=thread_id, text=_REGISTRATION_CLOSED_TEXT)
        return

    _, subscription = await subscribe_via_start(
        session,
        tenant_id=bot.tenant_id,
        bot_id=bot.id,
        type=type,
        chat_id=chat_id,
        thread_id=thread_id,
        title=title,
    )
    if subscription.status == SubscriptionStatus.BLOCKED_BY_ADMIN:
        await _reply(bot, chat_id=chat_id, thread_id=thread_id, text=_BLOCKED_TEXT)
        return
    await _reply(bot, chat_id=chat_id, thread_id=thread_id, text=_SUBSCRIBED_TEXT)


async def _handle_stop(
    session: AsyncSession, *, bot: Bot, chat_id: int, thread_id: int | None
) -> None:
    """Self-service unsubscribe."""
    subscription = await unsubscribe_via_stop(
        session, tenant_id=bot.tenant_id, bot_id=bot.id, chat_id=chat_id, thread_id=thread_id
    )
    text = _NOT_SUBSCRIBED_TEXT if subscription is None else _UNSUBSCRIBED_TEXT
    await _reply(bot, chat_id=chat_id, thread_id=thread_id, text=text)


async def _handle_status(*, bot: Bot, chat_id: int, thread_id: int | None) -> None:
    """`/status` shows chat_id/thread_id to make setup easier - mainly used

    by a user to copy a group/channel's `chat_id` for manual registration
    via the dashboard.
    """
    lines = [f"chat_id: {chat_id}"]
    if thread_id is not None:
        lines.append(f"thread_id: {thread_id}")
    await _reply(bot, chat_id=chat_id, thread_id=thread_id, text="\n".join(lines))


async def _handle_help(*, bot: Bot, chat_id: int, thread_id: int | None) -> None:
    text = _HELP_TEXT_TEMPLATE.format(help_url=settings.telegram.help_url)
    await _reply(bot, chat_id=chat_id, thread_id=thread_id, text=text)


async def _reply(bot: Bot, *, chat_id: int, thread_id: int | None, text: str) -> None:
    try:
        await send_message(
            reveal_token(bot), chat_id=chat_id, text=text, message_thread_id=thread_id
        )
    except TelegramAPIError as exc:
        # A failed reply must not fail webhook processing (the DB state is
        # already committed regardless) - just log it.
        logger.warning(
            "webhook_reply_send_failed", bot_id=str(bot.id), chat_id=chat_id, error=str(exc)
        )


def _extract_command(text: str) -> str | None:
    """Parses `/start`, `/stop@mybot`, etc. - ignores any arguments after

    the command & the `@botusername` suffix Telegram adds in groups (when
    multiple bots share the same group).
    """
    if not text.startswith("/"):
        return None
    first_token = text.split(maxsplit=1)[0]
    command = first_token[1:].split("@", 1)[0].lower()
    return command if command in _KNOWN_COMMANDS else None


def _resolve_destination_context(
    message: TelegramMessage,
) -> tuple[DestinationType, int, int | None, str] | None:
    """Derives `(DestinationType, chat_id, thread_id, title)` from the

    Telegram message's chat. `channel` deliberately returns `None` - a
    command comes from `message`, not `channel_post`, so channels are out
    of scope for this flow (channels are registered manually via the
    dashboard, not via `/start` - see `modules/destinations`).
    """
    chat = message.chat
    if chat.type == "private":
        title = (
            chat.username
            or " ".join(filter(None, [chat.first_name, chat.last_name]))
            or str(chat.id)
        )
        return DestinationType.PERSONAL, chat.id, None, title
    if chat.type in ("group", "supergroup"):
        title = chat.title or str(chat.id)
        if message.message_thread_id is not None:
            return DestinationType.GROUP_THREAD, chat.id, message.message_thread_id, title
        return DestinationType.GROUP, chat.id, None, title
    return None
