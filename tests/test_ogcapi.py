"""Unit tests for utils/ogcapi.py: CRS URIs, cached JSON, collections, links, extents."""

from unittest.mock import Mock, patch

import pytest

from utils import ogc
from utils.ogc import is_metric, is_yx
from utils.ogcapi import (
    CRS84,
    cached_json,
    collections,
    crs_uri,
    link,
    lonlat_area,
    lonlat_extent,
    pick_collection,
)


@pytest.fixture(autouse=True)
def fresh_cache():
    ogc._CAPS.clear()


def json_get(payload):
    return Mock(return_value=Mock(json=Mock(return_value=payload), raise_for_status=Mock()))


def test_crs_uri():
    assert crs_uri("EPSG:3763") == "http://www.opengis.net/def/crs/EPSG/0/3763"
    assert crs_uri("CRS:84") == crs_uri("OGC:CRS84") == crs_uri(CRS84) == CRS84
    assert crs_uri("http://www.opengis.net/def/crs/EPSG/0/4258") == "http://www.opengis.net/def/crs/EPSG/0/4258"


def test_collections_url_trailing_slash():
    get = json_get({"collections": [{"id": "a"}]})
    with patch("utils.ogcapi.requests.get", get):
        assert collections("https://x/api/", {}) == [{"id": "a"}]
    assert get.call_args.args[0] == "https://x/api/collections?f=json"


def test_cached_json_fetched_once():
    get = json_get({"ok": 1})
    with patch("utils.ogcapi.requests.get", get):
        assert cached_json("https://x/api/collections/a", {}) == cached_json("https://x/api/collections/a", {})
    assert get.call_count == 1


COL = {
    "id": "a",
    "links": [
        {"rel": "items", "type": "text/html", "href": "https://x/items?f=html"},
        {"rel": "items", "type": "application/geo+json", "href": "https://x/items?f=json"},
        {
            "rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
            "type": "application/json",
            "href": "https://x/t",
        },
    ],
    "extent": {
        "spatial": {"bbox": [[-9.68, 36.77, -6.17, 42.15]], "crs": "http://www.opengis.net/def/crs/EPSG/0/3763"}
    },
}


def test_link_matches_rel_suffix_and_type():
    assert link(COL, "tilesets-vector") == "https://x/t"
    assert link(COL, "items", "application/geo+json") == "https://x/items?f=json"
    assert link(COL, "items") == "https://x/items?f=html"
    assert link(COL, "map") is None


def test_pick_collection():
    cols = [{"id": "plain", "links": []}, COL]
    assert pick_collection(cols, None, "tilesets-vector") is COL
    assert pick_collection(cols, "a", "items") is COL
    with pytest.raises(ValueError, match=r"Collections with tilesets-vector: \['a'\]"):
        pick_collection(cols, "plain", "tilesets-vector")
    with pytest.raises(ValueError, match="'nope'"):
        pick_collection(cols, "nope", "items")


def test_lonlat_extent_errors():
    assert lonlat_extent(COL) == (-9.68, 36.77, -6.17, 42.15)  # read as CRS84 whatever crs it declares
    for broken in ({"id": "b"}, {**COL, "id": "b", "extent": {"spatial": {"bbox": [[0, 0, 500000, 400000]]}}}):
        with pytest.raises(ValueError, match="'b'"):
            lonlat_extent(broken)
    with pytest.raises(ValueError, match="'b'"):
        lonlat_extent({**COL, "id": "b", "extent": {"spatial": {"bbox": [[5, 40, 4, 41]]}}})


def test_pyproj_reads_ogc_uris():
    assert not is_yx(CRS84)
    assert is_yx(crs_uri("EPSG:4326"))
    assert is_metric(crs_uri("EPSG:3857"))


def test_lonlat_area():
    assert lonlat_area(COL, "") == (-9.68, 36.77, -6.17, 42.15)
    assert lonlat_area(COL, "-10,38,-9,39") == (-9.68, 38.0, -9.0, 39.0)
    with pytest.raises(ValueError, match="does not overlap"):
        lonlat_area(COL, "100,10,101,11")
    broken = {"id": "b"}
    assert lonlat_area(broken, "-10,38,-9,39") == (-10.0, 38.0, -9.0, 39.0)  # --extent alone bounds the test
    with pytest.raises(ValueError, match="'b'"):
        lonlat_area(broken, "")
