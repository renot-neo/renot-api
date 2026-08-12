"""Unit tests for `MessageCreate.model_validator`'s validation.

Focuses on field combinations per `content_type` (`text`/`media`/`poll`) -
see the `schema.py` docstring for why this validation belongs at the
Pydantic layer, not `service.py`.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.messaging.model import MediaType, MessageContentType
from app.modules.messaging.schema import MessageCreate
from app.shared.telegram_types import PollInput

_BOT_ID = uuid.uuid4()
_DEST_ID = uuid.uuid4()


def test_text_message_valid() -> None:
    message = MessageCreate(
        bot_id=_BOT_ID, destination_ids=[_DEST_ID], content_type=MessageContentType.TEXT, text="hi"
    )
    assert message.text == "hi"


def test_text_message_requires_text() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID, destination_ids=[_DEST_ID], content_type=MessageContentType.TEXT
        )


def test_media_message_valid() -> None:
    message = MessageCreate(
        bot_id=_BOT_ID,
        destination_ids=[_DEST_ID],
        content_type=MessageContentType.MEDIA,
        media_type=MediaType.PHOTO,
        media_url="https://example.com/pic.jpg",
    )
    assert message.media_url == "https://example.com/pic.jpg"


def test_media_message_requires_media_type_and_url() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.MEDIA,
            media_type=MediaType.PHOTO,
        )


def test_media_fields_rejected_outside_media_content_type() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.TEXT,
            text="hi",
            media_url="https://example.com/pic.jpg",
        )


def test_poll_message_valid() -> None:
    message = MessageCreate(
        bot_id=_BOT_ID,
        destination_ids=[_DEST_ID],
        content_type=MessageContentType.POLL,
        poll=PollInput(question="Q?", options=["A", "B"]),
    )
    assert message.poll is not None


def test_poll_requires_poll_field() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID, destination_ids=[_DEST_ID], content_type=MessageContentType.POLL
        )


def test_poll_rejects_inline_keyboard() -> None:
    from app.shared.telegram_types import InlineKeyboardButton, InlineKeyboardMarkup

    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.POLL,
            poll=PollInput(question="Q?", options=["A", "B"]),
            inline_keyboard=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://example.com")]]
            ),
        )


def test_poll_field_forbidden_when_content_type_is_not_poll() -> None:
    with pytest.raises(ValidationError, match="only allowed when `content_type=poll`"):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.TEXT,
            text="hi",
            poll=PollInput(question="Q?", options=["A", "B"]),
        )


def test_template_id_forbids_explicit_text() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.TEXT,
            text="hi",
            template_id=uuid.uuid4(),
        )


def test_template_id_only_supports_text_content_type() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID],
            content_type=MessageContentType.POLL,
            poll=PollInput(question="Q?", options=["A", "B"]),
            template_id=uuid.uuid4(),
        )


def test_duplicate_destination_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(
            bot_id=_BOT_ID,
            destination_ids=[_DEST_ID, _DEST_ID],
            content_type=MessageContentType.TEXT,
            text="hi",
        )
