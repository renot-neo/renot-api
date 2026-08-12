"""Unit tests for `app.modules.webhooks.service`.

Pure logic - the `app.modules.bots`/`app.modules.destinations` service
interfaces and `app.shared.telegram_client.send_message` are mocked, no
real DB/network. Every external call to the Telegram Bot API must be mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import encrypt_secret
from app.modules.bots.exceptions import BotNotFoundError
from app.modules.bots.model import Bot
from app.modules.destinations.model import (
    BotDestinationSubscription,
    DestinationType,
    SubscriptionStatus,
)
from app.modules.webhooks import service
from app.modules.webhooks.exceptions import WebhookSecretInvalidError
from app.shared.telegram_client import TelegramAPIError
from app.shared.telegram_types import TelegramChat, TelegramMessage, TelegramUpdate

# The plaintext behind `_bot()`'s default `webhook_secret_encrypted` - tests
# reference this constant directly (never `bot.webhook_secret`, which no
# longer exists now that the column only holds ciphertext) when they need
# to pass the REAL secret as the inbound `X-Telegram-Bot-Api-Secret-Token`.
_WEBHOOK_SECRET = "s3cr3t"


def _bot(**overrides: object) -> Bot:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "My Bot",
        "telegram_bot_id": 123456789,
        "username": "mybot",
        "token_encrypted": encrypt_secret("123456:dummy-token"),
        "token_last_four": "oken",
        "webhook_secret_encrypted": encrypt_secret(_WEBHOOK_SECRET),
        "webhook_enabled": True,
        "api_key_hash": "hash",
        "api_key_prefix": "tgbm_live_abcd",
        "outbound_callback_url": None,
    }
    defaults.update(overrides)
    return Bot(**defaults)  # type: ignore[arg-type]


def _subscription(**overrides: object) -> BotDestinationSubscription:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "destination_id": uuid.uuid4(),
        "status": SubscriptionStatus.ACTIVE,
    }
    defaults.update(overrides)
    return BotDestinationSubscription(**defaults)  # type: ignore[arg-type]


def _update(
    text: str | None,
    *,
    chat_type: str = "private",
    chat_id: int = 111,
    message_thread_id: int | None = None,
    title: str | None = None,
    username: str | None = "someuser",
    has_message: bool = True,
) -> TelegramUpdate:
    if not has_message:
        return TelegramUpdate(update_id=1, message=None)
    chat = TelegramChat(
        id=chat_id, type=chat_type, title=title, username=username, first_name="John"
    )
    message = TelegramMessage(
        message_id=1, date=1690000000, chat=chat, message_thread_id=message_thread_id, text=text
    )
    return TelegramUpdate(update_id=1, message=message)


@pytest.mark.asyncio
async def test_handle_telegram_update_propagates_bot_not_found() -> None:
    with patch(
        "app.modules.webhooks.service.get_bot_for_webhook",
        AsyncMock(side_effect=BotNotFoundError()),
    ):
        with pytest.raises(BotNotFoundError):
            await service.handle_telegram_update(
                AsyncMock(),
                bot_id=uuid.uuid4(),
                secret_token=_WEBHOOK_SECRET,
                update=_update("/start"),
            )


@pytest.mark.asyncio
async def test_handle_telegram_update_raises_when_secret_missing() -> None:
    bot = _bot()
    with patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)):
        with pytest.raises(WebhookSecretInvalidError):
            await service.handle_telegram_update(
                AsyncMock(), bot_id=bot.id, secret_token=None, update=_update("/start")
            )


@pytest.mark.asyncio
async def test_handle_telegram_update_raises_when_secret_mismatched() -> None:
    bot = _bot(webhook_secret_encrypted=encrypt_secret("expected"))
    with patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)):
        with pytest.raises(WebhookSecretInvalidError):
            await service.handle_telegram_update(
                AsyncMock(), bot_id=bot.id, secret_token="wrong", update=_update("/start")
            )


@pytest.mark.asyncio
async def test_handle_telegram_update_ignores_update_without_message() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.subscribe_via_start", AsyncMock()) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        result = await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update(None, has_message=False),
        )

        subscribe.assert_not_called()
        send.assert_not_called()
        # An event_in is recorded for EVERY update that passes secret
        # validation, not just recognized commands - `router.py` enqueues
        # it via this return value after commit.
        assert result == bot.tenant_id


@pytest.mark.asyncio
async def test_handle_telegram_update_ignores_non_command_text() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        result = await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update("hello there"),
        )

        send.assert_not_called()
        assert result == bot.tenant_id


@pytest.mark.asyncio
async def test_handle_telegram_update_ignores_channel_chat() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update("/start", chat_type="channel", title="My Channel", username=None),
        )

        send.assert_not_called()


@pytest.mark.asyncio
async def test_start_command_subscribes_when_webhook_enabled() -> None:
    bot = _bot(webhook_enabled=True)
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/start")
        )

        subscribe.assert_awaited_once()
        assert subscribe.await_args.kwargs["tenant_id"] == bot.tenant_id
        assert subscribe.await_args.kwargs["bot_id"] == bot.id
        assert subscribe.await_args.kwargs["chat_id"] == 111
        send.assert_awaited_once()
        assert "subscribed" in send.await_args.kwargs["text"].lower()
        assert send.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_start_command_with_bot_username_suffix_and_args() -> None:
    bot = _bot(webhook_enabled=True)
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()),
    ):
        await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update("/start@mybot some-arg"),
        )

        subscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_command_replies_closed_when_webhook_disabled() -> None:
    bot = _bot(webhook_enabled=False)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.subscribe_via_start", AsyncMock()) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        result = await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/start")
        )

        subscribe.assert_not_called()
        send.assert_awaited_once()
        assert "closed" in send.await_args.kwargs["text"].lower()
        # A closed registration still counts as an event_in - Telegram still
        # sends its update to the webhook, it just doesn't end up subscribing.
        assert result == bot.tenant_id


@pytest.mark.asyncio
async def test_start_command_replies_blocked_when_subscription_blocked_by_admin() -> None:
    bot = _bot(webhook_enabled=True)
    subscription = _subscription(status=SubscriptionStatus.BLOCKED_BY_ADMIN)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/start")
        )

        assert "blocked" in send.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_start_command_resolves_group_thread_context() -> None:
    bot = _bot(webhook_enabled=True)
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()),
    ):
        await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update(
                "/start", chat_type="supergroup", chat_id=-100999, message_thread_id=5, title="Grp"
            ),
        )

        assert subscribe.await_args.kwargs["thread_id"] == 5
        assert subscribe.await_args.kwargs["chat_id"] == -100999


@pytest.mark.asyncio
async def test_start_command_resolves_plain_group_without_thread() -> None:
    """A regular (non-supergroup, non-forum) group has no

    `message_thread_id` at all - distinct from the group+thread case above,
    which takes the `GROUP_THREAD` branch instead.
    """
    bot = _bot(webhook_enabled=True)
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ) as subscribe,
        patch("app.modules.webhooks.service.send_message", AsyncMock()),
    ):
        await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update("/start", chat_type="group", chat_id=-100888, title="Plain Grp"),
        )

        assert subscribe.await_args.kwargs["type"] == DestinationType.GROUP
        assert subscribe.await_args.kwargs["thread_id"] is None
        assert subscribe.await_args.kwargs["chat_id"] == -100888


@pytest.mark.asyncio
async def test_start_command_escapes_bot_name_with_html_special_chars() -> None:
    bot = _bot(webhook_enabled=True, name="A & B <Bot>")
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.subscribe_via_start",
            AsyncMock(return_value=(object(), subscription)),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/start")
        )

        text = send.await_args.kwargs["text"]
        assert "A &amp; B &lt;Bot&gt;" in text
        assert "<Bot>" not in text


@pytest.mark.asyncio
async def test_stop_command_replies_unsubscribed_when_found() -> None:
    bot = _bot()
    subscription = _subscription(status=SubscriptionStatus.UNSUBSCRIBED)
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.unsubscribe_via_stop",
            AsyncMock(return_value=subscription),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/stop")
        )

        assert "unsubscribed" in send.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_stop_command_replies_not_subscribed_when_none() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.unsubscribe_via_stop", AsyncMock(return_value=None)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/stop")
        )

        assert "weren't subscribed" in send.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_status_command_replies_with_chat_and_thread_id() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.get_subscription_status",
            AsyncMock(return_value=SubscriptionStatus.ACTIVE),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(),
            bot_id=bot.id,
            secret_token=_WEBHOOK_SECRET,
            update=_update(
                "/status", chat_type="supergroup", chat_id=-100999, message_thread_id=5, title="Grp"
            ),
        )

        text = send.await_args.kwargs["text"]
        assert "Chat ID: <code>-100999</code>" in text
        assert "Thread ID: <code>5</code>" in text
        assert send.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_status_command_omits_thread_id_line_when_none() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.get_subscription_status",
            AsyncMock(return_value=SubscriptionStatus.ACTIVE),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/status")
        )

        text = send.await_args.kwargs["text"]
        assert "Chat ID: <code>111</code>" in text
        assert "Thread ID:" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_snippet"),
    [
        (SubscriptionStatus.ACTIVE, "actively subscribed"),
        (SubscriptionStatus.UNSUBSCRIBED, "not currently subscribed"),
        (SubscriptionStatus.BLOCKED_BY_ADMIN, "blocked"),
        (None, "haven't subscribed"),
    ],
)
async def test_status_command_header_reflects_subscription_state(
    status: SubscriptionStatus | None, expected_snippet: str
) -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.get_subscription_status",
            AsyncMock(return_value=status),
        ),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/status")
        )

        assert expected_snippet in send.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_help_command_lists_commands_and_help_url() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/help")
        )

        text = send.await_args.kwargs["text"]
        assert "http" in text
        assert "/start" in text
        assert "/stop" in text
        assert "/status" in text
        assert "/about" in text


@pytest.mark.asyncio
async def test_about_command_replies_with_name_and_username() -> None:
    bot = _bot(name="My Bot", username="mybot")
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/about")
        )

        text = send.await_args.kwargs["text"]
        assert "My Bot" in text
        assert "@mybot" in text
        assert "Renot" in text
        assert send.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_about_command_escapes_html_special_chars() -> None:
    bot = _bot(name="A & B <Bot>", username="my_bot")
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch("app.modules.webhooks.service.send_message", AsyncMock()) as send,
    ):
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/about")
        )

        text = send.await_args.kwargs["text"]
        assert "A &amp; B &lt;Bot&gt;" in text
        assert "<Bot>" not in text


@pytest.mark.asyncio
async def test_reply_send_failure_does_not_raise() -> None:
    bot = _bot()
    with (
        patch("app.modules.webhooks.service.get_bot_for_webhook", AsyncMock(return_value=bot)),
        patch(
            "app.modules.webhooks.service.send_message",
            AsyncMock(side_effect=TelegramAPIError("bot was blocked")),
        ),
    ):
        # Must not raise - a failed reply doesn't fail webhook processing
        # (the DB state is already committed regardless).
        await service.handle_telegram_update(
            AsyncMock(), bot_id=bot.id, secret_token=_WEBHOOK_SECRET, update=_update("/help")
        )
