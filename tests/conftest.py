"""Root pytest conftest.

This file intentionally holds no fixtures - they live in
`tests/support/db.py` (real Postgres via `testcontainers`, no SQLite
in-memory, since the project relies on Postgres-specific features like
JSONB and partial unique indexes) and are wildcard-re-exported per test
tier's own `conftest.py` (`tests/integration/conftest.py`,
`tests/feature/conftest.py`) - see those files' docstrings for why a
wildcard import is used instead of `pytest_plugins`. `tests/unit/` never
imports any of this, so unit-only runs stay Postgres/docker-free.
"""
