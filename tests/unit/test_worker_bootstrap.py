"""Unit tests for `app.worker`.

Regression test for a bug class that was hit live once: the Celery worker
process doesn't import `app.main`'s routers
transitively like the FastAPI process does, so every module's `model.py`
must be explicitly imported in `app/worker/__init__.py` or SQLAlchemy can't
resolve the string `ForeignKey("organizations.id")` used by
`TenantScopedBase` - a bug unit tests with mocked repositories can never
catch (mapper resolution only triggers on a real flush), which is exactly
why it slipped through 163 passing mocked tests at the time. This test
can't run the worker process for real either, but it at least proves the
import graph itself is intact and every domain model actually got
registered onto `Base.metadata` - the specific thing that broke.
"""

from __future__ import annotations

from app.core.database import Base


def test_worker_module_imports_cleanly() -> None:
    import app.worker  # noqa: F401 - import success is the assertion


def test_worker_registers_every_domain_model_on_base_metadata() -> None:
    import app.worker  # noqa: F401 - side effect: registers every model below

    table_names = set(Base.metadata.tables.keys())
    for expected in (
        "users",
        "organizations",
        "organization_memberships",
        "bots",
        "destinations",
        "bot_destination_subscriptions",
        "messages",
        "message_templates",
        "delivery_logs",
        "plans",
        "organization_plans",
        "usage_events",
    ):
        assert expected in table_names


def test_worker_registers_expected_celery_tasks() -> None:
    from app.worker import celery_app

    task_names = set(celery_app.tasks.keys())
    assert "app.modules.messaging.tasks.send_message_to_destination" in task_names
    assert "app.modules.messaging.tasks.dispatch_scheduled_message" in task_names
    assert "app.modules.billing.tasks.record_usage_event" in task_names
