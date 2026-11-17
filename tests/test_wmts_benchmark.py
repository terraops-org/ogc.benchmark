"""Unit tests for wmts.py: layer / matrix set / matrix choice, tile ranges, per-user streams, failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import gevent
import pytest
from locust.env import Environment

import wmts
from exceptions import ArgError
from utils import ogc
from utils.ogc import default_options
from wmts import WMTSBenchmark

MATRICES = {"0": (1, 1), "1": (10, 20), "2": (100, 200)}  # id: (matrixwidth, matrixheight); middle is "1"


def make_service():
    layer = Mock()
    layer.formats = ["image/png"]
    layer.tilematrixsetlinks = {"EPSG:28992": Mock()}
    matrices = {k: SimpleNamespace(matrixwidth=w, matrixheight=h) for k, (w, h) in MATRICES.items()}
    service = Mock()
    service.contents = {"a": layer}
    service.tilematrixsets = {"EPSG:28992": SimpleNamespace(tilematrix=matrices)}
    return service


def make_user(*args):
    ogc._CAPS.clear()
    env = Environment(user_classes=[WMTSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wmts.init_parser, *args)
    user = WMTSBenchmark(env)
    user.host = "http://example.com/wmts"
    with patch("wmts.WebMapTileService", return_value=make_service()):
        user.on_start()
    user.client = MagicMock()
    return user


def fake_resp(status=200, ctype="image/png", content=b"\x89PNG"):
    return SimpleNamespace(status_code=status, headers={"content-type": ctype}, content=content, failure=Mock())


def run_tile(user, resp=None):
    """One load_tile(); returns the GetTile query parameters and the response it checked."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_tile()
    return parse_qs(urlparse(user.client.get.call_args.args[0]).query), resp


def tile(query):
    return int(query["TILECOL"][0]), int(query["TILEROW"][0])


def test_defaults_first_layer_set_and_middle_matrix():
    user = make_user()
    assert (user.layer_name, user.tile_matrix_set, user.tile_matrix_value) == ("a", "EPSG:28992", "1")
    query, _ = run_tile(user)
    assert query["LAYER"] == ["a"] and query["TILEMATRIXSET"] == ["EPSG:28992"] and query["TILEMATRIX"] == ["1"]
    assert query["REQUEST"] == ["GetTile"] and query["FORMAT"] == ["image/png"]


@pytest.mark.parametrize(
    "args", [("--layer-name", "nope"), ("--tile-matrix-set", "EPSG:3857"), ("--tile-matrix", "9")], ids=str
)
def test_unknown_values_raise(args):
    with pytest.raises(ArgError):
        make_user(*args)


def test_tile_inside_matrix():
    user = make_user("--tile-matrix", "2")
    tiles = [tile(run_tile(user)[0]) for _ in range(100)]
    assert all(0 <= col <= 99 and 0 <= row <= 199 for col, row in tiles)
    assert len(set(tiles)) > 50  # really random, not one tile repeated


def test_two_users_different_tiles():
    first = tile(run_tile(make_user("--tile-matrix", "2"))[0])
    second = tile(run_tile(make_user("--tile-matrix", "2"))[0])
    assert first != second


def test_failure_message_stable():
    _, resp = run_tile(make_user(), fake_resp(503, "text/html", b"<html>busy</html>"))
    resp.failure.assert_called_once_with("Server Overload: HTTP 503")


def test_capabilities_failure_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[WMTSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wmts.init_parser)
    env.runner.quit = Mock()
    user = WMTSBenchmark(env)
    user.host = "http://example.com/wmts"
    with patch("wmts.WebMapTileService", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)  # quit() runs in its own greenlet
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_capabilities_fetched_once_per_process():
    first = make_user()
    user = WMTSBenchmark(first.environment)
    user.host = "http://example.com/wmts"
    with patch("wmts.WebMapTileService") as service:
        user.on_start()
    service.assert_not_called()
