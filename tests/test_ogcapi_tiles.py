"""Unit tests for ogcapi_tiles.py: collection / tileset / level choice, tile URLs, ranges, empty tiles, failures."""

import copy
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlparse

import gevent
import pytest
from locust.env import Environment

import ogcapi_tiles
from exceptions import ArgError
from ogcapi_tiles import OGCAPITilesBenchmark
from utils import ogc
from utils.ogc import default_options
from utils.tiles import tile_range, tms_matrix, to_crs_box

HOST = "http://example.com/api"
EXTENT = (-9.68, 36.77, -6.17, 42.15)
HALF = 20037508.3427892
MERC = "http://www.opengis.net/def/crs/EPSG/0/3857"


def level(z):
    return {
        "id": str(z),
        "cellSize": 156543.033928041 / 2**z,
        "pointOfOrigin": [-HALF, HALF],
        "tileWidth": 256,
        "tileHeight": 256,
        "matrixWidth": 2**z,
        "matrixHeight": 2**z,
    }


Z12 = tms_matrix(level(12), MERC)

DOCS = {
    "/api/collections": {
        "collections": [
            {"id": "plain", "links": [{"rel": "items", "type": "application/geo+json", "href": f"{HOST}/x"}]},
            {
                "id": "cos",
                "links": [
                    {
                        "rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
                        "type": "application/json",
                        "href": f"{HOST}/collections/cos/tiles",
                    }
                ],
                "extent": {"spatial": {"bbox": [list(EXTENT)]}},
            },
        ]
    },
    "/api/collections/cos/tiles": {
        "tilesets": [
            {
                "tileMatrixSetURI": "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
                "dataType": "vector",
                "links": [
                    {"rel": "self", "type": "application/json", "href": f"{HOST}/collections/cos/tiles/WebMercatorQuad"}
                ],
            }
        ]
    },
    "/api/collections/cos/tiles/WebMercatorQuad": {
        "links": [
            {
                "rel": "http://www.opengis.net/def/rel/ogc/1.0/tiling-scheme",
                "type": "application/json",
                "href": f"{HOST}/TileMatrixSets/WebMercatorQuad",
            },
            {
                "rel": "item",
                "type": "application/vnd.mapbox-vector-tile",
                "href": f"{HOST}/collections/cos/tiles/WebMercatorQuad/{{tileMatrix}}/{{tileRow}}/{{tileCol}}?f=mvt",
            },
        ]
    },
    "/api/TileMatrixSets/WebMercatorQuad": {"crs": MERC, "tileMatrices": [level(z) for z in range(25)]},
}


def json_get(docs):
    def get(url, **kwargs):
        doc = docs[urlparse(url).path]
        return Mock(json=Mock(return_value=doc), raise_for_status=Mock())

    return Mock(side_effect=get)


def make_user(*args, docs=None, get=None):
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPITilesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_tiles.init_parser, *args)
    user = OGCAPITilesBenchmark(env)
    user.host = HOST
    user.metadata_get = get or json_get(docs or DOCS)
    with patch("utils.ogcapi.requests.get", user.metadata_get):
        user.on_start()
    user.client = MagicMock()
    return user


def fake_resp(status=200, content=b"\x1a\x02mvt", ctype="application/vnd.mapbox-vector-tile"):
    return SimpleNamespace(
        status_code=status,
        headers={"content-type": ctype},
        content=content,
        failure=Mock(),
        request_meta={"name": "GetTile"},
    )


def run_tile(user, resp=None):
    """One load_tile(); returns (matrix, row, col) from the tile URL, and the response it checked."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_tile()
    url = user.client.get.call_args.args[0]
    m = re.search(r"/collections/cos/tiles/WebMercatorQuad/(\d+)/(\d+)/(\d+)\?f=mvt$", url)
    assert m, url
    return tuple(int(v) for v in m.groups()), resp


def with_changes(change):
    docs = copy.deepcopy(DOCS)
    change(docs)
    return docs


def test_defaults_first_tiled_collection_and_middle_level():
    user = make_user()
    assert (user.collection, user.target.id) == ("cos", "12")


def test_tile_url_from_template():
    user = make_user()
    c0, c1, r0, r1 = tile_range(Z12, to_crs_box(EXTENT, "EPSG:3857"))
    for _ in range(20):
        (z, row, col), _ = run_tile(user)
        assert z == 12 and c0 <= col <= c1 and r0 <= row <= r1


def test_extent_narrows_draws():
    user = make_user("--extent", "-9.3,38.6,-9.0,38.9")
    c0, c1, r0, r1 = tile_range(Z12, to_crs_box((-9.3, 38.6, -9.0, 38.9), "EPSG:3857"))
    for _ in range(50):
        (_, row, col), _ = run_tile(user)
        assert c0 <= col <= c1 and r0 <= row <= r1


@pytest.mark.parametrize("status, body", [(204, b""), (200, b"")])
def test_empty_tile_reported(status, body):
    _, resp = run_tile(
        make_user(), fake_resp(status, body, "" if status == 204 else "application/vnd.mapbox-vector-tile")
    )
    assert resp.request_meta["name"] == "GetTile (empty)"
    resp.failure.assert_not_called()


def test_tile_with_data_keeps_its_name():
    _, resp = run_tile(make_user())
    assert resp.request_meta["name"] == "GetTile"


def test_unknown_collection_lists_tiled_ones():
    with pytest.raises(ArgError, match=r"\['cos'\]"):
        make_user("--collection", "plain")


def test_map_tileset_refused():
    def change(docs):
        docs["/api/collections/cos/tiles"]["tilesets"][0]["dataType"] = "map"

    with pytest.raises(ArgError, match="only vector tilesets are supported"):
        make_user(docs=with_changes(change))


def test_limits_used_when_given():
    limits = [{"tileMatrix": "12", "minTileRow": 1500, "maxTileRow": 1501, "minTileCol": 1940, "maxTileCol": 1941}]

    def change(docs):
        docs["/api/collections/cos/tiles/WebMercatorQuad"]["tileMatrixSetLimits"] = limits

    user = make_user(docs=with_changes(change))
    for _ in range(30):
        (_, row, col), _ = run_tile(user)
        assert 1940 <= col <= 1941 and 1500 <= row <= 1501


def test_world_extent_does_not_crash():
    def change(docs):
        docs["/api/collections"]["collections"][1]["extent"]["spatial"]["bbox"] = [[-180, -90, 180, 90]]

    assert make_user(docs=with_changes(change)).range == (0, 4095, 0, 4095)


def test_extent_outside_refused():
    with pytest.raises(ArgError, match="does not overlap"):
        make_user("--extent", "100,10,101,11")


def test_auth_header_sent():
    user = make_user("--auth-user", "u", "--auth-password", "p")
    assert all(c.kwargs["headers"] == {"Authorization": "Basic dTpw"} for c in user.metadata_get.call_args_list)
    run_tile(user)
    assert user.client.get.call_args.kwargs["headers"] == {"Authorization": "Basic dTpw"}


def test_unreachable_host_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[OGCAPITilesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(ogcapi_tiles.init_parser)
    env.runner.quit = Mock()
    user = OGCAPITilesBenchmark(env)
    user.host = HOST
    with patch("utils.ogcapi.requests.get", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)  # quit() runs in its own greenlet
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_metadata_fetched_once_per_process():
    first = make_user()
    user = OGCAPITilesBenchmark(first.environment)
    user.host = HOST
    with patch("utils.ogcapi.requests.get") as get:
        user.on_start()
    get.assert_not_called()
