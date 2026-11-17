"""Unit tests for wms.py: layer and CRS choice, bbox axis order, failures, options."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from locust.env import Environment

import wms
from exceptions import LayerNameArgError
from utils.ogc import default_options
from wms import WMSBenchmark

WGS84 = (3.0, 50.0, 7.0, 54.0)
RD = (0.0, 300000.0, 280000.0, 620000.0)


def layer(crs_list, wgs84=WGS84):
    """An OWSLib-like layer: boundingBox is the first own box, or the WGS84 4-tuple when it has none."""
    lay = Mock()
    lay.crs_list = crs_list
    lay.boundingBox = crs_list[0] if crs_list else wgs84
    lay.boundingBoxWGS84 = wgs84
    return lay


def make_user(*args, layers=None):
    wms._CAPABILITIES.clear()
    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wms.init_parser, *args)
    service = Mock()
    service.contents = layers or {"a": layer([(*WGS84, "EPSG:4326"), (*RD, "EPSG:28992")])}
    service.identification.title = "test service"
    user = WMSBenchmark(env)
    user.host = "http://example.com/wms"
    with patch("wms.WebMapService", return_value=service):
        user.on_start()
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
    with pytest.raises(LayerNameArgError):
        make_user("--layer-name", "a", "--crs", "EPSG:3006")


def test_epsg3857_derived_from_wgs84():
    user = make_user("--layer-name", "a", "--crs", "EPSG:3857")
    minx, _, _, _, crs = user.extents["a"]
    assert crs == "EPSG:3857"
    assert minx == pytest.approx(333958.47, abs=0.1)


def test_unknown_layer_raises():
    with pytest.raises(LayerNameArgError):
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
