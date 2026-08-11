"""Celery tasks for the `messaging` module.

Scope: sending messages, templates, scheduling, and delivery tracking.

- `send_message_to_destination` (queue `messaging.send`): sends one
  `DeliveryLog` to Telegram. Idempotent (checks `status == SENT` before
  sending - here `delivery_log_id` itself is already unique per
  (message, destination) via `uq_delivery_logs_message_id_destination_id`).
  Throttled per-bot & per-chat via Redis (~30 messages/second globally per
  bot, ~1 message/second per chat) BEFORE calling the Telegram API - when
  the bucket is full, a NEW task is `apply_async`'d with a delay (not
  `self.retry` - see the note in `send_message_to_destination` for why the
  two are deliberately kept separate). Telegram API failures
  (`TelegramAPIError`) are retried with exponential backoff up to
  `max_retries`, and only then recorded as `DeliveryLog.status = failed`
  permanently - a permanently failed task must always be recorded, never
  silently dropped.
- `dispatch_scheduled_message` (queue `messaging.scheduled`, Celery beat -
  see `beat_schedule` in `app.core.celery_app`): scans due scheduled
  `Message`s and enqueues `send_message_to_destination` for each of their
  `DeliveryLog`s.

Celery tasks are always sync functions, with their async I/O wrapped in
`asyncio.run()` - each wrapper opens its own `WorkerAsyncSessionFactory()`
(a DB connection separate from the FastAPI request's, since the worker is a
different process).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import redis.asyncio as redis
import structlog

from app.core.celery_app import celery_app
from app.core.database import WorkerAsyncSessionFactory
from app.core.exceptions import AppException
from app.modules.billing import enqueue_event_out
from app.modules.bots import Bot, get_bot
from app.modules.destinations import Destination, get_destination
from app.modules.messaging import service
from app.modules.messaging.model import (
    DeliveryLog,
    DeliveryStatus,
    MediaType,
    Message,
    MessageContentType,
)
from app.modules.messaging.repository import DeliveryLogRepository, MessageRepository
from app.shared.telegram_client import (
    TelegramAPIError,
    send_document,
    send_message,
    send_photo,
    send_poll,
    send_video,
)

logger = structlog.get_logger(__name__)

_MAX_SEND_RETRIES = 5
_BOT_RATE_LIMIT_PER_SECOND = 30
_CHAT_RATE_LIMIT_PER_SECOND = 1
_OUTBOUND_CALLBACK_SIGNATURE_HEADER = "X-Renot-Signature"
_OUTBOUND_CALLBACK_TIMEOUT_SECONDS = 10.0


async def _get_redis() -> redis.Redis:
    """A BRAND NEW Redis connection per call - NOT cached across calls

    (unlike `core.deps.get_redis`, which is a genuine singleton, safe there
    because it's used within a single FastAPI event loop that lives for the
    process's whole lifetime). This one must NOT be a singleton: every
    Celery task execution wraps its async I/O in its own `asyncio.run()`,
    which means every retry runs on a BRAND NEW event loop - a Redis client
    (or DB connection, see `WorkerAsyncSessionFactory`/`core/database.py`)
    created on the first loop and then reused on a second loop blows up with
    `RuntimeError: Event loop is closed` (this actually happened on a retry
    of `send_message_to_destination`). The per-task Redis connect overhead
    is acceptable for this task volume.
    """
    from app.core.config import settings

    return redis.from_url(settings.redis.url, decode_responses=True)


async def _try_consume_window(redis_client: redis.Redis, key: str, limit: int) -> bool:
    """A fixed 1-second window counter (INCR + EXPIRE) - a simpler

    approximation of a token bucket. A full token-bucket/GCRA implementation
    needs a Lua script for precise atomicity; this window counter is close
    enough in practice for the ~30/second per-bot & ~1/second per-chat
    limits and is much easier to test (see `tests/unit/test_messaging_tasks.py`).
    """
    window = int(time.time())
    redis_key = f"{key}:{window}"
    count = await redis_client.incr(redis_key)
    if count == 1:
        await redis_client.expire(redis_key, 2)
    return count <= limit


async def _check_throttle(redis_client: redis.Redis, *, bot_id: str, chat_id: int) -> bool:
    bot_ok = await _try_consume_window(
        redis_client, f"throttle:bot:{bot_id}", _BOT_RATE_LIMIT_PER_SECOND
    )
    chat_ok = await _try_consume_window(
        redis_client, f"throttle:chat:{bot_id}:{chat_id}", _CHAT_RATE_LIMIT_PER_SECOND
    )
    return bot_ok and chat_ok


_MEDIA_FIELD_NAMES = {
    MediaType.PHOTO: "photo",
    MediaType.DOCUMENT: "document",
    MediaType.VIDEO: "video",
}


async def _send_via_telegram(bot_token: str, destination: Destination, message: Message) -> dict:
    """Dispatches to `shared/telegram_client` based on `Message.content_type`

    - lets `TelegramAPIError` propagate as-is on failure, caught by the
    caller (`send_message_to_destination`) which decides retry vs.
    permanent failure.

    Deliberately calls `send_message`/`send_photo`/etc. directly per branch
    (not through a dict capturing function references at import time) so
    those names stay resolvable through the module's globals at call-time -
    good for testability (`unittest.mock.patch("...tasks.send_photo", ...)`
    only takes effect if the lookup happens when it's called, not if it was
    already captured into a module-level variable/dict at import time).
    """
    reply_markup = message.inline_keyboard

    if message.content_type == MessageContentType.TEXT:
        return await send_message(
            bot_token,
            chat_id=destination.chat_id,
            text=message.text or "",
            message_thread_id=destination.thread_id,
            parse_mode=message.parse_mode,
            reply_markup=reply_markup,
        )

    if message.content_type == MessageContentType.MEDIA:
        assert message.media_type is not None and message.media_url is not None
        field_name = _MEDIA_FIELD_NAMES[message.media_type]
        # Typed `Any` - `send_photo`/`send_document`/`send_video` each have a
        # differently-named keyword-only argument (`photo`/`document`/
        # `video`), so mypy can't unify all three into one exact `Callable` signature.
        senders: dict[MediaType, Any] = {
            MediaType.PHOTO: send_photo,
            MediaType.DOCUMENT: send_document,
            MediaType.VIDEO: send_video,
        }
        sender = senders[message.media_type]
        return await sender(
            bot_token,
            chat_id=destination.chat_id,
            **{field_name: message.media_url},
            caption=message.text,
            message_thread_id=destination.thread_id,
            parse_mode=message.parse_mode,
            reply_markup=reply_markup,
        )

    # MessageContentType.POLL
    poll = message.poll or {}
    return await send_poll(
        bot_token,
        chat_id=destination.chat_id,
        question=poll["question"],
        options=poll["options"],
        is_anonymous=poll.get("is_anonymous", True),
        allows_multiple_answers=poll.get("allows_multiple_answers", False),
        message_thread_id=destination.thread_id,
    )


async def _fire_outbound_callback(
    bot: Bot,
    log: DeliveryLog,
    message: Message,
    *,
    status: str,
    telegram_message_id: int | None = None,
    error_reason: str | None = None,
) -> None:
    """Sends the delivery status to `Bot.outbound_callback_url` when set,

    signed with HMAC-SHA256 in the `X-Renot-Signature` header (a header name
    of our own choosing, DIFFERENT from the inbound
    `X-Telegram-Bot-Api-Secret-Token` that Telegram dictates - see the note
    in `modules/webhooks`). The HMAC secret reuses `Bot.webhook_secret`
    (there's no dedicated outbound-callback secret column on `Bot` - this is
    intentional, not a gap to fill later).
    Best-effort: a callback failure is logged as a warning, and does NOT
    raise an exception (the DeliveryLog is already committed regardless;
    the client can still poll `GET /messages/{id}/status`).
    """
    if not bot.outbound_callback_url:
        return

    payload = {
        "message_id": str(message.id),
        "destination_id": str(log.destination_id),
        "status": status,
        "telegram_message_id": telegram_message_id,
        "error_reason": error_reason,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(bot.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=_OUTBOUND_CALLBACK_TIMEOUT_SECONDS) as client:
            await client.post(
                bot.outbound_callback_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    _OUTBOUND_CALLBACK_SIGNATURE_HEADER: signature,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "outbound_callback_failed",
            bot_id=str(bot.id),
            url=bot.outbound_callback_url,
            error=str(exc),
        )


async def _process_delivery(delivery_log_id: str) -> str:
    """Returns `"sent"` / `"skipped"` (idempotent no-op / row missing) /

    `"throttled"`. Lets `TelegramAPIError` propagate on a failed Telegram
    call - DELIBERATELY not caught here (unlike a bot/destination lookup
    failure, which is permanent and immediately marked `failed`), so the
    sync caller (`send_message_to_destination`) can decide
    retry-with-backoff vs. permanent failure based on `self.request.retries`.
    """
    async with WorkerAsyncSessionFactory() as session:
        delivery_repo = DeliveryLogRepository(session)
        log = await delivery_repo.get_by_id(uuid.UUID(delivery_log_id))
        if log is None:
            logger.warning("delivery_log_not_found", delivery_log_id=delivery_log_id)
            return "skipped"
        if log.status == DeliveryStatus.SENT:
            return "skipped"

        message = await MessageRepository(session).get_active(
            tenant_id=log.tenant_id, message_id=log.message_id
        )
        if message is None:
            await delivery_repo.mark_failed(
                log, error_reason="Message not found or has been deleted."
            )
            await session.commit()
            return "failed"

        try:
            bot = await get_bot(session, tenant_id=log.tenant_id, bot_id=message.bot_id)
            destination = await get_destination(
                session, tenant_id=log.tenant_id, destination_id=log.destination_id
            )
        except AppException as exc:
            await delivery_repo.mark_failed(log, error_reason=exc.message)
            await session.commit()
            return "failed"

        redis_client = await _get_redis()
        try:
            throttle_ok = await _check_throttle(
                redis_client, bot_id=str(bot.id), chat_id=destination.chat_id
            )
        finally:
            await redis_client.aclose()
        if not throttle_ok:
            return "throttled"

        result = await _send_via_telegram(bot.token, destination, message)

        telegram_message_id = result.get("message_id")
        await delivery_repo.mark_sent(log, telegram_message_id=telegram_message_id)
        await session.commit()
        # Recorded as a `UsageEvent` event_out - AFTER commit (the
        # router->DB->Celery pattern, see the `enqueue_event_out` docstring
        # in `modules/billing/service.py`).
        enqueue_event_out(
            tenant_id=log.tenant_id,
            bot_id=bot.id,
            destination_id=destination.id,
            message_id=message.id,
            delivery_log_id=log.id,
        )
        await _fire_outbound_callback(
            bot, log, message, status="sent", telegram_message_id=telegram_message_id
        )
        return "sent"


async def _mark_delivery_failed_permanently(delivery_log_id: str, *, error_reason: str) -> None:
    """Called by the sync wrapper once `max_retries` is exhausted (a

    permanent failure) - it must always be recorded to the delivery log
    table with status `failed` and a reason.
    """
    async with WorkerAsyncSessionFactory() as session:
        delivery_repo = DeliveryLogRepository(session)
        log = await delivery_repo.get_by_id(uuid.UUID(delivery_log_id))
        if log is None:
            return
        await delivery_repo.mark_failed(log, error_reason=error_reason)
        await session.commit()

        message = await MessageRepository(session).get_active(
            tenant_id=log.tenant_id, message_id=log.message_id
        )
        if message is None:
            return
        try:
            bot = await get_bot(session, tenant_id=log.tenant_id, bot_id=message.bot_id)
        except AppException:
            return
        await _fire_outbound_callback(bot, log, message, status="failed", error_reason=error_reason)


@celery_app.task(
    name="app.modules.messaging.tasks.send_message_to_destination",
    bind=True,
    max_retries=_MAX_SEND_RETRIES,
)
def send_message_to_destination(self, *, delivery_log_id: str) -> None:  # type: ignore[no-untyped-def]
    try:
        outcome = asyncio.run(_process_delivery(delivery_log_id))
    except TelegramAPIError as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(_mark_delivery_failed_permanently(delivery_log_id, error_reason=str(exc)))
            return
        # Exponential backoff - 1s, 2s, 4s, 8s, 16s.
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc

    if outcome == "throttled":
        # The Redis bucket is full - requeue a NEW task with a short delay
        # rather than failing. Deliberately a fresh `apply_async`, NOT
        # `self.retry` - so it doesn't consume `self.request.retries`,
        # which must purely count actual send failures (see the
        # `TelegramAPIError` branch above), not get mixed up with
        # throttle-induced delays.
        send_message_to_destination.apply_async(
            kwargs={"delivery_log_id": delivery_log_id}, countdown=1
        )


@celery_app.task(name="app.modules.messaging.tasks.dispatch_scheduled_message")
def dispatch_scheduled_message() -> None:
    asyncio.run(_dispatch_scheduled_message_async())


async def _dispatch_scheduled_message_async() -> None:
    async with WorkerAsyncSessionFactory() as session:
        dispatched = await service.dispatch_due_scheduled_messages(session)
        await session.commit()

    for _dispatched_message, logs in dispatched:
        for log in logs:
            send_message_to_destination.delay(delivery_log_id=str(log.id))
