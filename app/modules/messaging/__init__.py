"""Module `messaging` - the service interface exposed to other modules.

Scope: sending messages, templates, scheduling, and delivery tracking.

Cross-module communication MUST go through the interface exposed here (or an
event/message for async/eventual things) - other modules MUST NOT
`from app.modules.messaging.model import X` directly.

`purge_delivery_logs_batch` is exposed for `modules/billing` (retention
purge - the `Plan.retention_days` policy is centralized there, called from
`billing/tasks.py::purge_expired_usage_data`, a daily Celery beat job).
`cascade_delete_pending_messages` is exposed for `modules/organizations`
(the cascade run by `DELETE /organizations/{id}` - see its docstring in
`service.py`). `router.py`/`tasks.py` import `app.modules.messaging.service`
directly (within this module itself, not cross-module, so this doesn't
violate the rule above).
"""

from __future__ import annotations

from app.modules.messaging.service import cascade_delete_pending_messages, purge_delivery_logs_batch

__all__ = ["cascade_delete_pending_messages", "purge_delivery_logs_batch"]
