"""SQLAlchemy models for the `destinations` module.

Scope: message destinations (chats/groups/channels) and their bot subscriptions.

Both entities are tenant-scoped (inherit `TenantScopedBase`) - soft-deleted
like every other tenant-scoped entity.

`Destination` is **global per-org**, related to `Bot` through the
`BotDestinationSubscription` pivot table - a single destination (e.g. one
group) can, in theory, be subscribed to more than one bot within the same org.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import TenantScopedBase


class DestinationType(enum.StrEnum):
    """The type of message recipient target."""

    PERSONAL = "personal"
    GROUP = "group"
    GROUP_THREAD = "group_thread"
    CHANNEL = "channel"


class SubscriptionStatus(enum.StrEnum):
    """The status of a Bot <-> Destination relationship."""

    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"
    BLOCKED_BY_ADMIN = "blocked_by_admin"


class Destination(TenantScopedBase):
    __tablename__ = "destinations"
    __table_args__ = (
        # Partial unique index, split into two (not a single plain
        # constraint) because Postgres treats every NULL as distinct from
        # every other NULL in a unique constraint - without this split,
        # multiple `thread_id IS NULL` destinations with the same `chat_id`
        # would still be allowed to insert.
        Index(
            "uq_destinations_chat_active",
            "tenant_id",
            "chat_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND thread_id IS NULL"),
        ),
        Index(
            "uq_destinations_chat_thread_active",
            "tenant_id",
            "chat_id",
            "thread_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND thread_id IS NOT NULL"),
        ),
        Index("ix_destinations_tenant_id_created_at", "tenant_id", "created_at"),
    )

    type: Mapped[DestinationType] = mapped_column(
        Enum(DestinationType, name="destination_type"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Set only when the target is a specific topic within a forum group.
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class BotDestinationSubscription(TenantScopedBase):
    __tablename__ = "bot_destination_subscriptions"
    __table_args__ = (
        # One subscription row per (bot, destination) pair - the status
        # changes in-place (active/unsubscribed/blocked_by_admin) rather
        # than being recreated, so a plain unique constraint (no partial
        # index needed) is enough (see `modules/destinations/service.py`).
        UniqueConstraint(
            "bot_id",
            "destination_id",
            name="uq_bot_destination_subscriptions_bot_id_destination_id",
        ),
        Index("ix_bot_destination_subscriptions_tenant_id_bot_id", "tenant_id", "bot_id"),
    )

    # FK to `bots.id` by table name string (not by importing the `bots`
    # model), so `destinations` doesn't have to import another module's
    # internals.
    bot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bots.id"), index=True, nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), index=True, nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="bot_destination_subscription_status"),
        nullable=False,
        default=SubscriptionStatus.ACTIVE,
    )
