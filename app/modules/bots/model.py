"""SQLAlchemy models for the `bots` module.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

`Bot` is a tenant-scoped entity (inherits `TenantScopedBase`) - soft-deleted
like every other tenant-scoped entity.

Sensitive fields:
- `token`: the BotFather token, stored **as-is (plaintext)** - a deliberate
  decision so it can be used directly to call the Telegram Bot API (getMe,
  setWebhook, and later sendMessage) without a decryption step. Unlike
  `api_key_hash`/passwords, which are never read back in plaintext.
  `token_last_four` (used to mask the token for display, e.g. `...ab12`) is
  a Python property derived from `token`, not a separate column.
- `api_key_hash`: the per-bot API key for inbound REST calls from external
  apps (via the `X-Bot-Api-Key` header) - a SHA-256 fingerprint (not a slow
  hash like argon2), following the same pattern as `RefreshToken.token_hash`
  in `modules/auth` (a high-entropy secret from `secrets.token_urlsafe`,
  needing a deterministic lookup by value - a different case from a human
  password, which needs a salted slow-hash). `api_key_prefix` is stored
  plaintext for masked display (format `tgbm_live_xxxx`).
- `webhook_secret`: validated against the
  `X-Telegram-Bot-Api-Secret-Token` header on inbound webhooks.

`BotAssignment` (the Bot<->User pivot backing "MEMBER role can only access
assigned bots") also lives in this module, not in `organizations` - a bot is
the resource whose access is being controlled, so assignment sits alongside
`bot:manage`/toggling `webhook_enabled`/regenerating the key (unlike
`BotDestinationSubscription`, which lives in `modules/destinations` instead,
since there the subscription is genuinely part of the destination's own
lifecycle). Tenant-scoped + soft-delete (`TenantScopedBase`), same as
`BotDestinationSubscription` - unassigning sets `deleted_at`, and
re-assigning after an unassign reactivates the old row (see
`service.py::assign_bot`) instead of creating a new one, so assignment
history isn't lost.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import TenantScopedBase


class Bot(TenantScopedBase):
    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint("api_key_hash", name="uq_bots_api_key_hash"),
        # Partial unique index (not a plain `UniqueConstraint`) - a Telegram
        # token belonging to a soft-deleted bot doesn't block re-registering
        # a bot with the same token.
        Index(
            "uq_bots_telegram_bot_id_active",
            "telegram_bot_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Composite index on the columns that are actually queried together
        # (listing bots per-org, sorted by created_at).
        Index("ix_bots_tenant_id_created_at", "tenant_id", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)

    token: Mapped[str] = mapped_column(Text, nullable=False)

    webhook_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)

    outbound_callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    @property
    def token_last_four(self) -> str:
        """Derived from `token` - used by `BotResponse` to mask it for display,

        so the API never returns the full token on a regular response
        (GET/list), even though the token itself is stored plaintext.
        """
        return self.token[-4:] if self.token else ""


class BotAssignment(TenantScopedBase):
    __tablename__ = "bot_assignments"
    __table_args__ = (
        # Partial unique index (not a plain `UniqueConstraint`) - same reason
        # as `Bot.telegram_bot_id`: an unassigned (soft-deleted) assignment
        # shouldn't block re-assigning the same (bot, user) pair.
        Index(
            "uq_bot_assignments_bot_id_user_id_active",
            "bot_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_bot_assignments_tenant_id_bot_id", "tenant_id", "bot_id"),
    )

    # FK to `bots.id` by table name string, not a cross-module model import
    # - same pattern as `BotDestinationSubscription`.
    bot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bots.id"), index=True, nullable=False
    )
    # FK to `users.id` (modules/auth) by table name string, same reason.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
