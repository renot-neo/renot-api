"""A thin async HTTP client for the Telegram Bot API.

A shared utility used across modules (`modules/bots`, `modules/messaging`,
`modules/webhooks`) - purely an async HTTP wrapper (`httpx.AsyncClient`),
with no state/DB, consistent with what `app/shared/` is for.

The methods here started out limited to what `modules/bots` needed (token
validation + webhook setup) and `modules/webhooks` (a plain `send_message`
for replying to built-in commands). `send_message` was later extended
(`parse_mode`/`reply_markup`, backward-compatible - new parameters always
default to `None`) and `send_photo`/`send_document`/`send_video`/
`send_poll` were added for `modules/messaging`
(text/media/markdown-HTML/inline keyboard/poll).

A Telegram API error (`ok: false`) is raised as a plain `TelegramAPIError`
(not an `AppException` subclass) so this module stays unaware of our
application's response envelope - the caller (e.g.
`modules/bots/service.py`) translates it into the appropriate `AppException`.
"""

from __future__ import annotations

from typing import Any

import httpx

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
_REQUEST_TIMEOUT_SECONDS = 10.0


class TelegramAPIError(Exception):
    """Raised when the Telegram API replies `ok: false`, or when the request
    fails outright (network/timeout) - `error_code` is `None` for the latter case.
    """

    def __init__(self, description: str, *, error_code: int | None = None) -> None:
        self.description = description
        self.error_code = error_code
        super().__init__(description)


async def _call(token: str, method: str, *, json: dict[str, Any] | None = None) -> Any:
    url = f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, json=json or {})
        except httpx.HTTPError as exc:
            raise TelegramAPIError(f"Failed to reach Telegram API: {exc}") from exc

    payload = response.json()
    if not payload.get("ok"):
        raise TelegramAPIError(
            payload.get("description", "Unknown Telegram API error"),
            error_code=payload.get("error_code"),
        )
    return payload["result"]


async def get_me(token: str) -> dict[str, Any]:
    """Validates the token via `getMe` - used during bot registration.
    Returns the raw `result` from Telegram (`id`, `username`, `first_name`, etc.).
    """
    result: dict[str, Any] = await _call(token, "getMe")
    return result


async def set_webhook(token: str, *, url: str, secret_token: str) -> bool:
    """Automatically `setWebhook`s to the platform's URL with a unique
    secret token - called during bot registration.
    """
    result: bool = await _call(token, "setWebhook", json={"url": url, "secret_token": secret_token})
    return result


async def send_message(
    token: str,
    *,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sends a text message - used by `modules/webhooks` (built-in command

    replies, plain without `parse_mode`/`reply_markup`) and
    `modules/messaging` (a text-type `Message`, with optional
    `parse_mode`/`reply_markup`). `reply_markup` is already a Telegram-shape
    dict (`InlineKeyboardMarkup.model_dump(exclude_none=True)` from
    `shared/telegram_types.py`), not built here.
    """
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result: dict[str, Any] = await _call(token, "sendMessage", json=payload)
    return result


async def _send_media(
    token: str,
    method: str,
    media_field: str,
    *,
    chat_id: int,
    media: str,
    caption: str | None,
    message_thread_id: int | None,
    parse_mode: str | None,
    reply_markup: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shared helper for `send_photo`/`send_document`/`send_video` - their

    Telegram payloads are identical except for the method name and the
    media field name (`photo`/`document`/`video`). `media` is always sent
    as a URL/`file_id` string (not a multipart upload) - enough for this
    MVP's scope (photo/document/video from a URL); raw file upload from the
    user isn't needed yet.
    """
    payload: dict[str, Any] = {"chat_id": chat_id, media_field: media}
    if caption is not None:
        payload["caption"] = caption
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result: dict[str, Any] = await _call(token, method, json=payload)
    return result


async def send_photo(
    token: str,
    *,
    chat_id: int,
    photo: str,
    caption: str | None = None,
    message_thread_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _send_media(
        token,
        "sendPhoto",
        "photo",
        chat_id=chat_id,
        media=photo,
        caption=caption,
        message_thread_id=message_thread_id,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def send_document(
    token: str,
    *,
    chat_id: int,
    document: str,
    caption: str | None = None,
    message_thread_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _send_media(
        token,
        "sendDocument",
        "document",
        chat_id=chat_id,
        media=document,
        caption=caption,
        message_thread_id=message_thread_id,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def send_video(
    token: str,
    *,
    chat_id: int,
    video: str,
    caption: str | None = None,
    message_thread_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _send_media(
        token,
        "sendVideo",
        "video",
        chat_id=chat_id,
        media=video,
        caption=caption,
        message_thread_id=message_thread_id,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def send_poll(
    token: str,
    *,
    chat_id: int,
    question: str,
    options: list[str],
    is_anonymous: bool = True,
    allows_multiple_answers: bool = False,
    message_thread_id: int | None = None,
) -> dict[str, Any]:
    """`reply_markup` is deliberately not accepted here - combining a poll

    with an inline keyboard is out of scope for this MVP (the two are
    separate features; no flow needs them combined).
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "question": question,
        "options": options,
        "is_anonymous": is_anonymous,
        "allows_multiple_answers": allows_multiple_answers,
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    result: dict[str, Any] = await _call(token, "sendPoll", json=payload)
    return result
