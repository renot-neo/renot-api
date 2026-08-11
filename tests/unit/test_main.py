"""Unit tests for `app.main.create_app` - doc-UI environment gating, the

branded-favicon override, and webhook route schema visibility. No DB/network
needed (matches the `tests/unit` tier's scope) - the one real HTTP dependency
(`RateLimitMiddleware`'s Redis call) is faked locally, see
`_isolated_rate_limit_redis` below.
"""

from __future__ import annotations

import tomllib

import httpx
import pytest

from app.core.config import settings
from app.main import _PYPROJECT_PATH, _read_app_version, create_app


@pytest.fixture(autouse=True)
def _isolated_rate_limit_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RateLimitMiddleware` runs on every request, including the new /docs,

    /redoc, /static, /favicon.ico routes this file exercises over real HTTP
    - without this, it calls the real `core.deps.get_redis()`, which needs
    an actual Redis instance this docker-free `tests/unit` tier deliberately
    doesn't have (see the module docstring). Same fake-counter pattern as
    `tests/support/db.py::_isolated_rate_limit_redis`, duplicated locally
    rather than imported from there - `tests/support` pulls in
    `testcontainers`/Postgres-container machinery at module import time,
    which would defeat this tier's docker-free-import guarantee just to
    reuse one small fake.
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


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_docs_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    # Custom /docs and /redoc routes (added for the branded favicon, see
    # test_docs_ui_uses_branded_favicon) must not exist in production either
    # - the whole point of docs_url=None is that no browsable docs UI is
    # reachable, custom or otherwise.
    assert (await _get(app, "/docs")).status_code == 404
    assert (await _get(app, "/redoc")).status_code == 404


@pytest.mark.asyncio
async def test_docs_enabled_outside_production(monkeypatch):
    """`app.docs_url`/`app.redoc_url` are intentionally `None` even here -

    the branded-favicon override (test_docs_ui_uses_branded_favicon) needs
    FastAPI's own built-in /docs and /redoc route setup suppressed
    (`docs_url=None` at construction) so a fully custom route can replace
    it; the constructor attribute is no longer a reliable "is this UI
    reachable" signal, so this test asserts on the actual route instead.
    """
    monkeypatch.setattr(settings, "environment", "development")
    app = create_app()
    assert (await _get(app, "/docs")).status_code == 200
    assert (await _get(app, "/redoc")).status_code == 200


@pytest.mark.asyncio
async def test_docs_ui_uses_branded_favicon(monkeypatch):
    """Swagger UI/ReDoc default to FastAPI's own favicon unless overridden -

    both custom docs routes must point at this project's icon instead.
    """
    monkeypatch.setattr(settings, "environment", "development")
    app = create_app()

    swagger_html = (await _get(app, "/docs")).text
    redoc_html = (await _get(app, "/redoc")).text

    assert "/static/favicon.ico" in swagger_html
    assert "/static/favicon.ico" in redoc_html


@pytest.mark.asyncio
async def test_static_favicon_served(monkeypatch):
    """The favicon file itself must actually be reachable at that URL, in

    every environment (a static icon isn't part of the docs-UI security
    gate) - both via the /static mount the docs pages reference and via
    the conventional root-level /favicon.ico browsers request automatically.
    """
    monkeypatch.setattr(settings, "environment", "production")
    app = create_app()

    static_response = await _get(app, "/static/favicon.ico")
    root_response = await _get(app, "/favicon.ico")

    assert static_response.status_code == 200
    assert static_response.content
    assert root_response.status_code == 200
    assert root_response.content == static_response.content


def test_webhook_inbound_route_hidden_from_openapi_schema():
    app = create_app()
    schema = app.openapi()
    assert "/api/v1/webhooks/telegram/{bot_id}" not in schema["paths"]


def test_read_app_version_reads_from_pyproject(tmp_path):
    fake_pyproject = tmp_path / "pyproject.toml"
    fake_pyproject.write_text('[project]\nversion = "9.9.9"\n')
    assert _read_app_version(fake_pyproject) == "9.9.9"


def test_read_app_version_falls_back_when_missing(tmp_path):
    missing_path = tmp_path / "does-not-exist.toml"
    assert _read_app_version(missing_path) == "0.0.0"


def test_app_version_matches_pyproject_toml():
    with _PYPROJECT_PATH.open("rb") as f:
        expected_version = tomllib.load(f)["project"]["version"]
    app = create_app()
    assert app.version == expected_version
