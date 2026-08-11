"""Alembic environment - async engine, target_metadata from `app.core.database.Base`.

`sqlalchemy.url` is read from `app.core.config.settings.database.url`
(not from `alembic.ini`) so it stays consistent with whichever environment
the app is actually running in.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.core.database import Base

# Import each module's model here so Alembic autogenerate can see it (a
# pure side-effect import that registers its metadata onto Base.metadata).
from app.modules.auth import model as auth_model  # noqa: F401
from app.modules.billing import model as billing_model  # noqa: F401
from app.modules.bots import model as bots_model  # noqa: F401
from app.modules.destinations import model as destinations_model  # noqa: F401
from app.modules.messaging import model as messaging_model  # noqa: F401
from app.modules.organizations import model as organizations_model  # noqa: F401

# TODO: uncomment once this module has a concrete model (`modules/webhooks`
# deliberately has no `model.py` - see the docstring there).
# from app.modules.webhooks import model as webhooks_model  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database.url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
