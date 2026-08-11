"""Unit test for the deliberately-empty `app.modules.webhooks` stub files -

`model.py`/`repository.py`/`schema.py` exist purely
to document *why* this module has no entity/table of its own (see each
file's own docstring), and are never imported by the running app otherwise
(no `alembic/env.py` or `app/worker/__init__.py` entry, unlike every other
module - intentional, see those files' comments). Importing them here is
enough to prove they stay valid Python as the rest of the codebase evolves.
"""

from __future__ import annotations


def test_stub_modules_import_cleanly() -> None:
    import app.modules.webhooks.model  # noqa: F401
    import app.modules.webhooks.repository  # noqa: F401
    import app.modules.webhooks.schema  # noqa: F401
