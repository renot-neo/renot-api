"""Unit tests for `app.shared.telegram_client`.

`httpx` is mocked via `respx` - no test ever actually hits `api.telegram.org`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.shared.telegram_client import (
    TelegramAPIError,
    get_me,
    send_document,
    send_message,
    send_photo,
    send_poll,
    send_video,
    set_webhook,
)

TOKEN = "123456:dummy-token"


@pytest.mark.asyncio
@respx.mock
async def test_get_me_returns_result_on_success() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": 123, "username": "mybot"}}
        )
    )

    result = await get_me(TOKEN)

    assert result == {"id": 123, "username": "mybot"}


@pytest.mark.asyncio
@respx.mock
async def test_get_me_raises_telegram_api_error_when_not_ok() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )
    )

    with pytest.raises(TelegramAPIError) as exc_info:
        await get_me(TOKEN)

    assert exc_info.value.error_code == 401
    assert exc_info.value.description == "Unauthorized"


@pytest.mark.asyncio
@respx.mock
async def test_get_me_raises_telegram_api_error_on_network_failure() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getMe").mock(
        side_effect=httpx.ConnectError("boom")
    )

    with pytest.raises(TelegramAPIError):
        await get_me(TOKEN)


@pytest.mark.asyncio
@respx.mock
async def test_set_webhook_returns_true_on_success() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )

    result = await set_webhook(TOKEN, url="https://example.com/hook", secret_token="s3cr3t")

    assert result is True
    sent_body = route.calls.last.request.content
    assert b"example.com/hook" in sent_body
    assert b"s3cr3t" in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_message_returns_result_on_success() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )

    result = await send_message(TOKEN, chat_id=123, text="hello")

    assert result == {"message_id": 42}
    sent_body = route.calls.last.request.content
    assert b'"chat_id":123' in sent_body
    assert b'"text":"hello"' in sent_body
    assert b"message_thread_id" not in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_message_includes_message_thread_id_when_given() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 43}})
    )

    await send_message(TOKEN, chat_id=123, text="hello", message_thread_id=7)

    sent_body = route.calls.last.request.content
    assert b'"message_thread_id":7' in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_message_raises_telegram_api_error_when_not_ok() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(
            403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}
        )
    )

    with pytest.raises(TelegramAPIError) as exc_info:
        await send_message(TOKEN, chat_id=123, text="hello")

    assert exc_info.value.error_code == 403


@pytest.mark.asyncio
@respx.mock
async def test_send_message_includes_parse_mode_and_reply_markup_when_given() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 44}})
    )
    reply_markup = {"inline_keyboard": [[{"text": "Open", "url": "https://example.com"}]]}

    await send_message(
        TOKEN, chat_id=123, text="hello", parse_mode="HTML", reply_markup=reply_markup
    )

    sent_body = route.calls.last.request.content
    assert b'"parse_mode":"HTML"' in sent_body
    assert b'"reply_markup"' in sent_body
    assert b"example.com" in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_photo_sends_photo_field_and_caption() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 45}})
    )

    result = await send_photo(
        TOKEN, chat_id=123, photo="https://example.com/pic.jpg", caption="Look at this"
    )

    assert result == {"message_id": 45}
    sent_body = route.calls.last.request.content
    assert b'"photo":"https://example.com/pic.jpg"' in sent_body
    assert b'"caption":"Look at this"' in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_document_sends_document_field() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 46}})
    )

    await send_document(TOKEN, chat_id=123, document="https://example.com/file.pdf")

    sent_body = route.calls.last.request.content
    assert b'"document":"https://example.com/file.pdf"' in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_video_sends_video_field() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendVideo").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 47}})
    )

    await send_video(TOKEN, chat_id=123, video="https://example.com/clip.mp4")

    sent_body = route.calls.last.request.content
    assert b'"video":"https://example.com/clip.mp4"' in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_poll_returns_result_on_success() -> None:
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendPoll").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 48}})
    )

    result = await send_poll(
        TOKEN, chat_id=123, question="Pineapple on pizza?", options=["Yes", "No"]
    )

    assert result == {"message_id": 48}
    sent_body = route.calls.last.request.content
    assert b'"question":"Pineapple on pizza?"' in sent_body
    assert b'"options":["Yes","No"]' in sent_body
    assert b'"is_anonymous":true' in sent_body
    assert b'"allows_multiple_answers":false' in sent_body


@pytest.mark.asyncio
@respx.mock
async def test_send_poll_raises_telegram_api_error_when_not_ok() -> None:
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendPoll").mock(
        return_value=httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "Bad Request: not enough options"},
        )
    )

    with pytest.raises(TelegramAPIError):
        await send_poll(TOKEN, chat_id=123, question="Q?", options=["A"])
