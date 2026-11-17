"""Shared test setup.

gevent must patch the standard library before anything imports `requests` (urllib3 builds its SSL
context at import time; patched too late, Python 3.14+ raises RecursionError). pytest imports this
file before any test module, so the patch goes first.
"""

from gevent import monkey

monkey.patch_all()

import os  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_locust_env(monkeypatch):
    """Option defaults come from the code, not from LOCUST_* variables in the developer's shell."""
    for name in [n for n in os.environ if n.startswith("LOCUST_")]:
        monkeypatch.delenv(name)
