"""Shared fixtures for `tests/integration/` and `tests/feature/`.

Re-exported (not imported as a conftest itself - see `tests/support/__init__.py`
docstring) by `tests/integration/conftest.py` and `tests/feature/conftest.py`
via `from tests.support.db import *`.

Deliberately does NOT touch `DATABASE__URL`/the app's module-level
`app.core.database.engine`/`worker_engine` at all - the FastAPI app's
`get_db` dependency is overridden per-test (`_override_get_db`) to point at
a session bound to this file's own test engine/container instead, and
Celery `.delay()` calls are mocked at the test call-site (`patch_task_delay`
below) rather than executed for real - actually running a Celery task
inline would need its own `asyncio.run()`/`WorkerAsyncSessionFactory`
plumbing (see the event-loop-per-call pitfalls documented in
`modules/messaging/tasks.py`) which is out of scope for a
router/service-level test; this broadens the same "external calls like the
Telegram API stay mocked" rule to Celery dispatch too, for the same
"don't re-fight already-solved infra problems inside a test" reasoning.
This also sidesteps the "module-level engine reads `settings.database.url`
at import time" ordering problem entirely - nothing here needs that env
var to be correct.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from app.core.deps import get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.auth import service as auth_service
from app.modules.auth.model import User
from app.modules.organizations import service as org_service
from app.modules.organizations.model import Organization, OrganizationRole

_REPO_ROOT = Path(__file__).resolve().parents[2]

__all__ = [
    "postgres_container",
    "test_engine",
    "_db_connection",
    "db_session",
    "_override_get_db",
    "test_organization",
    "test_user_owner",
    "test_user_admin",
    "test_user_member",
    "client",
    "client_as_owner",
    "client_as_admin",
    "client_as_member",
    "patch_task_delay",
    "_isolated_rate_limit_redis",
]


@pytest.fixture(scope="session")
def postgres_container() -> Generator[str, None, None]:
    """Real Postgres via testcontainers - session-scoped,

    one container for the whole `tests/integration`/`tests/feature` run.
    Runs `alembic upgrade head` against it once via subprocess (own process
    -> own fresh `Settings()` reading `DATABASE__URL` from this subprocess's
    env, no import-order issue with the pytest process's own already-imported
    `app.core.config.settings`).
    """
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_REPO_ROOT,
            env={**os.environ, "DATABASE__URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield url


@pytest.fixture(scope="session")
def test_engine(postgres_container: str) -> AsyncEngine:
    """`NullPool` - same reasoning as `app.core.database.worker_engine`:

    each test gets its own event loop via pytest-asyncio's function-scoped
    loop, so no connection may be reused across test functions. The engine
    object itself is safe to construct once at session scope (no I/O
    happens until first checkout).
    """
    return create_async_engine(postgres_container, poolclass=NullPool)


@pytest.fixture
async def _db_connection(test_engine: AsyncEngine) -> AsyncGenerator[AsyncConnection, None]:
    """The one physical connection + outer transaction for a test - never

    committed, only rolled back at teardown (standard SQLAlchemy 2.0
    "joining a session into an external transaction" pattern for test
    isolation). `db_session` and every per-HTTP-request session created by
    `_override_get_db` below all bind to THIS SAME connection (not to each
    other) - see that fixture's docstring for why they're deliberately
    separate `AsyncSession` objects rather than one shared instance.
    """
    async with test_engine.connect() as connection:
        await connection.begin()
        try:
            yield connection
        finally:
            await connection.rollback()


@pytest.fixture
async def db_session(_db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """The session fixture bodies/direct assertions use - joined to

    `_db_connection` via `join_transaction_mode="create_savepoint"` so
    `session.commit()` (routers/services do this per the project's
    router->DB->Celery convention) only ends/restarts a SAVEPOINT, not the
    real outer transaction.
    """
    session = AsyncSession(
        bind=_db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def _override_get_db(_db_connection: AsyncConnection) -> Generator[None, None, None]:
    """Points the FastAPI app's `get_db` dependency at a session bound to

    this test's `_db_connection` - so HTTP requests made through
    `client_as_*` and direct service/repository calls against `db_session`
    in the same test see the same in-progress (never-committed) outer
    transaction, and each other's `commit()`ed writes (a `commit()` under
    `create_savepoint` mode ends/restarts a SAVEPOINT, visible to any other
    session sharing the same underlying connection).

    Deliberately builds a **fresh `AsyncSession` per request** (not a
    single instance reused across the whole test, tried first) so that a
    request which raises before calling `session.commit()` (the "abort the
    whole operation" pattern several services rely on, e.g.
    `bots.service.register_bot` on webhook setup failure) can be rolled
    back exactly like production's per-request
    `async with AsyncSessionFactory() as session` (`AsyncSession.__aexit__`
    auto-rolls-back on unhandled exception) - without leaving its
    already-`flush()`ed rows visible to later requests in the same test.
    Rolling back one *shared* session instead (the first thing tried) also
    expired every ORM object still referenced by test fixtures
    (`test_user_owner`, `test_organization`, ...), raising `MissingGreenlet`
    the next time a test touched `fixture.id` after an unrelated request in
    the same test had failed - per-request sessions avoid that entirely,
    since only the failed request's own (freshly created, fixture-free)
    session gets rolled back/discarded.
    """

    async def _get_db_override() -> AsyncGenerator[AsyncSession, None]:
        session = AsyncSession(
            bind=_db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


def _unique_email(label: str) -> str:
    # `.com`, not `.test` - the latter is a reserved special-use TLD that
    # `email-validator` (backing Pydantic's `EmailStr`, used by request
    # schemas like `MembershipCreate`) rejects outright, and these emails
    # get submitted through real HTTP requests in integration/feature
    # tests, not just used at the service layer (which skips schema
    # validation entirely).
    return f"{label}-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture
async def test_user_owner(db_session: AsyncSession) -> User:
    user = await auth_service.register(
        db_session,
        email=_unique_email("owner"),
        password="TestPassword123!",
        full_name="Test Owner",
    )
    await db_session.commit()
    return user


@pytest.fixture
async def test_organization(db_session: AsyncSession, test_user_owner: User) -> Organization:
    organization = await org_service.create_organization(
        db_session, name="Test Organization", owner_user_id=test_user_owner.id
    )
    await db_session.commit()
    return organization


@pytest.fixture
async def test_user_admin(
    db_session: AsyncSession, test_organization: Organization, test_user_owner: User
) -> User:
    user = await auth_service.register(
        db_session,
        email=_unique_email("admin"),
        password="TestPassword123!",
        full_name="Test Admin",
    )
    await db_session.commit()
    await org_service.add_member(
        db_session,
        organization_id=test_organization.id,
        actor_user_id=test_user_owner.id,
        email=user.email,
        role=OrganizationRole.ADMIN,
    )
    await db_session.commit()
    return user


@pytest.fixture
async def test_user_member(
    db_session: AsyncSession, test_organization: Organization, test_user_owner: User
) -> User:
    user = await auth_service.register(
        db_session,
        email=_unique_email("member"),
        password="TestPassword123!",
        full_name="Test Member",
    )
    await db_session.commit()
    await org_service.add_member(
        db_session,
        organization_id=test_organization.id,
        actor_user_id=test_user_owner.id,
        email=user.email,
        role=OrganizationRole.MEMBER,
    )
    await db_session.commit()
    return user


@pytest.fixture
async def client(_override_get_db: None) -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated httpx client, same overridden `get_db` (and therefore

    same `db_session` transaction) as `client_as_*` - for flows that don't
    have a user/token yet (register/login) or that deliberately test the
    no-token/401 path.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _bearer_client(user_id: uuid.UUID, tenant_id: uuid.UUID) -> AsyncClient:
    token = create_access_token(subject=str(user_id), tenant_id=str(tenant_id))
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def client_as_owner(
    _override_get_db: None, test_organization: Organization, test_user_owner: User
) -> AsyncGenerator[AsyncClient, None]:
    async with _bearer_client(test_user_owner.id, test_organization.id) as client:
        yield client


@pytest.fixture
async def client_as_admin(
    _override_get_db: None, test_organization: Organization, test_user_admin: User
) -> AsyncGenerator[AsyncClient, None]:
    async with _bearer_client(test_user_admin.id, test_organization.id) as client:
        yield client


@pytest.fixture
async def client_as_member(
    _override_get_db: None, test_organization: Organization, test_user_member: User
) -> AsyncGenerator[AsyncClient, None]:
    async with _bearer_client(test_user_member.id, test_organization.id) as client:
        yield client


@pytest.fixture(autouse=True)
def _isolated_rate_limit_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RateLimitMiddleware` (registered globally in `app.main`, runs on

    EVERY request through `client`/`client_as_*`) calls `core.deps.get_redis()`
    - without isolation, its counters live in the REAL dev Redis
    (`REDIS__URL`, the same instance used for actual local dev work) and
    accumulate across every test function in the whole session (all tests
    share one identity under `httpx.ASGITransport` - no real client IP,
    same JWT `sub`/`tenant_id` reused across many tests via the fixture
    factories above), so a handful of tests hitting `/auth/login` etc.
    would trip the real 429 limit and fail unrelated later tests -
    genuinely observed, not hypothetical (auth integration tests started
    429-ing once `RateLimitMiddleware` began actually calling `get_redis`
    for the first time). Same "don't re-fight an already-solved
    external-dependency problem inside a test" reasoning as `respx`-mocking
    Telegram/`patch_task_delay` above (see module docstring) - autouse so
    no test has to remember to opt in, and a FRESH fake per test (not
    shared/module-level) keeps `RateLimitMiddleware`'s own
    classify/window-counter logic genuinely exercised (unlike disabling it
    outright) without cross-test state leakage.
    """

    class _FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, int] = {}

        async def incr(self, key: str) -> int:
            self.store[key] = self.store.get(key, 0) + 1
            return self.store[key]

        async def expire(self, _key: str, _ttl: int) -> bool:
            return True

    fake_redis = _FakeRedis()

    async def _fake_get_redis() -> object:
        return fake_redis

    monkeypatch.setattr("app.core.middleware.rate_limit.get_redis", _fake_get_redis)


@pytest.fixture
def patch_task_delay(monkeypatch: pytest.MonkeyPatch):
    """Returns a helper `patch_task_delay(some_task) -> Mock` that replaces

    a Celery task's `.delay()` with a `Mock()` for the duration of the
    test - see the module docstring for why real task execution is out of
    scope here. The returned `Mock` lets the test assert on call args
    (e.g. `mock.assert_called_once_with(tenant_id=..., bot_id=...)`).
    """

    def _patch(task: object) -> Mock:
        mock = Mock()
        monkeypatch.setattr(task, "delay", mock)
        return mock

    return _patch
