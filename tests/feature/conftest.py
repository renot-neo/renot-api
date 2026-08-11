"""Fixtures for `tests/feature/`: end-to-end HTTP-client

flows over real Postgres. Same fixture set as `tests/integration/` - see
`tests/integration/conftest.py`'s docstring for why this is a wildcard
re-export of `tests/support/db.py` rather than `pytest_plugins`.
"""

from __future__ import annotations

from tests.support.db import *  # noqa: F401,F403
