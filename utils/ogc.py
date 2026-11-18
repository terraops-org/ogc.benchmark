"""What the locustfiles share: response checks, CRS facts, shared options, per-user random streams."""

import itertools
import logging
import random
from base64 import b64encode
from functools import lru_cache
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import urllib3
from gevent.lock import BoundedSemaphore

from exceptions import ArgError
from locust import events
from locust.runners import MasterRunner
from locust.argument_parser import LocustArgumentParser
from owslib.util import Authentication
from pyproj import CRS
from pyproj.exceptions import CRSError

IMAGE = ("image/",)
CAPABILITIES = ("text/xml", "application/xml", "application/vnd.ogc.wms_xml")
FEATURES = CAPABILITIES + ("application/gml+xml", "application/json", "application/geo+json")

_USER_IDS = itertools.count()  # one number per simulated user in this process
_CAPS = {}  # parsed GetCapabilities per (service, host, ...) key, once per process
_CAPS_LOCK = BoundedSemaphore()  # users spawned together wait for the first fetch instead of repeating it
logger = logging.getLogger(__name__)
USER_AGENT = "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"  # on every request
# TLS certificates are not checked by default (--verify-tls): one warning per request would flood the log
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def check_response(resp, accept, empty_ok=False):
    """Mark a catch_response response as failed unless it is HTTP 200 with an accepted content type; True if not failed.

    Locust groups failures by their exact message, so every message is a fixed string: never the body
    or the URL (a server that echoes the bbox would otherwise create one error row per request).
    With empty_ok, a 204 (No Content, an empty tile) is a success; the caller reports it with mark_empty().
    """
    code = resp.status_code
    ctype = (resp.headers or {}).get("content-type", "").lower()
    if empty_ok and code == 204:
        return True
    if not code:  # timeout, refused connection, DNS or TLS failure: no HTTP answer, headers None
        resp.failure("Connection error")
    elif code in (502, 503, 504):
        resp.failure(f"Server Overload: HTTP {code}")
    elif code != 200:
        resp.failure(f"OGC Error: HTTP {code}" if "xml" in ctype else f"HTTP {code}")
    elif "xml" in ctype and b"ExceptionReport" in (resp.content or b"")[:1000]:  # ServiceExceptionReport too
        resp.failure("OGC Exception")
    elif not ctype.startswith(accept):
        resp.failure(f"Invalid content type: {ctype.split(';')[0].strip()}")
    else:
        return True
    return False


def mark_empty(resp):
    """Report a successful response as '<name> (empty)': a blank tile or map, or no features, on a row of its own."""
    resp.request_meta["name"] = f"{resp.request_meta['name']} (empty)"


def _crs(code):
    try:
        return CRS.from_user_input("OGC:CRS84" if code == "CRS:84" else code)
    except CRSError:
        return None


def same_crs(a, b):
    """True when two CRS codes or URIs name the same CRS (pyproj); strings compared when pyproj reads neither."""
    ca, cb = _crs(a), _crs(b)
    return ca == cb if ca is not None and cb is not None else a == b


@lru_cache(maxsize=None)
def crs_known(code):
    """True when pyproj can read the CRS (ArcGIS's EPSG:102100, for one, it cannot)."""
    return _crs(code) is not None


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


@lru_cache(maxsize=None)
def is_geographic(code):
    """True for a CRS in degrees (EPSG:4326, CRS:84, ...): bboxes are then sized in degrees."""
    crs = _crs(code)
    return bool(crs and crs.is_geographic)


def cached_capabilities(key, fetch):
    """(capabilities, fetched_now): fetch() runs once per process per key; concurrent users wait for it."""
    with _CAPS_LOCK:
        if key in _CAPS:
            return _CAPS[key], False
        _CAPS[key] = fetch()
        return _CAPS[key], True


def add_common_args(parser, bbox=True):
    """The options every locustfile shares: --random-seed, and --bbox-area / --bbox-ratio for bbox services."""
    parser.add_argument(
        "--random-seed",
        type=int,
        default=1640,
        env_var="LOCUST_RANDOM_SEED",
        help="Seed for the random requests: the same seed replays the same requests (default 1640)",
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        default=False,
        env_var="LOCUST_VERIFY_TLS",
        help="Check the servers' TLS certificates (default: not checked, test servers often have bad certificates)",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default=USER_AGENT,
        env_var="LOCUST_USER_AGENT",
        help=f"User-Agent of every request, so server operators can tell benchmark traffic apart (default: {USER_AGENT})",
    )
    if bbox:
        parser.add_argument(
            "--bbox-area",
            type=float,
            default=100.0,
            env_var="LOCUST_BBOX_AREA",
            help="Bbox area in km2 (default 100, i.e. 10 x 10 km with --bbox-ratio 1.0)",
        )
        parser.add_argument(
            "--bbox-ratio",
            type=float,
            default=1.0,
            env_var="LOCUST_BBOX_RATIO",
            help="Bbox width/height ratio (default 1.0, square)",
        )


def add_auth_args(parser):
    """--auth-user / --auth-password: HTTP basic auth for the metadata and every request."""
    parser.add_argument(
        "--auth-user",
        type=str,
        default="",
        env_var="LOCUST_AUTH_USER",
        help="HTTP basic auth user, for GetCapabilities (or the OGC API metadata) and every request (default: none)",
    )
    parser.add_argument(
        "--auth-password",
        type=str,
        default="",
        env_var="LOCUST_AUTH_PASSWORD",
        include_in_web_ui=False,  # the web UI page would carry its value in clear
        help="HTTP basic auth password; prefer the env var, the command line is visible in ps",
    )


def auth_header(opts):
    """{"Authorization": "Basic ..."} for --auth-user / --auth-password, or {} without a user."""
    if not getattr(opts, "auth_user", ""):
        return {}
    return {"Authorization": "Basic " + b64encode(f"{opts.auth_user}:{opts.auth_password}".encode()).decode()}


def request_headers(opts):
    """The headers of every request: the benchmark's User-Agent, plus basic auth when --auth-user is set."""
    return {"User-Agent": getattr(opts, "user_agent", USER_AGENT), **auth_header(opts)}


def add_extent_arg(parser):
    """--extent: the area to test, in WGS84 lon/lat, narrowing the layer's or collection's own extent."""
    parser.add_argument(
        "--extent",
        type=str,
        default="",
        env_var="LOCUST_EXTENT",
        help="Area to test, WGS84 lon/lat: minlon,minlat,maxlon,maxlat (default: the layer's or collection's extent)",
    )


def parse_extent(text):
    """'minlon,minlat,maxlon,maxlat' -> a 4-tuple of floats; '' -> None; ValueError for anything else."""
    if not text:
        return None
    try:
        box = tuple(float(v) for v in text.split(","))
    except ValueError:
        raise ValueError(f"--extent must be 4 numbers minlon,minlat,maxlon,maxlat, got '{text}'") from None
    if len(box) != 4:
        raise ValueError(f"--extent must be 4 numbers minlon,minlat,maxlon,maxlat, got '{text}'")
    minx, miny, maxx, maxy = box
    if minx >= maxx or miny >= maxy:
        raise ValueError(f"--extent needs min < max for both lon and lat, got '{text}'")
    if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise ValueError(f"--extent must be WGS84 lon/lat (lon -180..180, lat -90..90), got '{text}'")
    return box


def intersect(a, b):
    """The overlap of two (minx, miny, maxx, maxy) boxes, or None when they share no area."""
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] < box[2] and box[1] < box[3] else None


def add_map_args(parser):
    """The map options wms.py and ogcapi_maps.py share: zoom mix, image size, per-user rate."""
    parser.add_argument(
        "--bbox-areas",
        type=str,
        default="",
        env_var="LOCUST_BBOX_AREAS",
        help="Comma-separated bbox areas in km2, one picked at random per request (zoom mix), e.g. '1,10,100'. "
        "Overrides --bbox-area.",
    )
    parser.add_argument(
        "--width", type=int, default=512, env_var="LOCUST_WIDTH", help="GetMap width in pixels (default 512)"
    )
    parser.add_argument(
        "--height", type=int, default=512, env_var="LOCUST_HEIGHT", help="GetMap height in pixels (default 512)"
    )
    parser.add_argument(
        "--user-rps",
        type=float,
        default=0.0,
        env_var="LOCUST_USER_RPS",
        help="Requests per second per user (constant throughput), so total rate = users x this value. "
        "Default 0 keeps the 1-2 s wait between requests.",
    )


def bbox_areas(opts):
    """The bbox areas in km2: --bbox-areas parsed, else [--bbox-area]; ValueError for non-numbers."""
    if not opts.bbox_areas:
        return [opts.bbox_area]
    try:
        return [float(a) for a in opts.bbox_areas.split(",")]
    except ValueError:
        raise ValueError(f"--bbox-areas must be km2 numbers, e.g. '1,10,100', got '{opts.bbox_areas}'") from None


@events.init.add_listener
def apply_verify_tls(environment, **kwargs):
    """--verify-tls: let Locust check certificates too (FastHttpUser does not by default); log when nothing is."""
    opts = environment.parsed_options
    if opts is None:
        return
    if getattr(opts, "verify_tls", False):
        for user_class in environment.user_classes:
            user_class.insecure = False
    else:
        logger.info("TLS certificates are not checked (--verify-tls checks them)")


@events.init.add_listener
def warm_up(environment, runner=None, **kwargs):
    """
    Run each user class's start-up once before the run: metadata (GetCapabilities, OGC API landing page and
    collections), the WMTS probe and the Maps reference are cached when the first user starts, so Locust's -t
    clock and statistics count the load only. Locust fires `init` before it starts the runner and the -t timer.

    Skipped on a distributed master (the workers warm up) and without --host (web UI: the host comes later).
    """
    if environment.parsed_options is None or isinstance(runner, MasterRunner) or not environment.host:
        return
    for user_class in environment.user_classes:
        user = user_class(environment)
        user.host = environment.host  # Locust copies --host onto the classes only when the run starts
        try:
            user.on_start()
        except ArgError:  # logged, exit code 1 set and runner.quit() scheduled by handle_error
            return


@events.test_start.add_listener
def reset_user_ids(**kwargs):
    """Number the simulated users from 0 at every run (after the warm-up, and for each web UI run)."""
    global _USER_IDS
    _USER_IDS = itertools.count()


def owslib_auth(opts):
    """OWSLib Authentication: basic auth when --auth-user is set, certificate checks only with --verify-tls."""
    user = getattr(opts, "auth_user", "") or None
    password = getattr(opts, "auth_password", "") or None
    return Authentication(user, password, verify=getattr(opts, "verify_tls", False))


def with_params(host, params):
    """host with params merged into its query string (the host may already carry ?map=... or a token)."""
    parts = urlparse(host)
    query = parse_qs(parts.query)
    query.update(params)
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def default_options(init_parser, *args):
    """The options a locustfile's init_parser defines, parsed from args (defaults and LOCUST_* env vars)."""
    parser = LocustArgumentParser()
    init_parser(parser)
    return parser.parse_args(list(args))


def user_rng(environment, seed):
    """A random stream of its own for each simulated user, distinct across users and worker processes."""
    worker = getattr(environment.runner, "worker_index", 0)
    return random.Random(f"{seed}-{worker}-{next(_USER_IDS)}")
