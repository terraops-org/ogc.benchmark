"""Live tests against public PDOK services (network required): real GetCapabilities and real requests.

A test is skipped only when the service cannot be reached (connection error, timeout). Any other
problem, an HTTP 404 or 500 included, fails it: a live test that cannot fail tests nothing.
"""

import re

import pytest
import requests
from locust.env import Environment

import wfs
import wms
import wmts
from exceptions import ArgError
from utils import ogc
from utils.ogc import default_options

PDOK_WMS = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
PDOK_WMTS = "https://service.pdok.nl/lv/bgt/wmts/v1_0"
PDOK_ORTHO_WMTS = "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0"
PDOK_WFS = "https://service.pdok.nl/lv/bag/wfs/v2_0"
UNREACHABLE = (requests.ConnectionError, requests.Timeout)

pytestmark = [pytest.mark.network, pytest.mark.slow]


def make_user(module, user_class, host, *args):
    """A user of `user_class` on `host`, plus a list that collects (name, status, exception, content) per request."""
    ogc._CAPS.clear()
    env = Environment(user_classes=[user_class])
    env.create_local_runner()
    env.parsed_options = default_options(module.init_parser, *args)
    seen = []
    env.events.request.add_listener(
        lambda name, response, exception, **kw: seen.append((name, response.status_code, exception, response.content))
    )
    user = user_class(env)
    user.host = host
    return user, seen


def start_or_skip(user):
    """on_start(), skipping the test only when the service is unreachable (the cause of the ArgError)."""
    try:
        user.on_start()
    except ArgError as error:
        if isinstance(error.__context__, UNREACHABLE):
            pytest.skip(f"{user.host} unreachable: {error.__context__}")
        raise


def test_wmts_capabilities():
    user, _ = make_user(wmts, wmts.WMTSBenchmark, PDOK_WMTS)
    start_or_skip(user)
    assert user.layers and user.tile_matrix_set and user.layer_width > 0 and user.layer_height > 0


def test_wmts_tile_request():
    """One real GetTile: HTTP 200 with an image, counted as a success."""
    user, seen = make_user(wmts, wmts.WMTSBenchmark, PDOK_WMTS)
    start_or_skip(user)
    user.load_tile()
    ((name, status, exception, _),) = seen
    assert name in ("GetTile", "GetTile (empty)") and (status, exception) == (200, None)


def test_wmts_probe_finds_images():
    """The start-up probe on an orthophoto WMTS: some coarse tiles have images, not all; most draws land on images."""
    user, seen = make_user(
        wmts,
        wmts.WMTSBenchmark,
        PDOK_ORTHO_WMTS,
        "--layer-name",
        "Actueel_orthoHR",
        "--tile-matrix-set",
        "EPSG:28992",
        "--tile-matrix",
        "10",
    )
    start_or_skip(user)
    assert 0 < len(user.coverage.tiles) < user.coverage.fetched
    for _ in range(20):
        user.load_tile()
    images = sum(1 for name, status, exception, _ in seen if name == "GetTile" and status == 200 and not exception)
    print(f"{images} of 20 tiles with images")
    assert images >= 5


def test_wms_capabilities():
    """The aerial photo WMS: layers found, an extent with a CRS picked."""
    user, _ = make_user(wms, wms.WMSBenchmark, PDOK_WMS)
    start_or_skip(user)
    minx, miny, maxx, maxy, crs = user.extents[user.layer_names[0]]
    assert user.layers and minx < maxx and miny < maxy and crs


def test_wms_map_request():
    """One real GetMap on Actueel_orthoHR: HTTP 200 with an image, counted as a success."""
    user, seen = make_user(wms, wms.WMSBenchmark, PDOK_WMS, "--layer-name", "Actueel_orthoHR", "--bbox-area", "1")
    start_or_skip(user)
    user.load_map()
    assert [(n, s, e) for n, s, e, _ in seen] == [("GetMap Actueel_orthoHR", 200, None)]


def test_wfs_capabilities():
    """PDOK BAG: feature types found, the first one has a lon/lat extent."""
    user, _ = make_user(wfs, wfs.WFSBenchmark, PDOK_WFS)
    start_or_skip(user)
    minx, miny, maxx, maxy = user.extent
    assert user.feature_names and -180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])  # PDOK BAG does not offer WFS 1.0.0
def test_wfs_features_returned(version):
    """1 km2 of central Amsterdam: the GetFeature succeeds AND holds buildings (a CRS-less bbox found none)."""
    user, seen = make_user(
        wfs, wfs.WFSBenchmark, PDOK_WFS, "--feature-name", "bag:pand", "--bbox-area", "1", "--wfs-version", version
    )
    start_or_skip(user)
    user.extent = (4.88, 52.36, 4.90, 52.38)
    user.load_features()
    ((_, status, exception, content),) = seen
    assert (status, exception) == (200, None)
    assert feature_count(content) > 0


def feature_count(content):
    """Features in a GetFeature response: numberReturned (2.0.0), numberOfFeatures (1.1.0, when present),
    else the member elements (MapServer's 1.1.0 GML has no count attribute)."""
    count = re.search(rb'number(?:Returned|OfFeatures)="(\d+)"', content)
    return int(count.group(1)) if count else len(re.findall(rb"<(?:gml:featureMember|wfs:member)\b", content))


@pytest.mark.parametrize("url", [PDOK_WMS + "?service=WMS", PDOK_WMTS + "?service=WMTS", PDOK_WFS + "?service=WFS"])
def test_service_availability(url):
    """Each service answers GetCapabilities with XML; one service failing does not hide the others."""
    try:
        response = requests.get(url + "&request=GetCapabilities", timeout=30)
    except UNREACHABLE as error:
        pytest.skip(f"unreachable: {error}")
    assert response.status_code == 200
    assert "xml" in response.headers.get("content-type", "").lower()
