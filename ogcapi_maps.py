"""OGC API - Maps load test: map images of random bboxes inside a collection's extent, at one or several zoom levels."""

# gevent patches the standard library before requests is imported (needed for `python ogcapi_maps.py`)
from gevent import monkey

monkey.patch_all()

import logging  # noqa: E402
import random  # noqa: E402

import requests  # noqa: E402
from exceptions import ArgError  # noqa: E402
from locust import FastHttpUser, between, constant_throughput, events, task  # noqa: E402
from owslib.ogcapi.maps import Maps  # noqa: E402

from utils.ogc import (  # noqa: E402
    IMAGE,
    add_auth_args,
    add_common_args,
    add_extent_arg,
    add_map_args,
    bbox_areas,
    cached_capabilities,
    check_response,
    default_options,
    is_geographic,
    is_metric,
    is_yx,
    mark_empty,
    request_headers,
    user_rng,
    with_params,
)
from utils.ogcapi import (  # noqa: E402
    CRS84,
    crs_uri,
    link,
    lonlat_area,
    lonlat_extent,
    ogcapi_collections,
    pick_collection,
)
from utils.random_bbox import random_bbox  # noqa: E402
from utils.tiles import to_crs_box  # noqa: E402

logger = logging.getLogger(__name__)


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser)
    parser.add_argument(
        "--collection",
        type=str,
        default="",
        env_var="LOCUST_COLLECTION",
        help="Collection to benchmark (default: the first collection with a map)",
    )
    parser.add_argument(
        "--crs",
        type=str,
        default="",
        env_var="LOCUST_CRS",
        help="CRS of the bbox and the map, e.g. EPSG:3857, in metres or degrees (default: CRS84)",
    )
    add_map_args(parser)
    add_extent_arg(parser)
    add_auth_args(parser)


@events.init.add_listener
def on_init(environment, **kwargs):
    """Apply --user-rps before any user starts (wait_time is a class attribute)."""
    opts = environment.parsed_options
    if opts is not None and getattr(opts, "user_rps", 0) > 0:
        OGCAPIMapsBenchmark.wait_time = constant_throughput(opts.user_rps)


class OGCAPIMapsBenchmark(FastHttpUser):
    """Map requests for random bboxes inside a collection's extent."""

    wait_time = between(1, 2)  # 1-2 s between requests, replaced by constant_throughput when --user-rps is set
    host = ""  # without a class attribute the __main__ example cannot set the host
    network_timeout = 120.0  # seconds; a slow server can take tens of seconds for one orthophoto map
    connection_timeout = 120.0

    def on_start(self):
        """Read the collections (once per process), pick collection, CRS and area, and a blank map to compare to."""
        opts = self.environment.parsed_options
        try:
            self.areas = bbox_areas(opts)
        except ValueError as e:
            ArgError(str(e)).handle_error(self.environment)
        if min(self.areas) <= 0 or opts.bbox_ratio <= 0:
            ArgError(f"bbox areas {self.areas} and ratio {opts.bbox_ratio} must be greater than 0").handle_error(
                self.environment
            )
        if opts.auth_password and not opts.auth_user:
            ArgError("--auth-password is set but --auth-user is not").handle_error(self.environment)
        self.headers = request_headers(opts)
        try:
            self.setup(opts)
        except ValueError as e:  # a wrong option or metadata the benchmark cannot use
            ArgError(str(e)).handle_error(self.environment)
        except Exception as e:  # unreachable host, HTTP 401, not an OGC API: stop now instead of running with no users
            ArgError(f"Cannot read the OGC API collections of {self.host}: {e}").handle_error(self.environment)
        key = ("ogcapi-map-blank", self.host, self.collection, self.crs, opts.width, opts.height)
        self.blank, fetched_now = cached_capabilities(key, lambda: self.blank_reference(opts))
        if fetched_now and self.blank is None:
            logger.info("no blank reference map (the request outside the extent failed): no '(empty)' check")
        self.rng = user_rng(self.environment, opts.random_seed)
        logger.info(
            f"collection {self.collection}, CRS {self.crs}, area {self.area}, bbox areas in km2 {self.areas}, "
            f"seed {opts.random_seed}"
        )

    def setup(self, opts):
        """Set collection, map URL, CRS and area (in that CRS); ValueError if unusable."""
        col = pick_collection(ogcapi_collections(Maps, self.host, opts)["collections"], opts.collection, "map")
        self.collection = col["id"]
        self.map_url = link(col, "map", "image/png") or link(col, "map")
        self.crs = crs_uri(opts.crs) if opts.crs else CRS84
        if not (is_metric(self.crs) or is_geographic(self.crs)):
            raise ValueError(f"CRS {self.crs} is neither in metres nor in degrees")
        box = lonlat_area(col, opts.extent)
        self.area = box if self.crs == CRS84 else to_crs_box(box, self.crs)
        try:  # the blank reference goes outside the collection's data, not just outside --extent
            own = lonlat_extent(col)
            self.data_area = own if self.crs == CRS84 else to_crs_box(own, self.crs)
        except ValueError:
            self.data_area = self.area

    def map_params(self, box):
        """The map query parameters for a bbox (x/y order); y/x CRSs get latitude first, as the standard says."""
        opts = self.environment.parsed_options
        if is_yx(self.crs):
            box = (box[1], box[0], box[3], box[2])
        params = {"f": "png", "width": str(opts.width), "height": str(opts.height), "bbox": ",".join(map(str, box))}
        if self.crs != CRS84:  # an explicit CRS84 bbox-crs is not needed, and some servers answer it with a 500
            params["bbox-crs"] = params["crs"] = self.crs
        return params

    def blank_reference(self, opts):
        """The bytes of a map just outside the extent (no data there), or None if that request fails."""
        size = random_bbox(random.Random(0), self.area, self.areas[0], opts.bbox_ratio, not is_metric(self.crs))
        width, height = size[2] - size[0], size[3] - size[1]
        minx, miny = self.data_area[0], self.data_area[1]
        box = (minx - width, miny, minx, miny + height)  # just west of the collection's extent
        if self.crs == CRS84 and box[0] < -180:
            maxx = self.data_area[2]
            box = (maxx, miny, maxx + width, miny + height)  # just east instead
        try:
            resp = requests.get(
                with_params(self.map_url, self.map_params(box)),
                headers=self.headers,
                timeout=120,
                verify=opts.verify_tls,
            )
        except Exception:
            return None
        ctype = (resp.headers or {}).get("content-type", "")
        return resp.content if resp.status_code == 200 and ctype.startswith(IMAGE) else None

    @task
    def load_map(self):
        """A map of a random bbox at one of the bbox areas; anything but a 200 image is a failure."""
        opts = self.environment.parsed_options
        area = self.rng.choice(self.areas)
        box = random_bbox(self.rng, self.area, area, opts.bbox_ratio, geographic=not is_metric(self.crs))
        url = with_params(self.map_url, self.map_params(box))
        name = f"GetMap {self.collection}"
        with self.client.get(url, headers=self.headers, catch_response=True, name=name) as resp:
            if check_response(resp, IMAGE) and self.blank is not None and resp.content == self.blank:
                mark_empty(resp)


# Example: one map from the public pygeoapi demo (world map, 100 km2 bbox in CRS84)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[OGCAPIMapsBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--collection", "mapserver_world_map")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception)
    )

    user = OGCAPIMapsBenchmark(env)
    user.host = "https://demo.pygeoapi.io/master"
    user.on_start()
    user.load_map()
    env.runner.quit()
