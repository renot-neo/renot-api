"""Unit tests for `app.main.create_app` - doc-UI environment gating and

webhook route schema visibility. No DB/network needed (matches the
`tests/unit` tier's scope).
"""

from __future__ import annotations

import tomllib

from app.core.config import settings
from app.main import _PYPROJECT_PATH, _read_app_version, create_app


def test_docs_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None


def test_docs_enabled_outside_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    app = create_app()
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"


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
