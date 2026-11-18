"""Unit tests for ogcapi_features.py: collection and CRS choice, bbox axis order, limits, empty answers, failures."""

import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import gevent
import pytest
from locust.env import Environment

import ogcapi_features
from exceptions import ArgError
from ogcapi_features import OGCAPIFeaturesBenchmark, is_empty_collection
from utils import ogc
from utils.ogc import default_options
from utils.ogcapi import CRS84, crs_uri
from utils.tiles import to_crs_box

HOST = "http://example.com/api"
EXTENT = (-9.68, 36.77, -6.17, 42.15)
CRSS = [CRS84] + [crs_uri(f"EPSG:{c}") for c in (4326, 3857, 4258, 3763)]
COLLECTIONS = {
    "collections": [
        {"id": "maps-only", "links": [{"rel": "map", "type": "image/png", "href": f"{HOST}/m"}]},
        {
            "id": "cos",
            "links": [
                {"rel": "items", "type": "text/html", "href": f"{HOST}/collections/cos/items?f=html"},
                {"rel": "items", "type": "application/geo+json", "href": f"{HOST}/collections/cos/items?f=json"},
            ],
            "crs": CRSS,
            "extent": {"spatial": {"bbox": [list(EXTENT)]}},
        },
    ]
}


def make_user(*args, docs=None):
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPIFeaturesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_features.init_parser, *args)
    user = OGCAPIFeaturesBenchmark(env)
    user.host = HOST
    doc = docs or COLLECTIONS
    get = Mock(return_value=Mock(json=Mock(return_value=doc), raise_for_status=Mock()))
    with patch("utils.ogcapi.requests.get", get):
        user.on_start()
    user.client = MagicMock()
    return user


def fake_resp(content=b'{"type":"FeatureCollection","features":[{}],"numberReturned":1}'):
    return SimpleNamespace(
        status_code=200,
        headers={"content-type": "application/geo+json"},
        content=content,
        failure=Mock(),
        request_meta={"name": "GetFeature"},
    )


def run_features(user, resp=None):
    """One load_features(); returns the query parameters (single values), the URL path and the response."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_features()
    url = urlparse(user.client.get.call_args.args[0])
    return {k: v[0] for k, v in parse_qs(url.query).items()}, url.path, resp


def bbox(query):
    return [float(v) for v in query["bbox"].split(",")]


def test_default_crs84_sends_no_crs_params():
    user = make_user()
    query, path, _ = run_features(user)
    assert path == "/api/collections/cos/items"
    minx, miny, maxx, maxy = bbox(query)
    assert EXTENT[0] <= minx < maxx <= EXTENT[2] and EXTENT[1] <= miny < maxy <= EXTENT[3]
    assert (query["f"], query["limit"]) == ("json", "100")
    assert "bbox-crs" not in query and "crs" not in query
    assert user.client.get.call_args.kwargs["name"] == "GetFeature"


def test_crs_4326_lat_first_with_uris():
    query, _, _ = run_features(make_user("--crs", "EPSG:4326"))
    lat0, lon0, lat1, lon1 = bbox(query)
    assert EXTENT[1] <= lat0 < lat1 <= EXTENT[3] and EXTENT[0] <= lon0 < lon1 <= EXTENT[2]
    assert query["bbox-crs"] == query["crs"] == crs_uri("EPSG:4326")


def test_crs_3763_box_in_metres():
    query, _, _ = run_features(make_user("--crs", "EPSG:3763"))
    minx, miny, maxx, maxy = bbox(query)
    ex = to_crs_box(EXTENT, "EPSG:3763")
    assert ex[0] <= minx < maxx <= ex[2] and ex[1] <= miny < maxy <= ex[3]
    assert maxx - minx == pytest.approx(10_000, rel=0.01)  # 100 km2, square
    assert query["bbox-crs"] == crs_uri("EPSG:3763")


def test_crs_not_offered_refused():
    with pytest.raises(ArgError, match="EPSG/0/3763"):
        make_user("--crs", "EPSG:25829")


def test_limit_must_be_positive():
    with pytest.raises(ArgError, match="--limit"):
        make_user("--limit", "0")


def test_extent_outside_refused():
    with pytest.raises(ArgError, match="does not overlap"):
        make_user("--extent", "100,10,101,11")


def test_broken_extent_refused():
    docs = copy.deepcopy(COLLECTIONS)
    del docs["collections"][1]["extent"]
    with pytest.raises(ArgError, match="'cos'"):
        make_user(docs=docs)


def test_broken_extent_with_extent_option_uses_it():
    docs = copy.deepcopy(COLLECTIONS)
    del docs["collections"][1]["extent"]
    query, _, _ = run_features(make_user("--extent", "-9.3,38.6,-9.0,38.9", "--bbox-area", "1", docs=docs))
    minx, miny, maxx, maxy = bbox(query)
    assert -9.3 <= minx < maxx <= -9.0 and 38.6 <= miny < maxy <= 38.9


@pytest.mark.parametrize(
    "body, empty",
    [
        (b'{"type":"FeatureCollection","features":[],"numberReturned":0}', True),
        (b'{"type": "FeatureCollection", "features": [ ]}', True),
        (b'{"type":"FeatureCollection","features":[{}],"numberReturned":10}', False),
        (b'{"features":[' + b"0" * 70_000 + b"]}", False),
    ],
    ids=["numberReturned 0", "features []", "numberReturned 10", "large body"],
)
def test_empty_collection_marked(body, empty):
    assert is_empty_collection(body) is empty


def test_empty_response_renamed():
    _, _, resp = run_features(make_user(), fake_resp(b'{"features":[],"numberReturned":0}'))
    assert resp.request_meta["name"] == "GetFeature (empty)"
    resp.failure.assert_not_called()


def test_unreachable_host_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPIFeaturesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_features.init_parser)
    env.runner.quit = Mock()
    user = OGCAPIFeaturesBenchmark(env)
    user.host = HOST
    with patch("utils.ogcapi.requests.get", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_user_agent_sent():
    user = make_user()
    run_features(user)
    assert user.client.get.call_args.kwargs["headers"] == {
        "User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    }
