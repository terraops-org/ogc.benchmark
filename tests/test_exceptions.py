"""Tests for the argument error: a bad option stops the whole run with a clear message."""

from types import SimpleNamespace
from unittest.mock import Mock

import gevent
import pytest

from exceptions import ArgError


def test_handle_error_quits_runner():
    environment = SimpleNamespace(runner=Mock())
    with pytest.raises(ArgError, match="no layer 'x'"):
        ArgError("no layer 'x'").handle_error(environment)
    gevent.sleep(0)  # quit() runs in its own greenlet
    environment.runner.quit.assert_called_once()


def test_handle_error_without_runner():
    with pytest.raises(ArgError):
        ArgError("no runner yet").handle_error(SimpleNamespace(runner=None))


def test_handle_error_sets_exit_code():
    environment = SimpleNamespace(runner=Mock(), process_exit_code=None)
    with pytest.raises(ArgError):
        ArgError("bad").handle_error(environment)
    assert environment.process_exit_code == 1  # a run that stops itself must not exit 0


def test_error_in_on_start_stops_a_real_run():
    """runner.quit() called from inside a user greenlet used to be killed half-way (state left in cleanup,
    the run went on with zero users until -t). A real LocalRunner must reach STOPPED with exit code 1."""
    import gevent
    from locust import User, constant, task
    from locust.env import Environment
    from locust.runners import STATE_STOPPED

    from utils.ogc import cached_capabilities

    class FailsAtStart(User):
        wait_time = constant(1)

        def on_start(self):  # as wms/wmts/wfs do: the other users wait on the capabilities lock meanwhile
            def unreachable():
                gevent.sleep(0.01)
                raise ConnectionError("refused")

            try:
                cached_capabilities(("test", "http://unreachable"), unreachable)
            except ConnectionError as e:
                ArgError(f"Cannot read GetCapabilities: {e}").handle_error(self.environment)

        @task
        def nothing(self):
            pass

    env = Environment(user_classes=[FailsAtStart])
    runner = env.create_local_runner()
    runner.start(3, spawn_rate=3)
    gevent.sleep(1)
    assert runner.state == STATE_STOPPED
    assert env.process_exit_code == 1
