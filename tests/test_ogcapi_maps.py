"""Unit tests for ogcapi_maps.py: collection and CRS choice, map parameters, zoom mix, blank maps, failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import gevent
import pytest
from locust.env import Environment

import ogcapi_maps
from exceptions import ArgError
from ogcapi_maps import OGCAPIMapsBenchmark
from utils import ogc
from utils.ogc import default_options
from utils.ogcapi import crs_uri
from utils.tiles import to_crs_box

HOST = "http://example.com/api"
EXTENT = (-10.19, 36.76, -5.71, 42.28)
COLLECTIONS = {
    "collections": [
        {"id": "feat", "links": [{"rel": "items", "type": "application/geo+json", "href": f"{HOST}/x"}]},
        {
            "id": "cosc",
            "links": [
                {
                    "rel": "http://www.opengis.net/def/rel/ogc/1.0/map",
                    "type": "image/png",
                    "href": f"{HOST}/collections/cosc/map?f=png",
                }
            ],
            "extent": {"spatial": {"bbox": [list(EXTENT)]}},
        },
    ]
}


def answers(reference_status=200, reference_body=b"BLANK"):
    """A fake requests.get: the collections JSON, or the blank-reference map."""

    def get(url, **kwargs):
        if urlparse(url).path.endswith("/map"):
            return SimpleNamespace(
                status_code=reference_status, headers={"content-type": "image/png"}, content=reference_body
            )
        return Mock(json=Mock(return_value=COLLECTIONS), raise_for_status=Mock())

    return Mock(side_effect=get)


def make_user(*args, get=None):
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPIMapsBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_maps.init_parser, *args)
    user = OGCAPIMapsBenchmark(env)
    user.host = HOST
    user.metadata_get = get or answers()
    with patch("utils.ogcapi.requests.get", user.metadata_get), patch("ogcapi_maps.requests.get", user.metadata_get):
        user.on_start()
    user.client = MagicMock()
    return user


def fake_resp(content=b"IMG"):
    return SimpleNamespace(
        status_code=200,
        headers={"content-type": "image/png"},
        content=content,
        failure=Mock(),
        request_meta={"name": "GetMap cosc"},
    )


def run_map(user, resp=None):
    """One load_map(); returns the query parameters (single values) and the response it checked."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_map()
    return {k: v[0] for k, v in parse_qs(urlparse(user.client.get.call_args.args[0]).query).items()}, resp


def bbox(query):
    return [float(v) for v in query["bbox"].split(",")]


def test_default_params_crs84():
    user = make_user()
    query, _ = run_map(user)
    assert (query["f"], query["width"], query["height"]) == ("png", "512", "512")
    minx, miny, maxx, maxy = bbox(query)
    assert EXTENT[0] <= minx < maxx <= EXTENT[2] and EXTENT[1] <= miny < maxy <= EXTENT[3]
    assert "bbox-crs" not in query and "crs" not in query
    assert user.client.get.call_args.kwargs["name"] == "GetMap cosc"


def test_crs_3857_sends_uris():
    query, _ = run_map(make_user("--crs", "EPSG:3857"))
    assert query["bbox-crs"] == query["crs"] == crs_uri("EPSG:3857")
    minx, miny, maxx, maxy = bbox(query)
    ex = to_crs_box(EXTENT, "EPSG:3857")
    assert ex[0] <= minx < maxx <= ex[2] and ex[1] <= miny < maxy <= ex[3]


def test_bbox_areas_mix():
    user = make_user("--bbox-areas", "1,100")
    sizes = set()
    for _ in range(50):
        b = bbox(run_map(user)[0])
        sizes.add(round(b[3] - b[1], 6))  # heights in degrees: 1 km / 111.321 or 10 km / 111.321
    assert len(sizes) == 2


def test_user_rps_sets_constant_throughput(monkeypatch):
    monkeypatch.setattr(OGCAPIMapsBenchmark, "wait_time", OGCAPIMapsBenchmark.wait_time)
    before = OGCAPIMapsBenchmark.wait_time
    env = SimpleNamespace(parsed_options=default_options(ogcapi_maps.init_parser, "--user-rps", "2"))
    ogcapi_maps.on_init(env)
    assert OGCAPIMapsBenchmark.wait_time is not before


def test_blank_reference_marks_empty():
    user = make_user()
    _, resp = run_map(user, fake_resp(b"BLANK"))
    assert resp.request_meta["name"] == "GetMap cosc (empty)"
    resp.failure.assert_not_called()
    _, resp = run_map(user, fake_resp(b"IMG"))
    assert resp.request_meta["name"] == "GetMap cosc"


def test_reference_request_is_outside_the_extent():
    user = make_user()
    (url,), _ = [c for c in user.metadata_get.call_args_list if urlparse(c.args[0]).path.endswith("/map")][0]
    minx, miny, maxx, maxy = [float(v) for v in parse_qs(urlparse(url).query)["bbox"][0].split(",")]
    assert maxx <= EXTENT[0]  # just west of the extent: no data there


def test_reference_failure_disables_empty_check(caplog):
    caplog.set_level("INFO")
    user = make_user(get=answers(reference_status=500, reference_body=b"oops"))
    assert "no blank reference" in caplog.text
    _, resp = run_map(user, fake_resp(b"oops"))
    assert resp.request_meta["name"] == "GetMap cosc"


def test_unknown_collection_lists_map_collections():
    with pytest.raises(ArgError, match=r"\['cosc'\]"):
        make_user("--collection", "feat")


def test_extent_outside_refused():
    with pytest.raises(ArgError, match="does not overlap"):
        make_user("--extent", "100,10,101,11")


def test_unreachable_host_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPIMapsBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_maps.init_parser)
    env.runner.quit = Mock()
    user = OGCAPIMapsBenchmark(env)
    user.host = HOST
    with patch("utils.ogcapi.requests.get", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_tls_not_verified_by_default():
    assert all(c.kwargs["verify"] is False for c in make_user().metadata_get.call_args_list)
    assert all(c.kwargs["verify"] is True for c in make_user("--verify-tls").metadata_get.call_args_list)


def test_user_agent_sent():
    user = make_user()
    run_map(user)
    assert user.client.get.call_args.kwargs["headers"] == {
        "User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    }
    assert all(
        c.kwargs["headers"] == {"User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"}
        for c in user.metadata_get.call_args_list
    )
