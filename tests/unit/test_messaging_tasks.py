"""Unit tests for `app.modules.messaging.tasks`.

Pure logic - the DB (`WorkerAsyncSessionFactory`/repository), Redis,
`shared.telegram_client`, and other modules' service interfaces
(`bots.get_bot`, `destinations.get_destination`) are all mocked. The Celery
task is called directly (not via `.delay()`) - for a `bind=True` task,
calling it directly executes synchronously in the same process with `self`
bound to the task instance itself (so `send_message_to_destination(...)`
is used as-is like a regular function call, not through a real Celery
broker - consistent with never actually hitting a real Celery/Redis/Telegram in tests).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from celery.exceptions import Retry

from app.modules.bots.exceptions import BotNotFoundError
from app.modules.bots.model import Bot
from app.modules.destinations.model import Destination, DestinationType
from app.modules.messaging import tasks
from app.modules.messaging.model import (
    DeliveryLog,
    DeliveryStatus,
    MediaType,
    Message,
    MessageContentType,
)
from app.shared.telegram_client import TelegramAPIError


def _bot(**overrides: object) -> Bot:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "My Bot",
        "telegram_bot_id": 123456789,
        "username": "mybot",
        "token": "123456:dummy-token",
        "webhook_secret": "secret",
        "webhook_enabled": True,
        "api_key_hash": "hash",
        "api_key_prefix": "tgbm_live_abcd",
        "outbound_callback_url": None,
    }
    defaults.update(overrides)
    return Bot(**defaults)  # type: ignore[arg-type]


def _destination(**overrides: object) -> Destination:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "type": DestinationType.PERSONAL,
        "chat_id": 999,
        "thread_id": None,
        "title": "Someone",
    }
    defaults.update(overrides)
    return Destination(**defaults)  # type: ignore[arg-type]


def _message(**overrides: object) -> Message:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "template_id": None,
        "content_type": MessageContentType.TEXT,
        "text": "hello",
        "parse_mode": None,
        "media_type": None,
        "media_url": None,
        "inline_keyboard": None,
        "poll": None,
        "scheduled_at": None,
        "dispatched_at": None,
    }
    defaults.update(overrides)
    return Message(**defaults)  # type: ignore[arg-type]


def _delivery_log(**overrides: object) -> DeliveryLog:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "destination_id": uuid.uuid4(),
        "status": DeliveryStatus.QUEUED,
        "telegram_message_id": None,
        "error_reason": None,
        "sent_at": None,
    }
    defaults.update(overrides)
    return DeliveryLog(**defaults)  # type: ignore[arg-type]


class _FakeAsyncSessionCM:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _session_factory(session: object) -> object:
    return lambda: _FakeAsyncSessionCM(session)


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


# --- throttle (Redis window counter) ---


@pytest.mark.asyncio
async def test_try_consume_window_allows_up_to_limit() -> None:
    redis_client = _FakeRedis()
    for _ in range(3):
        assert await tasks._try_consume_window(redis_client, "k", 3) is True


@pytest.mark.asyncio
async def test_try_consume_window_rejects_beyond_limit() -> None:
    redis_client = _FakeRedis()
    for _ in range(3):
        await tasks._try_consume_window(redis_client, "k", 3)

    assert await tasks._try_consume_window(redis_client, "k", 3) is False


@pytest.mark.asyncio
async def test_check_throttle_false_when_chat_limit_exceeded() -> None:
    redis_client = _FakeRedis()
    assert await tasks._check_throttle(redis_client, bot_id="bot-1", chat_id=1) is True
    # `_CHAT_RATE_LIMIT_PER_SECOND` = 1 - a second attempt to the same chat
    # within the same second's window must be rejected even if the bot's
    # own quota still has room.
    assert await tasks._check_throttle(redis_client, bot_id="bot-1", chat_id=1) is False


# --- _send_via_telegram dispatch ---


@pytest.mark.asyncio
async def test_send_via_telegram_dispatches_text() -> None:
    message = _message(content_type=MessageContentType.TEXT, text="hi", parse_mode=None)
    destination = _destination(chat_id=42, thread_id=7)
    with patch(
        "app.modules.messaging.tasks.send_message", AsyncMock(return_value={"message_id": 1})
    ) as send:
        result = await tasks._send_via_telegram("token", destination, message)

        assert result == {"message_id": 1}
        send.assert_awaited_once_with(
            "token", chat_id=42, text="hi", message_thread_id=7, parse_mode=None, reply_markup=None
        )


@pytest.mark.asyncio
async def test_send_via_telegram_dispatches_media_photo() -> None:
    message = _message(
        content_type=MessageContentType.MEDIA,
        media_type=MediaType.PHOTO,
        media_url="https://example.com/pic.jpg",
        text="caption",
    )
    destination = _destination()
    with patch(
        "app.modules.messaging.tasks.send_photo", AsyncMock(return_value={"message_id": 2})
    ) as send:
        result = await tasks._send_via_telegram("token", destination, message)

        assert result == {"message_id": 2}
        assert send.await_args.kwargs["photo"] == "https://example.com/pic.jpg"
        assert send.await_args.kwargs["caption"] == "caption"


@pytest.mark.asyncio
async def test_send_via_telegram_dispatches_poll() -> None:
    message = _message(
        content_type=MessageContentType.POLL,
        text=None,
        poll={
            "question": "Q?",
            "options": ["A", "B"],
            "is_anonymous": True,
            "allows_multiple_answers": False,
        },
    )
    destination = _destination()
    with patch(
        "app.modules.messaging.tasks.send_poll", AsyncMock(return_value={"message_id": 3})
    ) as send:
        result = await tasks._send_via_telegram("token", destination, message)

        assert result == {"message_id": 3}
        assert send.await_args.kwargs["question"] == "Q?"
        assert send.await_args.kwargs["options"] == ["A", "B"]


# --- _process_delivery ---


@pytest.mark.asyncio
async def test_process_delivery_skips_when_log_not_found() -> None:
    session = _mock_session()
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
    ):
        log_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

        outcome = await tasks._process_delivery(str(uuid.uuid4()))

        assert outcome == "skipped"


@pytest.mark.asyncio
async def test_process_delivery_skips_when_already_sent_idempotent() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.SENT)
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
    ):
        log_repo_cls.return_value.get_by_id = AsyncMock(return_value=log)

        outcome = await tasks._process_delivery(str(log.id))

        assert outcome == "skipped"


@pytest.mark.asyncio
async def test_process_delivery_marks_failed_when_message_missing() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
    ):
        log_repo = log_repo_cls.return_value
        log_repo.get_by_id = AsyncMock(return_value=log)
        log_repo.mark_failed = AsyncMock()
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        outcome = await tasks._process_delivery(str(log.id))

        assert outcome == "failed"
        log_repo.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_delivery_marks_failed_when_bot_lookup_raises_app_exception() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    message = _message(id=log.message_id)
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.tasks.get_bot", AsyncMock(side_effect=BotNotFoundError())),
    ):
        log_repo = log_repo_cls.return_value
        log_repo.get_by_id = AsyncMock(return_value=log)
        log_repo.mark_failed = AsyncMock()
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=message)

        outcome = await tasks._process_delivery(str(log.id))

        assert outcome == "failed"
        log_repo.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_delivery_returns_throttled_when_bucket_full() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    message = _message(id=log.message_id)
    bot = _bot()
    destination = _destination()
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.tasks.get_bot", AsyncMock(return_value=bot)),
        patch("app.modules.messaging.tasks.get_destination", AsyncMock(return_value=destination)),
        patch("app.modules.messaging.tasks._get_redis", AsyncMock(return_value=AsyncMock())),
        patch("app.modules.messaging.tasks._check_throttle", AsyncMock(return_value=False)),
    ):
        log_repo_cls.return_value.get_by_id = AsyncMock(return_value=log)
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=message)

        outcome = await tasks._process_delivery(str(log.id))

        assert outcome == "throttled"


@pytest.mark.asyncio
async def test_process_delivery_marks_sent_and_fires_callback_on_success() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    message = _message(id=log.message_id)
    bot = _bot(outbound_callback_url="https://example.com/callback")
    destination = _destination()
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.tasks.get_bot", AsyncMock(return_value=bot)),
        patch("app.modules.messaging.tasks.get_destination", AsyncMock(return_value=destination)),
        patch("app.modules.messaging.tasks._get_redis", AsyncMock(return_value=AsyncMock())),
        patch("app.modules.messaging.tasks._check_throttle", AsyncMock(return_value=True)),
        patch(
            "app.modules.messaging.tasks._send_via_telegram",
            AsyncMock(return_value={"message_id": 555}),
        ),
        patch("app.modules.messaging.tasks._fire_outbound_callback", AsyncMock()) as callback,
        patch("app.modules.messaging.tasks.enqueue_event_out") as enqueue_event_out,
    ):
        log_repo = log_repo_cls.return_value
        log_repo.get_by_id = AsyncMock(return_value=log)
        log_repo.mark_sent = AsyncMock()
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=message)

        outcome = await tasks._process_delivery(str(log.id))

        assert outcome == "sent"
        log_repo.mark_sent.assert_awaited_once_with(log, telegram_message_id=555)
        callback.assert_awaited_once()
        assert callback.await_args.kwargs["status"] == "sent"
        # An event_out is enqueued AFTER `mark_sent` succeeds - see
        # `modules/billing/service.py`.
        enqueue_event_out.assert_called_once_with(
            tenant_id=log.tenant_id,
            bot_id=bot.id,
            destination_id=destination.id,
            message_id=message.id,
            delivery_log_id=log.id,
        )


@pytest.mark.asyncio
async def test_process_delivery_propagates_telegram_api_error_uncaught() -> None:
    """A Telegram call failure is NOT caught in `_process_delivery` - it's

    left to the sync caller (`send_message_to_destination`) to decide retry
    vs. permanent failure (see that function's docstring).
    """
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    message = _message(id=log.message_id)
    bot = _bot()
    destination = _destination()
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.tasks.get_bot", AsyncMock(return_value=bot)),
        patch("app.modules.messaging.tasks.get_destination", AsyncMock(return_value=destination)),
        patch("app.modules.messaging.tasks._get_redis", AsyncMock(return_value=AsyncMock())),
        patch("app.modules.messaging.tasks._check_throttle", AsyncMock(return_value=True)),
        patch(
            "app.modules.messaging.tasks._send_via_telegram",
            AsyncMock(side_effect=TelegramAPIError("boom")),
        ),
    ):
        log_repo_cls.return_value.get_by_id = AsyncMock(return_value=log)
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=message)

        with pytest.raises(TelegramAPIError):
            await tasks._process_delivery(str(log.id))


# --- _fire_outbound_callback ---


@pytest.mark.asyncio
async def test_fire_outbound_callback_noop_when_no_url() -> None:
    bot = _bot(outbound_callback_url=None)
    log = _delivery_log()
    message = _message()
    # No respx route registered - if it tried to POST it would raise.
    await tasks._fire_outbound_callback(bot, log, message, status="sent")


@pytest.mark.asyncio
@respx.mock
async def test_fire_outbound_callback_sends_signed_request() -> None:
    bot = _bot(outbound_callback_url="https://example.com/callback")
    log = _delivery_log(sent_at=datetime.now(UTC))
    message = _message(id=log.message_id)
    route = respx.post("https://example.com/callback").mock(return_value=httpx.Response(200))

    await tasks._fire_outbound_callback(bot, log, message, status="sent", telegram_message_id=99)

    assert route.called
    sent_request = route.calls.last.request
    assert tasks._OUTBOUND_CALLBACK_SIGNATURE_HEADER in sent_request.headers


@pytest.mark.asyncio
@respx.mock
async def test_fire_outbound_callback_swallows_http_error() -> None:
    bot = _bot(outbound_callback_url="https://example.com/callback")
    log = _delivery_log()
    message = _message(id=log.message_id)
    respx.post("https://example.com/callback").mock(side_effect=httpx.ConnectError("boom"))

    # Should not raise.
    await tasks._fire_outbound_callback(bot, log, message, status="failed", error_reason="boom")


# --- send_message_to_destination (bound Celery task) ---


def test_send_message_to_destination_success_does_not_retry() -> None:
    with patch("app.modules.messaging.tasks._process_delivery", AsyncMock(return_value="sent")):
        tasks.send_message_to_destination(delivery_log_id=str(uuid.uuid4()))


def test_send_message_to_destination_requeues_new_task_when_throttled() -> None:
    delivery_log_id = str(uuid.uuid4())
    with (
        patch("app.modules.messaging.tasks._process_delivery", AsyncMock(return_value="throttled")),
        patch.object(tasks.send_message_to_destination, "apply_async") as apply_async,
    ):
        tasks.send_message_to_destination(delivery_log_id=delivery_log_id)

        apply_async.assert_called_once_with(
            kwargs={"delivery_log_id": delivery_log_id}, countdown=1
        )


def test_send_message_to_destination_retries_with_backoff_on_telegram_error() -> None:
    """Calling the bound task directly (not via `.apply_async`) makes Celery's

    `request.called_directly` true, in which case `self.retry()` just
    re-raises the underlying exception instead of going through the real
    retry-scheduling path (a Celery quirk when there's no actual broker
    request context) - so `self.retry` itself is mocked here to assert the
    exponential-backoff `countdown` it's called with, rather than relying on
    `self.retry()`'s real (context-dependent) raise behavior.
    """
    exc = TelegramAPIError("boom")
    with (
        patch("app.modules.messaging.tasks._process_delivery", AsyncMock(side_effect=exc)),
        patch.object(
            tasks.send_message_to_destination, "retry", side_effect=Retry("mocked")
        ) as retry,
    ):
        with pytest.raises(Retry):
            tasks.send_message_to_destination(delivery_log_id=str(uuid.uuid4()))

        assert retry.call_args.kwargs["exc"] is exc
        assert retry.call_args.kwargs["countdown"] == 1  # 2**0 (self.request.retries == 0)


def test_send_message_to_destination_marks_failed_permanently_after_max_retries() -> None:
    original_max_retries = tasks.send_message_to_destination.max_retries
    tasks.send_message_to_destination.max_retries = 0
    try:
        with (
            patch(
                "app.modules.messaging.tasks._process_delivery",
                AsyncMock(side_effect=TelegramAPIError("boom")),
            ),
            patch(
                "app.modules.messaging.tasks._mark_delivery_failed_permanently", AsyncMock()
            ) as mark_failed,
        ):
            tasks.send_message_to_destination(delivery_log_id=str(uuid.uuid4()))

            mark_failed.assert_awaited_once()
    finally:
        tasks.send_message_to_destination.max_retries = original_max_retries


# --- _mark_delivery_failed_permanently ---


@pytest.mark.asyncio
async def test_mark_delivery_failed_permanently_updates_log_and_fires_callback() -> None:
    session = _mock_session()
    log = _delivery_log(status=DeliveryStatus.QUEUED)
    message = _message(id=log.message_id)
    bot = _bot(outbound_callback_url="https://example.com/callback")
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
        patch("app.modules.messaging.tasks.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.tasks.get_bot", AsyncMock(return_value=bot)),
        patch("app.modules.messaging.tasks._fire_outbound_callback", AsyncMock()) as callback,
    ):
        log_repo = log_repo_cls.return_value
        log_repo.get_by_id = AsyncMock(return_value=log)
        log_repo.mark_failed = AsyncMock()
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=message)

        await tasks._mark_delivery_failed_permanently(str(log.id), error_reason="gave up")

        log_repo.mark_failed.assert_awaited_once_with(log, error_reason="gave up")
        callback.assert_awaited_once()
        assert callback.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_mark_delivery_failed_permanently_noop_when_log_missing() -> None:
    session = _mock_session()
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.messaging.tasks.DeliveryLogRepository") as log_repo_cls,
    ):
        log_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

        # Should not raise.
        await tasks._mark_delivery_failed_permanently(str(uuid.uuid4()), error_reason="x")


# --- dispatch_scheduled_message / _dispatch_scheduled_message_async ---


@pytest.mark.asyncio
async def test_dispatch_scheduled_message_async_commits_before_enqueueing() -> None:
    session = _mock_session()
    message = _message(scheduled_at=datetime.now(UTC) - timedelta(minutes=1))
    log = _delivery_log(message_id=message.id)
    with (
        patch("app.modules.messaging.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch(
            "app.modules.messaging.service.dispatch_due_scheduled_messages",
            AsyncMock(return_value=[(message, [log])]),
        ),
        patch("app.modules.messaging.tasks.send_message_to_destination") as task,
    ):
        await tasks._dispatch_scheduled_message_async()

        session.commit.assert_awaited_once()
        task.delay.assert_called_once_with(delivery_log_id=str(log.id))


def test_dispatch_scheduled_message_task_runs_without_error() -> None:
    with patch(
        "app.modules.messaging.tasks._dispatch_scheduled_message_async", AsyncMock()
    ) as inner:
        tasks.dispatch_scheduled_message()
        inner.assert_awaited_once()
