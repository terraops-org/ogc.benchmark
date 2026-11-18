"""Live tests against the public pygeoapi demo (network required): real OGC API metadata and real requests.

Skipped only when the demo cannot be reached; any other problem fails the test (see test_live_services.py).
"""

import pytest

import ogcapi_tiles
from tests.test_live_services import make_user, start_or_skip

DEMO = "https://demo.pygeoapi.io/master"

pytestmark = [pytest.mark.network, pytest.mark.slow]


def test_ogcapi_tile_request():
    """One real vector tile (world lakes, zoom level 2): a tile or an empty tile, never a failure."""
    user, seen = make_user(
        ogcapi_tiles, ogcapi_tiles.OGCAPITilesBenchmark, DEMO, "--collection", "lakes", "--tile-matrix", "2"
    )
    start_or_skip(user)
    user.load_tile()
    ((name, status, exception, _),) = seen
    assert (name, status) in {("GetTile", 200), ("GetTile (empty)", 200), ("GetTile (empty)", 204)}
    assert exception is None
