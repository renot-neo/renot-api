"""Shared support code for `tests/integration/` and `tests/feature/`.

Not itself a `conftest.py` on purpose - `pytest_plugins` can only be declared
in the rootdir `conftest.py` since pytest 4.x, so fixtures defined here are
re-exported via `from tests.support.db import *` in each subtree's own
`conftest.py` instead (see `tests/integration/conftest.py`,
`tests/feature/conftest.py`).
"""
