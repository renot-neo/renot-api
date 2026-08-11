"""Unit tests for `app.main.create_app` - doc-UI environment gating and

webhook route schema visibility. No DB/network needed (matches the
`tests/unit` tier's scope).
"""

from __future__ import annotations

from app.core.config import settings
from app.main import create_app


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
