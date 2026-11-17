"""What the three locustfiles share: response checks, CRS facts, option defaults, per-user random streams."""

import itertools
import random
from functools import lru_cache

from locust.argument_parser import LocustArgumentParser
from pyproj import CRS
from pyproj.exceptions import CRSError

IMAGE = ("image/",)
CAPABILITIES = ("text/xml", "application/xml", "application/vnd.ogc.wms_xml")
FEATURES = CAPABILITIES + ("application/gml+xml", "application/json", "application/geo+json")

_USER_IDS = itertools.count()  # one number per simulated user in this process


def check_response(resp, accept):
    """Mark a catch_response response as failed unless it is HTTP 200 with an accepted content type.

    Locust groups failures by their exact message, so every message is a fixed string: never the body
    or the URL (a server that echoes the bbox would otherwise create one error row per request).
    """
    code = resp.status_code
    ctype = (resp.headers or {}).get("content-type", "").lower()
    if not code:  # timeout, refused connection, DNS or TLS failure: no HTTP answer, headers None
        resp.failure("Connection error")
    elif code in (502, 503, 504):
        resp.failure(f"Server Overload: HTTP {code}")
    elif code != 200:
        resp.failure(f"OGC Error: HTTP {code}" if "xml" in ctype else f"HTTP {code}")
    elif "xml" in ctype and b"Exception" in (resp.content or b"")[:1000]:
        resp.failure("OGC Exception")
    elif not ctype.startswith(accept):
        resp.failure(f"Invalid content type: {ctype.split(';')[0].strip()}")


def _crs(code):
    try:
        return CRS.from_user_input(code)
    except CRSError:
        return None


@lru_cache(maxsize=None)
def is_metric(code):
    """True for a projected CRS whose first axis is in metres (bbox areas are then km2 of map units)."""
    crs = _crs(code)
    return bool(crs and crs.is_projected and crs.axis_info and crs.axis_info[0].unit_name in ("metre", "meter"))


@lru_cache(maxsize=None)
def is_yx(code):
    """True when the CRS's first axis is northing/latitude: WMS 1.3.0 then wants the bbox as miny,minx,maxy,maxx."""
    crs = _crs(code)
    return bool(crs and crs.axis_info and crs.axis_info[0].direction.lower() in ("north", "south"))


def default_options(init_parser, *args):
    """The options a locustfile's init_parser defines, parsed from args (defaults and LOCUST_* env vars)."""
    parser = LocustArgumentParser()
    init_parser(parser)
    return parser.parse_args(list(args))


def user_rng(environment, seed):
    """A random stream of its own for each simulated user, distinct across users and worker processes."""
    worker = getattr(environment.runner, "worker_index", 0)
    return random.Random(f"{seed}-{worker}-{next(_USER_IDS)}")
