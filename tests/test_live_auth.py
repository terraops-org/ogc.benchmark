"""Live HTTP basic auth check against a password-protected WMS.

Runs only when the service and credentials are given as environment variables (never in a file):

    AUTH_TEST_WMS_URL=https://... AUTH_TEST_USER=... AUTH_TEST_PASSWORD=... uv run pytest tests/test_live_auth.py

AUTH_TEST_* rather than LOCUST_*: conftest.py clears LOCUST_* so option defaults come from the code.
"""

import os

import pytest
from locust.env import Environment

import wms
from utils.ogc import default_options
from wms import WMSBenchmark

URL, USER, PASSWORD = (os.environ.get(n, "") for n in ("AUTH_TEST_WMS_URL", "AUTH_TEST_USER", "AUTH_TEST_PASSWORD"))

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not (URL and USER and PASSWORD), reason="AUTH_TEST_WMS_URL/USER/PASSWORD not set"),
]


def make_user(password):
    wms._CAPABILITIES.clear()
    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wms.init_parser, "--auth-user", USER, "--auth-password", password)
    user = WMSBenchmark(env)
    user.host = URL
    return env, user


def test_basic_auth_capabilities_and_getmap():
    env, user = make_user(PASSWORD)
    seen = []
    env.events.request.add_listener(
        lambda name, response, exception, **kw: seen.append((response.status_code, exception))
    )
    user.on_start()
    user.load_map()
    assert seen == [(200, None)]


def test_wrong_password_is_refused():
    _, user = make_user("wrong")
    with pytest.raises(Exception, match="401"):  # the server refuses, not some other error
        user.on_start()
