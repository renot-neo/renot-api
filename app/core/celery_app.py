"""Central Celery app.

Separate queues per concern (not a single default queue for everything):
- `messaging.send`       - sending messages to Telegram (high priority, per-bot throttled)
- `messaging.scheduled`  - dispatcher for scheduled messages
- `webhooks.outbound`    - status callbacks sent to external apps
- `billing.metering`     - usage event recording (can be eventual, low priority)

Task modules are registered via `app/worker/__init__.py` (which imports from
`modules/*/tasks.py`), not registered directly here.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "renot",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
)

celery_app.conf.update(
    task_routes={
        "app.modules.messaging.tasks.dispatch_scheduled_message": {"queue": "messaging.scheduled"},
        "app.modules.messaging.tasks.*": {"queue": "messaging.send"},
        "app.modules.webhooks.tasks.*": {"queue": "webhooks.outbound"},
        "app.modules.billing.tasks.*": {"queue": "billing.metering"},
    },
    task_default_queue="default",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
    # Celery beat: periodic dispatcher for scheduled `Message` sends (the
    # optional "schedule send" feature) - scans every 30 seconds, see
    # `modules/messaging/tasks.dispatch_scheduled_message`.
    beat_schedule={
        "messaging-dispatch-scheduled": {
            "task": "app.modules.messaging.tasks.dispatch_scheduled_message",
            "schedule": 30.0,
        },
        # Retention purge (`UsageEvent`/`messaging.DeliveryLog`, driven by
        # `Plan.retention_days`). Daily at 03:00 UTC (low-traffic) - unlike
        # the scheduled-message dispatcher above which must run often
        # (a scheduled message can come due at any time), purging these
        # high-volume tables once a day is enough.
        "billing-purge-expired-usage-data": {
            "task": "app.modules.billing.tasks.purge_expired_usage_data",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)
