"""Unit tests for utils/ogc.py: response checks, CRS facts, per-user random streams."""

import itertools
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from utils import ogc
import gevent

from utils.ogc import (
    CAPABILITIES,
    FEATURES,
    IMAGE,
    auth_header,
    bbox_areas,
    cached_capabilities,
    check_response,
    intersect,
    is_geographic,
    is_metric,
    is_yx,
    mark_empty,
    parse_extent,
    user_rng,
)


def fake_response(status, ctype=None, content=b""):
    headers = None if ctype is None else {"content-type": ctype}
    return SimpleNamespace(status_code=status, headers=headers, content=content, failure=Mock())


def test_status_zero_is_connection_error():
    resp = fake_response(0, content=None)
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with("Connection error")


@pytest.mark.parametrize("code", [502, 503, 504])
def test_overload_codes(code):
    resp = fake_response(code, "text/html")
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with(f"Server Overload: HTTP {code}")


def test_xml_error_status():
    resp = fake_response(400, "text/xml", b"<ServiceExceptionReport/>")
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with("OGC Error: HTTP 400")
    resp = fake_response(404, "text/html", b"<html>not found</html>")
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with("HTTP 404")


def test_ok_image():
    resp = fake_response(200, "image/png", b"\x89PNG")
    check_response(resp, IMAGE)
    resp.failure.assert_not_called()


def test_ogc_exception_in_200():
    body = b'<?xml version="1.0"?><ServiceExceptionReport><ServiceException>bbox 1,2,3,4</ServiceException>'
    resp = fake_response(200, "text/xml", body)
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with("OGC Exception")


def test_wrong_type():
    resp = fake_response(200, "text/html; charset=utf-8", b"<html></html>")
    check_response(resp, IMAGE)
    resp.failure.assert_called_once_with("Invalid content type: text/html")


def test_features_accepts_gml():
    resp = fake_response(200, "text/xml; subtype=gml/3.2", b'<wfs:FeatureCollection numberReturned="3">')
    check_response(resp, FEATURES)
    resp.failure.assert_not_called()


def test_crs_facts():
    for code in ("EPSG:28992", "urn:ogc:def:crs:EPSG::28992", "EPSG:3857"):
        assert is_metric(code), code
    for code in ("EPSG:4326", "OGC:CRS84", "CRS:84", "EPSG:999999"):
        assert not is_metric(code), code
    for code in ("EPSG:4326", "EPSG:3006"):
        assert is_yx(code), code
    for code in ("EPSG:28992", "CRS:84", "nonsense"):
        assert not is_yx(code), code


def test_user_rng_streams_differ():
    env = SimpleNamespace(runner=SimpleNamespace())  # a local runner has no worker_index
    ogc._USER_IDS = itertools.count()
    first = (user_rng(env, 1640).random(), user_rng(env, 1640).random())
    assert first[0] != first[1]

    ogc._USER_IDS = itertools.count()
    assert (user_rng(env, 1640).random(), user_rng(env, 1640).random()) == first

    ogc._USER_IDS = itertools.count()
    worker3 = SimpleNamespace(runner=SimpleNamespace(worker_index=3))
    assert user_rng(worker3, 1640).random() != first[0]


def test_common_args():
    opts = ogc.default_options(lambda parser: ogc.add_common_args(parser))
    assert (opts.random_seed, opts.bbox_area, opts.bbox_ratio) == (1640, 100.0, 1.0)
    opts = ogc.default_options(lambda parser: ogc.add_common_args(parser, bbox=False))
    assert opts.random_seed == 1640 and not hasattr(opts, "bbox_area")


def test_capabilities_exception_element_is_not_a_failure():
    """A WMS 1.3.0 capabilities document lists its <Exception> formats early; that is not an error report."""
    doc = b'<WMS_Capabilities version="1.3.0"><Capability><Request/><Exception><Format>XML</Format></Exception>'
    resp = fake_response(200, "text/xml", doc)
    check_response(resp, CAPABILITIES)
    resp.failure.assert_not_called()


def test_is_geographic():
    for code in ("EPSG:4326", "CRS:84", "OGC:CRS84", "urn:ogc:def:crs:OGC:1.3:CRS84"):
        assert is_geographic(code), code
    for code in ("EPSG:28992", "EPSG:2263", "nonsense"):
        assert not is_geographic(code), code


def test_capabilities_fetched_once_while_users_arrive_together():
    ogc._CAPS.clear()
    calls = []

    def fetch():
        calls.append(1)
        gevent.sleep(0.05)  # a slow GetCapabilities: other users arrive meanwhile
        return "doc"

    jobs = [gevent.spawn(cached_capabilities, ("wms", "http://h"), fetch) for _ in range(5)]
    gevent.joinall(jobs)
    assert len(calls) == 1
    assert sorted(job.value[1] for job in jobs) == [False, False, False, False, True]  # one fetched, four reused
    assert {job.value[0] for job in jobs} == {"doc"}


def test_204_is_an_empty_success_only_when_allowed():
    resp = fake_response(204)
    assert check_response(resp, IMAGE, empty_ok=True) is True
    resp.failure.assert_not_called()
    resp = fake_response(204)
    assert check_response(resp, IMAGE) is False
    resp.failure.assert_called_once_with("HTTP 204")


def test_check_response_returns_success():
    assert check_response(fake_response(200, "image/png"), IMAGE) is True
    assert check_response(fake_response(500, "text/html"), IMAGE) is False


def test_mark_empty_renames():
    resp = SimpleNamespace(request_meta={"name": "GetTile"})
    mark_empty(resp)
    assert resp.request_meta["name"] == "GetTile (empty)"


def test_auth_header():
    assert auth_header(SimpleNamespace(auth_user="u", auth_password="p")) == {"Authorization": "Basic dTpw"}
    assert auth_header(SimpleNamespace(auth_user="", auth_password="")) == {}


@pytest.mark.parametrize("text", ["1,2,3", "a,b,c,d", "-9,39,-9.5,38.5", "-200,0,0,10", "0,-95,10,0"])
def test_parse_extent_rejects(text):
    with pytest.raises(ValueError):
        parse_extent(text)


def test_parse_extent():
    assert parse_extent("") is None
    assert parse_extent("-9.5,38.5,-9.0,39.0") == (-9.5, 38.5, -9.0, 39.0)


def test_intersect():
    assert intersect((0, 0, 10, 10), (5, 5, 20, 20)) == (5, 5, 10, 10)
    assert intersect((0, 0, 10, 10), (20, 20, 30, 30)) is None
    assert intersect((0, 0, 10, 10), (10, 0, 20, 10)) is None  # touching edges: no area


def test_bbox_areas():
    assert bbox_areas(SimpleNamespace(bbox_areas="1,10,100", bbox_area=100.0)) == [1.0, 10.0, 100.0]
    assert bbox_areas(SimpleNamespace(bbox_areas="", bbox_area=50.0)) == [50.0]
    with pytest.raises(ValueError, match="--bbox-areas must be km2 numbers"):
        bbox_areas(SimpleNamespace(bbox_areas="a", bbox_area=100.0))
