"""Tile grids (WMTS and OGC API TileMatrixSets), tile ranges, and the start-up probe for where the images are."""

import collections
import hashlib
import math
from dataclasses import dataclass

from gevent.pool import Pool
from pyproj import CRS, Transformer

from utils.ogc import is_metric, is_yx, intersect

METRES_PER_DEGREE = 2 * math.pi * 6378137 / 360  # the WMTS standard's value for degree CRSs
PIXEL_SIZE = 0.00028  # metres per pixel in WMTS scale denominators
MERCATOR_LAT = 85.0511287798


@dataclass(frozen=True)
class Matrix:
    """One zoom level: top-left origin (x/y), tile size in CRS units, size in tiles."""

    id: str
    x0: float
    y0: float
    span_x: float
    span_y: float
    width: int
    height: int


@dataclass(frozen=True)
class Coverage:
    """What the probe found: the image tiles (col, row) of its level, and the digest of the blank tile body."""

    level: Matrix
    tiles: tuple
    blank: bytes | None
    fetched: int


def wmts_matrix(tm, crs):
    """A Matrix from an OWSLib TileMatrix; topleftcorner comes in the CRS's axis order (lat/lon for EPSG:4326)."""
    per_unit = 1.0 if is_metric(crs) else METRES_PER_DEGREE
    x0, y0 = tm.topleftcorner[::-1] if is_yx(crs) else tm.topleftcorner
    return Matrix(
        str(tm.identifier),
        float(x0),
        float(y0),
        tm.tilewidth * tm.scaledenominator * PIXEL_SIZE / per_unit,
        tm.tileheight * tm.scaledenominator * PIXEL_SIZE / per_unit,
        int(tm.matrixwidth),
        int(tm.matrixheight),
    )


def tms_matrix(level, crs):
    """A Matrix from one level of an OGC TileMatrixSet JSON (cellSize in CRS units per pixel)."""
    if level.get("cornerOfOrigin", "topLeft") != "topLeft":
        raise ValueError(f"tile matrix {level.get('id')}: cornerOfOrigin {level['cornerOfOrigin']} is not supported")
    x0, y0 = level["pointOfOrigin"][::-1] if is_yx(crs) else level["pointOfOrigin"]
    return Matrix(
        str(level["id"]),
        float(x0),
        float(y0),
        level["cellSize"] * level["tileWidth"],
        level["cellSize"] * level["tileHeight"],
        int(level["matrixWidth"]),
        int(level["matrixHeight"]),
    )


def to_crs_box(lonlat_box, crs):
    """A WGS84 lon/lat box in `crs`, x/y order; latitudes clamped to Web Mercator's limits for EPSG:3857."""
    minx, miny, maxx, maxy = lonlat_box
    if CRS.from_user_input(crs).to_epsg() == 3857:
        miny, maxy = max(miny, -MERCATOR_LAT), min(maxy, MERCATOR_LAT)
    return Transformer.from_crs("OGC:CRS84", crs, always_xy=True).transform_bounds(minx, miny, maxx, maxy)


def tile_range(m, box):
    """(mincol, maxcol, minrow, maxrow) of the tiles a box (grid CRS, x/y) touches, clamped; None if it misses."""
    minx, miny, maxx, maxy = box
    eps = 1e-9  # a box edge on a tile edge does not reach into the next tile
    mincol = math.floor((minx - m.x0) / m.span_x)
    maxcol = max(mincol, math.ceil((maxx - m.x0) / m.span_x - eps) - 1)
    minrow = math.floor((m.y0 - maxy) / m.span_y)
    maxrow = max(minrow, math.ceil((m.y0 - miny) / m.span_y - eps) - 1)
    if mincol > m.width - 1 or maxcol < 0 or minrow > m.height - 1 or maxrow < 0:
        return None
    return max(mincol, 0), min(maxcol, m.width - 1), max(minrow, 0), min(maxrow, m.height - 1)


def tile_box(m, col, row):
    """(minx, miny, maxx, maxy) of one tile, grid CRS."""
    x, y = m.x0 + col * m.span_x, m.y0 - row * m.span_y
    return x, y - m.span_y, x + m.span_x, y


def count(r):
    return (r[1] - r[0] + 1) * (r[3] - r[2] + 1)


def probe_level(levels, target, box, max_tiles=256):
    """The deepest level up to `target` whose tiles inside `box` number at most max_tiles (the target if it fits)."""
    # ponytail: one probe level of at most 256 tiles; coast tiles are half sea, so about half the draws hit images.
    # Upgrade path: descend one more level under the image tiles to sharpen the mask.
    best = None
    for m in levels[: levels.index(target) + 1]:
        r = tile_range(m, box)
        if r and count(r) <= max_tiles:
            best = m
    return best


def digest(body):
    return hashlib.md5(body, usedforsecurity=False).digest()


def find_coverage(fetch, level, box, pool_size=8):
    """
    Fetch every tile of `level` inside `box` once (fetch(col, row) -> (status, body)) and sort out the image tiles.

    A failed fetch or a status other than 200/204 is no image; a 204 or an empty body is blank; among the 200
    bodies the most frequent one, if it occurs more than once, is the blank tile (blank tiles are byte-identical).
    """
    c0, c1, r0, r1 = tile_range(level, box)
    keys = [(col, row) for row in range(r0, r1 + 1) for col in range(c0, c1 + 1)]

    def get(key):
        try:
            return fetch(*key)
        except Exception:
            return None, b""

    answers = Pool(pool_size).map(get, keys)
    bodies = {k: digest(body) for k, (status, body) in zip(keys, answers) if status == 200 and body}
    common = collections.Counter(bodies.values()).most_common(1)
    blank = common[0][0] if common and common[0][1] > 1 else None
    tiles = tuple(k for k in keys if k in bodies and bodies[k] != blank)
    return Coverage(level, tiles, blank, len(keys))


def draw_tile(rng, target, box, coverage):
    """A random (col, row) of `target` inside `box`: under one of the probe's image tiles when there are any."""
    r = tile_range(target, box)
    if coverage and coverage.tiles:
        sub = intersect(tile_box(coverage.level, *rng.choice(coverage.tiles)), box)
        r = (sub and tile_range(target, sub)) or r
    return rng.randint(r[0], r[1]), rng.randint(r[2], r[3])
