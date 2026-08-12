"""Unit tests for `app.shared.telegram_types`'s two custom validators -

`InlineKeyboardButton._exactly_one_target` and `PollInput._validate_options`.
Both are only ever exercised indirectly today (via `tests/unit/test_messaging_schema.py`
nesting these types inside `MessageCreate`), and only their happy paths at
that - this covers the validators' own error branches directly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.shared.telegram_types import InlineKeyboardButton, PollInput


def test_inline_keyboard_button_raises_when_neither_url_nor_switch_inline_query_set() -> None:
    with pytest.raises(ValidationError, match="Exactly one of"):
        InlineKeyboardButton(text="Open")


def test_inline_keyboard_button_raises_when_both_url_and_switch_inline_query_set() -> None:
    with pytest.raises(ValidationError, match="Exactly one of"):
        InlineKeyboardButton(text="Open", url="https://example.com", switch_inline_query="q")


def test_inline_keyboard_button_accepts_url_only() -> None:
    button = InlineKeyboardButton(text="Open", url="https://example.com")
    assert button.url == "https://example.com"


def test_poll_input_raises_when_an_option_is_too_long() -> None:
    with pytest.raises(ValidationError, match="1-100 characters"):
        PollInput(question="Q?", options=["fine", "x" * 101])


def test_poll_input_raises_when_an_option_is_empty() -> None:
    with pytest.raises(ValidationError, match="1-100 characters"):
        PollInput(question="Q?", options=["fine", ""])


def test_poll_input_accepts_valid_options() -> None:
    poll = PollInput(question="Q?", options=["Yes", "No"])
    assert poll.options == ["Yes", "No"]
