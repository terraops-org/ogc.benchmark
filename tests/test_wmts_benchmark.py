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
from utils.tiles import Matrix, tile_range, to_crs_box
from wmts import WMTSBenchmark

MATRICES = {"0": (1, 1), "1": (10, 20), "2": (100, 200)}  # id: (matrixwidth, matrixheight); middle is "1"
SPANS = {"0": 1_000_000, "1": 100_000, "2": 10_000}  # metres per tile: every level covers the same 1000 x 2000 km
ORIGIN = (-500_000.0, 1_500_000.0)  # top-left corner, EPSG:28992 x/y
WGS84 = (4.0, 51.0, 6.0, 53.0)  # the layer's WGS84 bbox: part of the grid
LEVELS = {k: Matrix(k, *ORIGIN, SPANS[k], SPANS[k] * (2 if k == "0" else 1), w, h) for k, (w, h) in MATRICES.items()}
IMAGE_TILE = tile_range(LEVELS["1"], to_crs_box((5.1, 52.0, 5.1, 52.0), "EPSG:28992"))[0::2]  # (col, row), level 1


def matrix(k):
    w, h = MATRICES[k]
    scale = SPANS[k] / (256 * 0.00028)
    return SimpleNamespace(
        identifier=k,
        topleftcorner=ORIGIN,
        scaledenominator=scale,
        tilewidth=256,
        tileheight=512 if k == "0" else 256,
        matrixwidth=w,
        matrixheight=h,
    )


def make_service(limits=None, ids=None, crs="EPSG:28992"):
    layer = Mock()
    layer.formats = ["image/png"]
    layer.boundingBoxWGS84 = WGS84
    layer.tilematrixsetlinks = {"EPSG:28992": SimpleNamespace(tilematrixlimits=limits or {})}
    service = Mock()
    service.contents = {"a": layer}
    service.tilematrixsets = {
        "EPSG:28992": SimpleNamespace(crs=crs, tilematrix={k: matrix(k) for k in (ids or MATRICES)})
    }
    return service


def probe_answer(image=lambda col, row: (col, row) == IMAGE_TILE):
    """A fake requests.get for the probe: an image under `image(col, row)`, the same blank body elsewhere."""

    def get(url, **kwargs):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        col, row = int(q["TILECOL"]), int(q["TILEROW"])
        body = b"IMG-%d-%d" % (col, row) if image(col, row) else b"BLANK"
        return SimpleNamespace(status_code=200, content=body)

    return Mock(side_effect=get)


def make_user(*args, limits=None, probe=None, ids=None, crs="EPSG:28992"):
    ogc._CAPS.clear()
    env = Environment(user_classes=[WMTSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wmts.init_parser, *args)
    user = WMTSBenchmark(env)
    user.host = "http://example.com/wmts"
    user.probe_get = probe or probe_answer()
    with (
        patch("wmts.WebMapTileService", return_value=make_service(limits, ids, crs)) as ctor,
        patch("wmts.requests.get", user.probe_get),
    ):
        user.on_start()
    user.wmts_ctor = ctor  # test-only handle on the OWSLib constructor call
    user.client = MagicMock()
    return user


def fake_resp(status=200, ctype="image/png", content=b"\x89PNG"):
    return SimpleNamespace(
        status_code=status,
        headers={"content-type": ctype},
        content=content,
        failure=Mock(),
        request_meta={"name": "GetTile"},
    )


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


def test_tiles_drawn_only_under_image_tiles():
    user = make_user("--tile-matrix", "2")
    tiles = [tile(run_tile(user)[0]) for _ in range(50)]
    assert all((col // 10, row // 10) == IMAGE_TILE for col, row in tiles)  # level 2 is 10 x 10 per level-1 tile


def test_blank_tile_reported_as_empty():
    user = make_user()
    _, resp = run_tile(user, fake_resp(content=b"BLANK"))
    assert resp.request_meta["name"] == "GetTile (empty)"
    resp.failure.assert_not_called()
    _, resp = run_tile(user, fake_resp(content=b"IMG"))
    assert resp.request_meta["name"] == "GetTile"


def test_extent_outside_layer_stops_the_run():
    with pytest.raises(ArgError, match="does not overlap"):
        make_user("--extent", "100,10,101,11")


def test_bad_extent_stops_the_run():
    with pytest.raises(ArgError, match="--extent"):
        make_user("--extent", "5,52,4,51")


def test_probe_runs_once_per_process():
    first = make_user("--tile-matrix", "2")
    probed = first.probe_get.call_count
    assert probed > 1
    user = WMTSBenchmark(first.environment)
    user.host = "http://example.com/wmts"
    with patch("wmts.WebMapTileService", return_value=make_service()), patch("wmts.requests.get") as get:
        user.on_start()
    get.assert_not_called()


def test_no_image_tiles_warns_and_uses_area(caplog):
    user = make_user("--tile-matrix", "2", probe=probe_answer(image=lambda col, row: False))
    assert "no tile with an image" in caplog.text
    c0, c1, r0, r1 = tile_range(LEVELS["2"], user.area)
    tiles = [tile(run_tile(user)[0]) for _ in range(50)]
    assert all(c0 <= col <= c1 and r0 <= row <= r1 for col, row in tiles)


def test_tile_matrix_limits_used_when_advertised():
    limits = {"2": SimpleNamespace(mintilerow=2, maxtilerow=3, mintilecol=4, maxtilecol=5)}
    user = make_user("--tile-matrix", "2", limits=limits)
    tiles = [tile(run_tile(user)[0]) for _ in range(50)]
    assert all(4 <= col <= 5 and 2 <= row <= 3 for col, row in tiles)


def test_tls_not_verified_by_default():
    user = make_user()
    assert user.wmts_ctor.call_args.kwargs["auth"].verify is False
    assert all(c.kwargs["verify"] is False for c in user.probe_get.call_args_list)
    user = make_user("--verify-tls")
    assert user.wmts_ctor.call_args.kwargs["auth"].verify is True
    assert all(c.kwargs["verify"] is True for c in user.probe_get.call_args_list)


def test_user_agent_sent():
    user = make_user()
    run_tile(user)
    assert (
        user.client.get.call_args.kwargs["headers"]["User-Agent"]
        == "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    )
    assert user.wmts_ctor.call_args.kwargs["headers"] == {
        "User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    }
    assert all(
        c.kwargs["headers"]["User-Agent"] == "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
        for c in user.probe_get.call_args_list
    )


def test_no_level_small_enough_to_probe(caplog):
    """Only a fine level (100 x 200 tiles, the area holds more than 256): warn and draw over the area (I2)."""
    user = make_user(ids=["2"])
    assert "no level small enough to probe" in caplog.text
    c0, c1, r0, r1 = tile_range(LEVELS["2"], user.area)
    tiles = [tile(run_tile(user)[0]) for _ in range(30)]
    assert all(c0 <= col <= c1 and r0 <= row <= r1 for col, row in tiles)


LIMITS = {"2": SimpleNamespace(mintilerow=90, maxtilerow=120, mintilecol=50, maxtilecol=80)}


def test_extent_outside_limits_stops_the_run():
    with pytest.raises(ArgError, match="does not overlap"):
        make_user("--tile-matrix", "2", "--extent", "100,10,101,11", limits=LIMITS)


def test_bad_extent_with_limits_stops_the_run():
    with pytest.raises(ArgError, match="--extent"):
        make_user("--tile-matrix", "2", "--extent", "not,an,extent,here", limits=LIMITS)


def test_extent_narrows_the_limits():
    user = make_user("--tile-matrix", "2", "--extent", "4.0,51.0,6.0,53.0", limits=LIMITS)
    c0, c1, r0, r1 = tile_range(LEVELS["2"], to_crs_box((4.0, 51.0, 6.0, 53.0), "EPSG:28992"))
    tiles = [tile(run_tile(user)[0]) for _ in range(40)]
    assert c0 < 80 and c1 > 50 and r0 < 120 and r1 > 90  # the extent and the limits overlap
    assert all(max(50, c0) <= col <= min(80, c1) and max(90, r0) <= row <= min(120, r1) for col, row in tiles)


def test_unknown_grid_crs_uses_the_whole_matrix(caplog):
    """A TileMatrixSet CRS pyproj cannot read (EPSG:102100) worked before the probe: it must still run (I6)."""
    user = make_user("--tile-matrix", "1", crs="urn:ogc:def:crs:EPSG::102100")
    assert "pyproj does not know" in caplog.text
    tiles = [tile(run_tile(user)[0]) for _ in range(30)]
    assert all(0 <= col <= 9 and 0 <= row <= 19 for col, row in tiles)


def test_unknown_grid_crs_with_extent_stops_the_run():
    with pytest.raises(ArgError, match="--extent"):
        make_user("--extent", "4.0,51.0,6.0,53.0", crs="urn:ogc:def:crs:EPSG::102100")
