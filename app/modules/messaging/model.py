"""SQLAlchemy models for the `messaging` module.

Scope: sending messages, templates, scheduling, and delivery tracking.

Three entities:
- `MessageTemplate` & `Message`: tenant-scoped (`TenantScopedBase`),
  soft-deleted like every other tenant-scoped entity.
- `DeliveryLog`: **NOT** `TenantScopedBase` (no `deleted_at`) - this is
  high-volume operational data subject to a retention policy (deleted via a
  scheduled job, not a user-facing endpoint), so it's excluded from the
  default soft-delete pattern. `tenant_id` is still present (a manual
  column, not via the mixin) so queries can still be filtered by tenant as
  usual - only the soft-delete part is skipped.

`Message` design notes:
- There's no separate `MessageDestination` pivot table - `DeliveryLog` is
  already "per Message x per Destination", so `DeliveryLog` (created with
  status `queued` for each destination when a `Message` is created, see
  `service.create_message`) ALREADY doubles as the pivot - one row = one
  delivery target.
- `content_type` (`text`/`media`/`poll`) is an explicit column rather than
  inferred from which fields are filled - the fields relevant per type are
  validated in `schema.py` (Pydantic) so invalid combinations (e.g. `media`
  without `media_url`) are rejected before hitting the DB.
- `inline_keyboard` & `poll` are stored as JSONB (structure defined in
  `app.shared.telegram_types.InlineKeyboardMarkup`/`PollInput`) - applicable
  to `content_type` `text`/`media` (inline_keyboard) and `poll` (poll only),
  per `schema.py`.
- `MessageTemplate` only supports `content_type=text` personalization for
  now (`body` is rendered into `Message.text`, see
  `service.render_template_text`) - media/poll templating is deliberately
  deferred to keep scope from expanding.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TenantScopedBase, TimestampMixin, UUIDPrimaryKeyMixin


class ParseMode(enum.StrEnum):
    HTML = "HTML"
    MARKDOWN_V2 = "MarkdownV2"


class MessageContentType(enum.StrEnum):
    TEXT = "text"
    MEDIA = "media"
    POLL = "poll"


class MediaType(enum.StrEnum):
    PHOTO = "photo"
    DOCUMENT = "document"
    VIDEO = "video"


class DeliveryStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


# The aggregate status for `GET /messages/{id}/status`
# (`service.compute_overall_status`) - computed on-the-fly, not a DB column,
# see that function's docstring.
OverallStatus = Literal["scheduled", "queued", "in_progress", "sent", "partially_failed", "failed"]


# A single instance, shared by `MessageTemplate.parse_mode` &
# `Message.parse_mode` so both reference the same Postgres enum type
# (`message_parse_mode`) instead of it being defined twice.
_PARSE_MODE_ENUM = Enum(ParseMode, name="message_parse_mode")


class MessageTemplate(TenantScopedBase):
    __tablename__ = "message_templates"
    __table_args__ = (
        Index("ix_message_templates_tenant_id_created_at", "tenant_id", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The body contains `{{variable_name}}` placeholders - see
    # `service.render_template_text` for the substitution rules.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[ParseMode | None] = mapped_column(_PARSE_MODE_ENUM, nullable=True)


class Message(TenantScopedBase):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_messages_tenant_id_bot_id", "tenant_id", "bot_id"),
        # Used by `dispatch_scheduled_message` (Celery beat, queue
        # `messaging.scheduled`) to scan scheduled messages that are due but
        # not yet dispatched.
        Index("ix_messages_scheduled_at_dispatched_at", "scheduled_at", "dispatched_at"),
    )

    # FK to `bots.id`/`message_templates.id` by table name string (not by
    # importing the model class), so this module doesn't have to import
    # `bots` directly.
    bot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bots.id"), index=True, nullable=False
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("message_templates.id"), nullable=True
    )

    content_type: Mapped[MessageContentType] = mapped_column(
        Enum(MessageContentType, name="message_content_type"), nullable=False
    )
    # Message text (`content_type=text`) or caption (`content_type=media`) -
    # `None` for `content_type=poll`.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_mode: Mapped[ParseMode | None] = mapped_column(_PARSE_MODE_ENUM, nullable=True)

    media_type: Mapped[MediaType | None] = mapped_column(
        Enum(MediaType, name="message_media_type"), nullable=True
    )
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # `InlineKeyboardMarkup`/`PollInput` structure (`shared/telegram_types.py`),
    # `model_dump(exclude_none=True)` - see `service.create_message`.
    inline_keyboard: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    poll: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # `NULL` = not yet dispatched. For a send-immediately message
    # (`scheduled_at` is `None`), dispatch happens synchronously right after
    # creation (see `router.py`/`service.enqueue_immediate_deliveries`) -
    # this column is NOT written for that case (one-shot, no risk of a
    # duplicate dispatch that this column would need to guard against,
    # unlike the scheduled dispatcher which runs repeatedly via polling).
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeliveryLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per `Message` x per `Destination` - hard-delete only, see the

    module-level docstring above.
    """

    __tablename__ = "delivery_logs"
    __table_args__ = (
        UniqueConstraint(
            "message_id", "destination_id", name="uq_delivery_logs_message_id_destination_id"
        ),
        Index("ix_delivery_logs_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_delivery_logs_tenant_id_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id"), index=True, nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), index=True, nullable=False
    )

    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_log_status"),
        nullable=False,
        default=DeliveryStatus.QUEUED,
    )
    # Stored from the MVP onward (not added later) - needed to reference
    # Telegram's message_id for a future reply-to/edit/delete without an
    # extra migration.
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
