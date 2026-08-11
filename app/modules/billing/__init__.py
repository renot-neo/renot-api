"""Module `billing` - the service interface exposed to other modules.

Scope: plans, usage metering, and retention.

Cross-module communication MUST go through the interface exposed here (or an
event/message for async/eventual things) - other modules MUST NOT
`from app.modules.billing.model import X` directly.

`enqueue_event_in` is exposed for `modules/webhooks` (called after
`session.commit()`). `enqueue_event_out` is exposed for `modules/messaging`
(called from `tasks._process_delivery` after `mark_sent` + commit). Both are
purely Celery task enqueues (queue `billing.metering`) - the caller doesn't
need an `AsyncSession`, see each one's docstring in `service.py`.
"""

from __future__ import annotations

from app.modules.billing.service import enqueue_event_in, enqueue_event_out

__all__ = ["enqueue_event_in", "enqueue_event_out"]
