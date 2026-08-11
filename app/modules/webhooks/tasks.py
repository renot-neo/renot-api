"""Celery tasks for the `webhooks` module.

Scope: processing inbound Telegram webhooks.

Queue: `webhooks.outbound` - registered via `task_routes` in
`app.core.celery_app`. Celery tasks are always sync functions (wrapping
async I/O in `asyncio.run()` inside the task). Should be idempotent where
possible (a dedup key), retrying with exponential backoff and an explicit
max retries - never retry unbounded.

TODO: implement the actual task(s) and register their import in
`app/worker/__init__.py`.
"""
