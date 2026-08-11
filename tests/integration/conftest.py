"""Fixtures for `tests/integration/`: real Postgres,

no mocked repositories, external calls (Telegram API, Celery dispatch)
mocked. Actual fixtures live in `tests/support/db.py` - re-exported here
(wildcard import, not `pytest_plugins`, which pytest only allows in the
rootdir conftest) so pytest starting a collection under this directory picks
them up, including the module-level `PostgresContainer(...).start()` this
triggers via the `postgres_container` fixture the first time it's requested.
`pytest tests/unit` never imports this file, so unit-only runs stay
docker-free.
"""

from __future__ import annotations

from tests.support.db import *  # noqa: F401,F403
