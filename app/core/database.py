"""Async engine/session factory + base model.

- `Base`: base model for non-tenant entities (e.g. `users`, `plans`).
- `TenantScopedBase`: inherited by every tenant-scoped model - has `id`
  (UUID v7), `tenant_id`, `created_at`, `updated_at`, `deleted_at` (soft-delete).
- The individual mixins (`UUIDPrimaryKeyMixin`, `TimestampMixin`,
  `SoftDeleteMixin`) are exposed separately for cases like `Organization`:
  it's a tenant root itself (no `tenant_id` pointing to itself) but still
  needs soft-delete.

The default query that excludes soft-deleted rows is implemented at each
module's repository layer (`active()` / `with_deleted()` methods), not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool
from uuid_utils import uuid7

from app.core.config import settings

# Explicit naming convention so constraint/index names stay consistent and
# predictable in Alembic migration files (makes autogenerate & diff review easier).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def generate_uuid7() -> uuid.UUID:
    """Generate a time-ordered UUID v7 in the application layer."""
    return uuid.UUID(bytes=uuid7().bytes)


engine: AsyncEngine = create_async_engine(
    settings.database.url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)

# Separate engine dedicated to the Celery worker (`modules/*/tasks.py`) - must
# NOT reuse the `engine`/`AsyncSessionFactory` above. Each Celery task wraps
# its async I/O in its own `asyncio.run()` call, which means every task
# execution runs on a BRAND NEW event loop. If the same `engine` (with its
# default connection pool, `AsyncAdaptedQueuePool`) were reused across calls,
# a pooled asyncpg connection from a previous (now-closed) loop could get
# handed to the new loop and blow up with `RuntimeError: Future attached to
# a different loop` - this actually happened on a retry of
# `send_message_to_destination` (second loop, connection left over from the
# first attempt). `NullPool` means no connection reuse across
# checkout/loop at all - every checkout opens a fresh connection and fully
# closes it on release, which is safe across `asyncio.run()` calls, at the
# cost of a per-task connect overhead (acceptable for Celery task volume,
# unlike FastAPI HTTP requests which are far more frequent and need a real pool).
worker_engine: AsyncEngine = create_async_engine(settings.database.url, poolclass=NullPool)

WorkerAsyncSessionFactory = async_sessionmaker(worker_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base model for non-tenant entities (e.g. `users`, `plans`)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """`id` as UUID v7, generated in the application layer."""

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )


class TimestampMixin:
    """`created_at` / `updated_at`, always UTC (`timestamptz`)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """`deleted_at`, nullable - excluded by default at the repository layer."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class TenantScopedBase(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Base model for every tenant-scoped entity.

    The FK to `organizations.id` is referenced by table name string (not by
    importing the `organizations` model class), so that `core` doesn't have
    to import another module's internals - SQLAlchemy resolves the FK lazily
    when the mapper is configured, not at Python import time.
    """

    __abstract__ = True

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id"),
        index=True,
        nullable=False,
    )
