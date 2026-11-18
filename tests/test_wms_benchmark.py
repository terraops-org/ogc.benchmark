"""Unit tests for wms.py: layer and CRS choice, bbox axis order, failures, options."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import gevent
import pytest
from locust.env import Environment

import wms
from exceptions import ArgError
from locust.argument_parser import LocustArgumentParser

from utils import ogc
from utils.ogc import default_options
from wms import WMSBenchmark

WGS84 = (3.0, 50.0, 7.0, 54.0)
RD = (0.0, 300000.0, 280000.0, 620000.0)


def layer(crs_list, wgs84=WGS84, crs_options=None, parent=None):
    """An OWSLib-like layer: boundingBox is the first own box, or the WGS84 4-tuple when it has none."""
    lay = Mock()
    lay.crs_list = crs_list
    lay.boundingBox = crs_list[0] if crs_list else wgs84
    lay.boundingBoxWGS84 = wgs84
    lay.crsOptions = crs_options if crs_options is not None else [box[4] for box in crs_list] or ["CRS:84"]
    lay.parent = parent
    return lay


def make_user(*args, layers=None):
    ogc._CAPS.clear()
    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wms.init_parser, *args)
    service = Mock()
    service.contents = layers or {"a": layer([(*WGS84, "EPSG:4326"), (*RD, "EPSG:28992")])}
    service.identification.title = "test service"
    user = WMSBenchmark(env)
    user.host = "http://example.com/wms"
    with patch("wms.WebMapService", return_value=service) as webmapservice:
        user.on_start()
    user.webmapservice = webmapservice  # test-only handle on the OWSLib constructor call
    user.client = MagicMock()
    return user


def fake_resp(status=200, ctype="image/png", content=b"\x89PNG"):
    return SimpleNamespace(status_code=status, headers={"content-type": ctype}, content=content, failure=Mock())


def run_map(user, resp=None):
    """One load_map(); returns the GetMap query parameters and the response it checked."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_map()
    return parse_qs(urlparse(user.client.get.call_args.args[0]).query), resp


def bbox(query):
    return [float(v) for v in query["bbox"][0].split(",")]


def test_auto_crs_prefers_metric():
    user = make_user("--layer-name", "a")
    assert user.extents["a"][4] == "EPSG:28992"


def test_inherited_bbox_uses_crs84():
    user = make_user("--layer-name", "a", layers={"a": layer([])})
    assert user.extents["a"] == (*WGS84, "CRS:84")
    query, _ = run_map(user)
    assert query["crs"] == ["CRS:84"]
    minx, miny, maxx, maxy = bbox(query)
    assert 3.0 <= minx < maxx <= 7.0 and 50.0 <= miny < maxy <= 54.0


def test_yx_crs_swaps_bbox():
    user = make_user("--layer-name", "a", "--crs", "EPSG:4326")
    query, _ = run_map(user)
    first, second, third, fourth = bbox(query)
    assert 50.0 <= first < third <= 54.0  # latitude first for EPSG:4326 in WMS 1.3.0
    assert 3.0 <= second < fourth <= 7.0


def test_explicit_crs_missing_raises():
    with pytest.raises(ArgError):
        make_user("--layer-name", "a", "--crs", "EPSG:3006")


def test_epsg3857_derived_from_wgs84():
    user = make_user("--layer-name", "a", "--crs", "EPSG:3857")
    minx, _, _, _, crs = user.extents["a"]
    assert crs == "EPSG:3857"
    assert minx == pytest.approx(333958.47, abs=0.1)


def test_unknown_layer_raises():
    with pytest.raises(ArgError):
        make_user("--layer-name", "nope")


def test_layer_mix_weights():
    two = {"a": layer([(*RD, "EPSG:28992")]), "b": layer([(*RD, "EPSG:28992")])}
    user = make_user("--layers", "a:3,b:1", layers=two)
    assert user.layer_names == ["a", "b"]
    assert user.layer_weights == [3.0, 1.0]


def test_two_users_different_bboxes():
    first, _ = run_map(make_user("--layer-name", "a"))
    second, _ = run_map(make_user("--layer-name", "a"))
    assert bbox(first) != bbox(second)


def test_failure_message_stable():
    body = b"<ServiceExceptionReport><ServiceException>BBOX 12.3,45.6 out of range</ServiceException>"
    _, resp = run_map(make_user("--layer-name", "a"), fake_resp(200, "text/xml", body))
    resp.failure.assert_called_once_with("OGC Exception")


def test_env_var_defaults(monkeypatch):
    monkeypatch.setenv("LOCUST_BBOX_AREA", "4")
    monkeypatch.setenv("LOCUST_WIDTH", "4")
    opts = default_options(wms.init_parser)
    assert opts.bbox_area == 4.0
    assert opts.width == 4


def test_caps_ratio_one_requests_capabilities():
    user = make_user("--layer-name", "a", "--caps-ratio", "1")
    user.client.get.return_value.__enter__.return_value = fake_resp(200, "text/xml", b"<WMS_Capabilities/>")
    user.load_map()
    assert user.client.get.call_args.kwargs["name"] == "GetCapabilities"


def test_basic_auth():
    user = make_user("--layer-name", "a", "--auth-user", "u", "--auth-password", "p")
    run_map(user)
    assert user.client.get.call_args.kwargs["headers"]["Authorization"] == "Basic dTpw"
    assert user.webmapservice.call_args.kwargs["auth"].username == "u"


def test_no_auth_no_header():
    user = make_user("--layer-name", "a")
    assert "Authorization" not in user.headers


def test_password_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    run_map(make_user("--layer-name", "a", "--auth-user", "u", "--auth-password", "p4ss-7f3a"))
    assert "p4ss-7f3a" not in caplog.text


def test_old_flags_gone():
    with pytest.raises(SystemExit):
        default_options(wms.init_parser, "--wms-user", "x")


def test_inherited_parent_boxes():
    """WMS 1.3.0: a layer without boxes of its own inherits its parent's BoundingBoxes."""
    parent = layer([(*WGS84, "EPSG:4326"), (*RD, "EPSG:28992")])
    child = layer([], crs_options=["EPSG:28992", "EPSG:4326"], parent=parent)
    user = make_user("--layer-name", "a", layers={"a": child})
    assert user.extents["a"] == (*RD, "EPSG:28992")
    user = make_user("--layer-name", "a", "--crs", "EPSG:4326", layers={"a": child})
    assert user.extents["a"] == (*WGS84, "EPSG:4326")


def test_no_boxes_and_no_crs84_falls_back_to_epsg4326():
    user = make_user("--layer-name", "a", layers={"a": layer([], crs_options=["EPSG:4326"])})
    assert user.extents["a"] == (*WGS84, "EPSG:4326")
    query, _ = run_map(user)
    first, second, _, _ = bbox(query)
    assert 50.0 <= first <= 54.0 and 3.0 <= second <= 7.0  # EPSG:4326 goes latitude first


def test_crs_in_feet_refused():
    feet = (900000.0, 120000.0, 1070000.0, 280000.0)
    with pytest.raises(ArgError):
        make_user("--layer-name", "a", layers={"a": layer([(*feet, "EPSG:2263")])})


def test_mercator_clamps_the_poles():
    _, south = wms.mercator(-180, -90)
    _, north = wms.mercator(180, 90)
    assert south == pytest.approx(-20037508.34, abs=1) and north == pytest.approx(20037508.34, abs=1)


@pytest.mark.parametrize(
    "mix, names, weights",
    [("a", ["a"], [1.0]), ("ns:a", ["ns:a"], [1.0]), ("ns:a:2,a:3", ["ns:a", "a"], [2.0, 3.0])],
)
def test_layer_mix_parsing(mix, names, weights):
    layers = {"a": layer([(*RD, "EPSG:28992")]), "ns:a": layer([(*RD, "EPSG:28992")])}
    user = make_user("--layers", mix, layers=layers)
    assert (user.layer_names, user.layer_weights) == (names, weights)


@pytest.mark.parametrize(
    "args",
    [
        ("--bbox-area", "0"),
        ("--bbox-areas", "1,0"),
        ("--bbox-areas", "1,x"),
        ("--bbox-ratio", "0"),
        ("--layers", "a:0"),
        ("--auth-password", "p"),
    ],
    ids=str,
)
def test_bad_options_stop_at_start(args):
    with pytest.raises(ArgError):
        make_user("--layer-name", "a", *args)


def test_capabilities_failure_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wms.init_parser)
    env.runner.quit = Mock()
    user = WMSBenchmark(env)
    user.host = "http://example.com/wms"
    with patch("wms.WebMapService", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)  # quit() runs in its own greenlet
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_capabilities_fetched_once_per_process():
    first = make_user("--layer-name", "a")
    user = WMSBenchmark(first.environment)  # a second user in the same process
    user.host = "http://example.com/wms"
    with patch("wms.WebMapService") as webmapservice:
        user.on_start()
    webmapservice.assert_not_called()


def test_password_not_in_web_ui():
    parser = LocustArgumentParser()
    wms.init_parser(parser)
    assert "auth_password" not in parser.args_included_in_web_ui  # the web UI page would carry its value


def test_tls_not_verified_by_default():
    assert make_user("--layer-name", "a").webmapservice.call_args.kwargs["auth"].verify is False
    assert make_user("--layer-name", "a", "--verify-tls").webmapservice.call_args.kwargs["auth"].verify is True


def test_user_agent_sent():
    user = make_user("--layer-name", "a")
    run_map(user)
    assert (
        user.client.get.call_args.kwargs["headers"]["User-Agent"]
        == "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    )
    assert user.webmapservice.call_args.kwargs["headers"] == {
        "User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    }
