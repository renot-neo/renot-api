"""Celery worker entrypoint - imports each domain module's task module here

so they get registered with `app.core.celery_app.celery_app`. Run via:
`celery -A app.worker worker --loglevel=info` (and `beat` for scheduled tasks).

**Also imports EVERY module's `model.py`** (a side effect of registering
its classes with SQLAlchemy's `Base.metadata`/mapper registry) - the exact
same pattern as `alembic/env.py`. Reason: this worker process is separate
from the FastAPI process (`app.main`) - it does NOT automatically import
every router/model the way the web process does (which imports every
router, and so transitively imports every model).
`TenantScopedBase.tenant_id` references `organizations.id` by **table name
string** (`ForeignKey("organizations.id")`, deliberately not a direct
import of the `Organization` class, see `core/database.py`) so other
modules don't have to import each other's internals - the consequence is
that SQLAlchemy can only resolve that string FK once the real
`organizations` table is registered in `Base.metadata`, which only happens
if `app.modules.organizations.model` has been imported in that process.
Before this fix, the messaging worker never imported `organizations.model`
(it only got `bots`/`destinations` transitively via `messaging.service`) -
the first flush in the worker process
(`DeliveryLogRepository.mark_sent`/`mark_failed`) blew up with
`NoReferencedTableError: ... could not find table 'organizations'` (found
during live end-to-end verification, not from a unit test - unit tests mock
the repository, so they never actually trigger SQLAlchemy mapper resolution).
"""

from app.core.celery_app import celery_app
from app.modules.auth import model as auth_model  # noqa: F401
from app.modules.billing import model as billing_model  # noqa: F401
from app.modules.billing import tasks as billing_tasks  # noqa: F401
from app.modules.bots import model as bots_model  # noqa: F401
from app.modules.destinations import model as destinations_model  # noqa: F401
from app.modules.messaging import model as messaging_model  # noqa: F401
from app.modules.messaging import tasks as messaging_tasks  # noqa: F401
from app.modules.organizations import model as organizations_model  # noqa: F401

# TODO: uncomment once this module has a tasks.py with a real task (and add
# `from app.modules.webhooks import model as webhooks_model` above ONLY if
# this module ever gets its own table - right now `modules/webhooks`
# deliberately has no `model.py`, see the docstring there).
# from app.modules.webhooks import tasks as webhooks_tasks  # noqa: F401

__all__ = ["celery_app"]
