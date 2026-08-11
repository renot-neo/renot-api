"""Pydantic schemas for the `messaging` module.

Scope: sending messages, templates, scheduling, and delivery tracking.

Field-combination validation per `content_type` (`text`/`media`/`poll`) is
done in `MessageCreate` (`model_validator`) - this is pure payload-shape
validation (an I/O boundary concern), not business logic that needs DB
access (e.g. whether a bot/destination actually exists, which stays in
`service.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.messaging.model import (
    DeliveryStatus,
    MediaType,
    MessageContentType,
    OverallStatus,
    ParseMode,
)
from app.shared.telegram_types import InlineKeyboardMarkup, PollInput


class MessageTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    body: str = Field(
        min_length=1, description="Template body, may contain `{{variable_name}}` placeholders."
    )
    parse_mode: ParseMode | None = None


class MessageTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, min_length=1)
    parse_mode: ParseMode | None = None


class MessageTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    body: str
    parse_mode: ParseMode | None
    created_at: datetime


class MessageCreate(BaseModel):
    bot_id: uuid.UUID
    destination_ids: list[uuid.UUID] = Field(
        min_length=1, description="Broadcast to one or many destinations at once."
    )
    content_type: MessageContentType

    # `content_type=text` (required) / `content_type=media` (optional, used as caption)
    text: str | None = Field(default=None, max_length=4096)
    parse_mode: ParseMode | None = None

    # `content_type=media` (both required)
    media_type: MediaType | None = None
    media_url: str | None = Field(default=None, max_length=2048)

    # `content_type=text`/`media` only - see the `send_poll` note in
    # `shared/telegram_client.py` for why polls don't accept this.
    inline_keyboard: InlineKeyboardMarkup | None = None

    # `content_type=poll` (required)
    poll: PollInput | None = None

    # Personalization via `MessageTemplate` - only `content_type=text` can
    # be paired with `template_id` (see the `model.py` docstring). When set,
    # `text` MUST be empty - the rendered template fills `text`, not the user directly.
    template_id: uuid.UUID | None = None
    template_variables: dict[str, str] | None = None

    scheduled_at: datetime | None = Field(
        default=None, description="Leave empty to send immediately. Must be a future timestamp."
    )

    @model_validator(mode="after")
    def _validate_content_fields(self) -> MessageCreate:
        if self.template_id is not None:
            if self.content_type != MessageContentType.TEXT:
                raise ValueError("`template_id` only supports `content_type=text`.")
            if self.text is not None:
                raise ValueError(
                    "`text` must be omitted when `template_id` is set - it is rendered "
                    "from the template."
                )
        elif self.content_type == MessageContentType.TEXT and not self.text:
            raise ValueError("`text` is required when `content_type=text` (or use `template_id`).")

        if self.content_type == MessageContentType.MEDIA:
            if self.media_type is None or not self.media_url:
                raise ValueError(
                    "`media_type` and `media_url` are required when `content_type=media`."
                )
        elif self.media_type is not None or self.media_url is not None:
            raise ValueError("`media_type`/`media_url` are only allowed when `content_type=media`.")

        if self.content_type == MessageContentType.POLL:
            if self.poll is None:
                raise ValueError("`poll` is required when `content_type=poll`.")
            if self.inline_keyboard is not None:
                raise ValueError(
                    "`inline_keyboard` is not supported together with `content_type=poll`."
                )
        elif self.poll is not None:
            raise ValueError("`poll` is only allowed when `content_type=poll`.")

        if len(set(self.destination_ids)) != len(self.destination_ids):
            raise ValueError("`destination_ids` must not contain duplicates.")

        return self


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    bot_id: uuid.UUID
    template_id: uuid.UUID | None
    content_type: MessageContentType
    text: str | None
    parse_mode: ParseMode | None
    media_type: MediaType | None
    media_url: str | None
    inline_keyboard: dict | None
    poll: dict | None
    scheduled_at: datetime | None
    dispatched_at: datetime | None
    created_at: datetime


class DeliveryLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    destination_id: uuid.UUID
    status: DeliveryStatus
    telegram_message_id: int | None
    error_reason: str | None
    sent_at: datetime | None


class DeliveryCounts(BaseModel):
    queued: int
    sent: int
    failed: int


class MessageStatusResponse(BaseModel):
    message_id: uuid.UUID
    scheduled_at: datetime | None
    dispatched_at: datetime | None
    overall_status: OverallStatus
    total_destinations: int
    counts: DeliveryCounts
    deliveries: list[DeliveryLogResponse]
