"""Unit tests for utils/tiles.py: tile ranges, grids from WMTS and OGC API, the coverage probe and the draws."""

import math
import random
from types import SimpleNamespace

import pytest

from utils.tiles import (
    Coverage,
    Matrix,
    digest,
    draw_tile,
    find_coverage,
    probe_level,
    tile_box,
    tile_range,
    tms_matrix,
    to_crs_box,
    wmts_matrix,
)

HALF = 20037508.3427892  # half the Web Mercator world, metres
WORLD = (-HALF, -HALF, HALF, HALF)
Z12 = Matrix("12", -HALF, HALF, 156543.033928041 / 4096 * 256, 156543.033928041 / 4096 * 256, 4096, 4096)


def quadtree(n):
    """WebMercatorQuad levels 0..n."""
    return [Matrix(str(z), -HALF, HALF, 2 * HALF / 2**z, 2 * HALF / 2**z, 2**z, 2**z) for z in range(n + 1)]


def test_lisbon_tile_z12():
    assert tile_range(Z12, to_crs_box((-9.14, 38.74, -9.14, 38.74), "EPSG:3857")) == (1944, 1944, 1569, 1569)


def test_wmts_grid_in_metres_from_a_wgs84_box():  # an EPSG:3763 grid, level 10
    tm = SimpleNamespace(
        identifier="10",
        topleftcorner=(-170000.0, 290000.0),
        scaledenominator=8378.7100655692,
        tilewidth=256,
        tileheight=256,
        matrixwidth=583,
        matrixheight=1024,
    )
    m = wmts_matrix(tm, "urn:ogc:def:crs:EPSG::3763")
    box = to_crs_box((-9.68238860216429, 36.905606097388144, -6.099938081309822, 42.189331450390036), "EPSG:3763")
    assert tile_range(m, box) == (53, 582, 13, 993)


def test_yx_origin_swapped():
    tm = SimpleNamespace(
        identifier="0",
        topleftcorner=(90.0, -180.0),
        scaledenominator=279541132.0143589,
        tilewidth=256,
        tileheight=256,
        matrixwidth=2,
        matrixheight=1,
    )
    m = wmts_matrix(tm, "EPSG:4326")
    assert (m.x0, m.y0) == (-180.0, 90.0)
    assert m.span_x == pytest.approx(256 * 279541132.0143589 * 0.00028 / 111319.49079327357)


def test_tms_matrix():
    level = {
        "id": "0",
        "cellSize": 156543.033928041,
        "pointOfOrigin": [-HALF, HALF],
        "tileWidth": 256,
        "tileHeight": 256,
        "matrixWidth": 1,
        "matrixHeight": 1,
    }
    m = tms_matrix(level, "http://www.opengis.net/def/crs/EPSG/0/3857")
    assert (m.id, m.x0, m.y0, m.width, m.height) == ("0", -HALF, HALF, 1, 1)
    assert m.span_x == m.span_y == 256 * 156543.033928041
    with pytest.raises(ValueError):
        tms_matrix({**level, "cornerOfOrigin": "bottomLeft"}, "http://www.opengis.net/def/crs/EPSG/0/3857")


def test_world_extent_in_web_mercator():
    box = to_crs_box((-180, -90, 180, 90), "EPSG:3857")
    assert all(math.isfinite(v) for v in box)
    assert tile_range(quadtree(2)[2], box) == (0, 3, 0, 3)


def test_range_clamped_and_outside():
    z2 = quadtree(2)[2]
    assert tile_range(z2, (-3 * HALF, -3 * HALF, 3 * HALF, 3 * HALF)) == (0, 3, 0, 3)
    assert tile_range(z2, (1.5 * HALF, 0, 2 * HALF, HALF)) is None


def test_probe_level_picks_deepest_within_budget():
    levels = quadtree(6)
    assert probe_level(levels, levels[6], WORLD).id == "4"  # 16 x 16 = 256 tiles; level 5 has 1024
    assert probe_level(levels, levels[3], WORLD).id == "3"  # the target fits: probe it, exact mask


def grid_fetch(bodies):
    """A probe fetch answering from {(col, row): (status, body) or an exception}."""

    def fetch(col, row):
        answer = bodies[(col, row)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


GRID = Matrix("g", 0.0, 2.0, 1.0, 1.0, 4, 2)  # 4 x 2 tiles of 1 x 1, top-left (0, 2)


def test_find_coverage_blank_is_most_frequent():
    bodies = {(c, r): (200, b"blank") for c in range(4) for r in range(2)}
    bodies[(1, 0)] = (200, b"a")
    bodies[(2, 1)] = (200, b"b")
    bodies[(3, 1)] = (204, b"")
    cov = find_coverage(grid_fetch(bodies), GRID, (0, 0, 4, 2))
    assert cov.tiles == ((1, 0), (2, 1))
    assert cov.blank == digest(b"blank")
    assert cov.fetched == 8 and cov.level == GRID


def test_find_coverage_failures_are_not_images():
    bodies = {(0, 0): TimeoutError("slow"), (1, 0): (500, b"oops"), (2, 0): (200, b"a"), (3, 0): (200, b"b")}
    cov = find_coverage(grid_fetch(bodies), GRID, (0, 1, 4, 2))
    assert cov.tiles == ((2, 0), (3, 0))
    assert cov.fetched == 4 and cov.blank is None


def test_find_coverage_all_distinct():
    bodies = {(c, 0): (200, bytes([c])) for c in range(4)}
    cov = find_coverage(grid_fetch(bodies), GRID, (0, 1, 4, 2))
    assert cov.blank is None and cov.tiles == ((0, 0), (1, 0), (2, 0), (3, 0))


def test_draws_stay_under_image_tiles():
    levels = quadtree(4)
    cov = Coverage(levels[2], ((1, 2),), None, 16)
    rng = random.Random(1640)
    draws = {draw_tile(rng, levels[4], WORLD, cov) for _ in range(200)}
    assert all(4 <= col <= 7 and 8 <= row <= 11 for col, row in draws)
    assert len(draws) > 8  # spread over the 4 x 4 children, not one tile


def test_draw_without_image_tiles_uses_area():
    z4 = quadtree(4)[4]
    area = tile_box(z4, 3, 9)[:2] + tile_box(z4, 6, 5)[2:]  # lower-left of tile (3, 9) to upper-right of (6, 5)
    expected = (3, 6, 5, 9)
    assert tile_range(z4, area) == expected
    rng = random.Random(1640)
    for coverage in (None, Coverage(quadtree(2)[2], (), None, 16)):
        for _ in range(100):
            col, row = draw_tile(rng, z4, area, coverage)
            assert expected[0] <= col <= expected[1] and expected[2] <= row <= expected[3]


def test_draws_repeat_with_the_seed():
    levels = quadtree(4)
    cov = Coverage(levels[2], ((1, 2), (3, 0)), None, 16)
    a, b = random.Random(1640), random.Random(1640)
    assert [draw_tile(a, levels[4], WORLD, cov) for _ in range(20)] == [
        draw_tile(b, levels[4], WORLD, cov) for _ in range(20)
    ]


def test_limits_round_trip_on_non_round_spans():
    """TileMatrixSetLimits -> box -> tile_range gives the limits back on GeoWebCache-style grids (I1)."""
    rng = random.Random(1)
    for z in range(1, 19):
        tm = SimpleNamespace(
            identifier=f"EPSG:900913:{z}",
            topleftcorner=(-20037508.34, 20037508.34),
            scaledenominator=559082264.0287178 / 2**z,
            tilewidth=256,
            tileheight=256,
            matrixwidth=2**z,
            matrixheight=2**z,
        )
        m = wmts_matrix(tm, "EPSG:3857")
        for _ in range(50):
            c0 = rng.randint(0, m.width - 1)
            c1 = rng.randint(c0, m.width - 1)
            r0 = rng.randint(0, m.height - 1)
            r1 = rng.randint(r0, m.height - 1)
            low, high = tile_box(m, c0, r1), tile_box(m, c1, r0)
            assert tile_range(m, (low[0], low[1], high[2], high[3])) == (c0, c1, r0, r1), (z, c0, c1, r0, r1)


def test_unknown_crs_is_taken_as_metres():
    tm = SimpleNamespace(
        identifier="0",
        topleftcorner=(-20037508.34, 20037508.34),
        scaledenominator=559082264.0287178,
        tilewidth=256,
        tileheight=256,
        matrixwidth=1,
        matrixheight=1,
    )
    m = wmts_matrix(tm, "urn:ogc:def:crs:EPSG::102100")  # ArcGIS Web Mercator id that pyproj does not know
    assert m.span_x == pytest.approx(256 * 559082264.0287178 * 0.00028)
