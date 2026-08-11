"""SQLAlchemy models for the `bots` module.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

`Bot` is a tenant-scoped entity (inherits `TenantScopedBase`) - soft-deleted
like every other tenant-scoped entity.

Sensitive fields:
- `token_encrypted`: the BotFather token, Fernet-encrypted at rest (see
  `core.security.encrypt_secret`/`decrypt_secret` and
  `private/specs/2026-08-12-bot-secret-encryption-design.md`). Read back via
  `service.get_bot_token` - no other module reads this column directly.
  **History**: an earlier draft of this module encrypted `token` and the
  user explicitly rejected it (plaintext, "as-is", was the deliberate
  choice at the time); that decision was reversed 2026-08-12 once the repo
  went public, per the design doc above - if this comes up again, the
  plaintext choice was NOT an oversight either time, both were deliberate
  calls for the context at the time.
  `token_last_four` (masks the token for display, e.g. `...ab12`) is a
  real stored column, populated once at create time from the plaintext
  before it's encrypted - the last 4 characters aren't sensitive on their
  own (can't reconstruct the real token from them) and can't be derived
  from ciphertext, so this is no longer a derived property.
- `api_key_hash`: the per-bot API key for inbound REST calls from external
  apps (via the `X-Bot-Api-Key` header) - a SHA-256 fingerprint (not a slow
  hash like argon2), following the same pattern as `RefreshToken.token_hash`
  in `modules/auth` (a high-entropy secret from `secrets.token_urlsafe`,
  needing a deterministic lookup by value - a different case from a human
  password, which needs a salted slow-hash). `api_key_prefix` is stored
  plaintext for masked display (format `tgbm_live_xxxx`). Deliberately
  NOT reversibly encrypted like `token_encrypted` above - this is a
  one-way lookup key, never read back, so a fingerprint is the right tool,
  not encryption.
- `webhook_secret_encrypted`: Fernet-encrypted, same reasoning as
  `token_encrypted`. Validated against the
  `X-Telegram-Bot-Api-Secret-Token` header on inbound webhooks (decrypt,
  then compare - not a fingerprint, since it also doubles as the outbound
  HMAC callback signing key and HMAC needs the real key bytes). Read back
  via `service.get_bot_webhook_secret`.

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

    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_last_four: Mapped[str] = mapped_column(String(4), nullable=False)

    webhook_secret_encrypted: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)

    outbound_callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)


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
