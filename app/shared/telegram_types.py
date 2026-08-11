"""Centralized custom Pydantic types/validators for Telegram-specific

formats - `chat_id`, `parse_mode`, the `inline_keyboard` structure, etc.
Used across modules (`messaging`, `bots`, `webhooks`).

A subset of Telegram's inbound Update (`TelegramChat`/`TelegramMessage`/
`TelegramUpdate`) was added for `modules/webhooks` - fields are limited to
what the built-in command handlers actually use (`/start`, `/stop`,
`/status`, `/help`), NOT a full representation of Telegram's Update schema
(which has dozens of optional fields: `edited_message`, `callback_query`,
`my_chat_member`, etc. - out of scope for this MVP).
`model_config = ConfigDict(extra="ignore")` so a field that isn't modeled
yet doesn't fail request validation.

The outbound types (used by `modules/messaging`) are further down this
file: `ChatId`, `ParseMode`, `InlineKeyboardButton`/`InlineKeyboardMarkup`
(url & switch_inline_query only - WITHOUT callback_data, deferred to later),
`PollInput`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TelegramChat(BaseModel):
    """A subset of Telegram's `Chat` object - see https://core.telegram.org/bots/api#chat."""

    model_config = ConfigDict(extra="ignore")

    id: int
    type: Literal["private", "group", "supergroup", "channel"]
    title: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class TelegramMessage(BaseModel):
    """A subset of Telegram's `Message` object - see https://core.telegram.org/bots/api#message.

    `message_thread_id` is set by Telegram only for messages inside a forum
    group's topic (our `thread_id`).
    """

    model_config = ConfigDict(extra="ignore")

    message_id: int
    date: int
    chat: TelegramChat
    message_thread_id: int | None = None
    text: str | None = None


class TelegramUpdate(BaseModel):
    """A subset of Telegram's `Update` object - see https://core.telegram.org/bots/api#update.

    Only `message` is processed by `modules/webhooks` for this MVP phase
    (built-in commands come through a private/group chat, not a channel
    post) - `channel_post` and other update types are accepted (the
    request isn't rejected) but ignored at the service level: nothing is
    forwarded anywhere except for the core commands.
    """

    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None


# --- Outbound types (used by modules/messaging) ---

# Telegram's `sendMessage` etc. accept `chat_id` as either an Integer or a
# String (`@username`, for public channels/supergroups only) - see
# https://core.telegram.org/bots/api#sendmessage. `modules/destinations`
# itself always stores `chat_id` as numeric (`/status` replies with a
# numeric chat_id), so in practice only `int` is ever used in the
# `modules/messaging` flow, but this type alias stays `int | str` to match
# Telegram's full contract, for future use outside of a registered
# Destination (e.g. broadcasting directly by `@username`).
ChatId = int | str

ParseMode = Literal["HTML", "MarkdownV2"]


class InlineKeyboardButton(BaseModel):
    """One inline keyboard button - this MVP only supports `url` &

    `switch_inline_query` (WITHOUT `callback_data`, deferred to later since
    it needs a separate event routing/handler design).
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=64)
    url: str | None = Field(default=None, max_length=2048)
    switch_inline_query: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> InlineKeyboardButton:
        if bool(self.url) == bool(self.switch_inline_query):
            raise ValueError("Exactly one of `url` or `switch_inline_query` must be set.")
        return self


class InlineKeyboardMarkup(BaseModel):
    """A subset of Telegram's `InlineKeyboardMarkup` - see

    https://core.telegram.org/bots/api#inlinekeyboardmarkup. Sent as-is
    (`model_dump(exclude_none=True)`) as `reply_markup` in
    `shared/telegram_client.py`.
    """

    model_config = ConfigDict(extra="forbid")

    inline_keyboard: list[list[InlineKeyboardButton]] = Field(min_length=1)


class PollInput(BaseModel):
    """A subset of Telegram's `sendPoll` payload - see

    https://core.telegram.org/bots/api#sendpoll. The `question`/`options`
    limits follow Telegram's own (question <=300 chars, 2-10 options, each
    option <=100 chars) so an invalid request is rejected by Pydantic (422)
    before ever being sent to Telegram and failing there.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=2, max_length=10)
    is_anonymous: bool = True
    allows_multiple_answers: bool = False

    @model_validator(mode="after")
    def _validate_options(self) -> PollInput:
        if any(not option or len(option) > 100 for option in self.options):
            raise ValueError("Each poll option must be 1-100 characters.")
        return self
