"""OGC API - Features load test: the items of a collection inside random bboxes of its extent."""

# gevent patches the standard library before requests is imported (needed for `python ogcapi_features.py`)
from gevent import monkey

monkey.patch_all()

import logging  # noqa: E402
import re  # noqa: E402

from exceptions import ArgError  # noqa: E402
from locust import FastHttpUser, between, events, task  # noqa: E402

from utils.ogc import (  # noqa: E402
    FEATURES,
    add_auth_args,
    add_common_args,
    add_extent_arg,
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
from utils.ogcapi import CRS84, collections, crs_uri, link, lonlat_area, pick_collection  # noqa: E402
from utils.random_bbox import random_bbox  # noqa: E402
from utils.tiles import to_crs_box  # noqa: E402

logger = logging.getLogger(__name__)

EMPTY_SCAN_BYTES = 65536  # an empty FeatureCollection is small; larger bodies are not scanned


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser)
    parser.add_argument(
        "--collection",
        type=str,
        default="",
        env_var="LOCUST_COLLECTION",
        help="Collection to benchmark (default: the first collection with items)",
    )
    parser.add_argument(
        "--crs",
        type=str,
        default="",
        env_var="LOCUST_CRS",
        help="CRS of the bbox and the answer, e.g. EPSG:3763; must be in the collection's crs list (default: CRS84)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        env_var="LOCUST_LIMIT",
        help="Features per request (default 100; servers may cap it)",
    )
    add_extent_arg(parser)
    add_auth_args(parser)


def is_empty_collection(content):
    """True for a small FeatureCollection with numberReturned 0 (or, without it, an empty features list)."""
    if len(content) >= EMPTY_SCAN_BYTES:
        return False
    returned = re.search(rb'"numberReturned"\s*:\s*(\d+)', content)
    if returned:
        return returned.group(1) == b"0"
    return re.search(rb'"features"\s*:\s*\[\s*\]', content) is not None


class OGCAPIFeaturesBenchmark(FastHttpUser):
    """Items requests for random bboxes inside a collection's extent."""

    wait_time = between(1, 2)  # 1-2 s between requests per simulated user
    host = ""  # without a class attribute the __main__ example cannot set the host
    network_timeout = 120.0  # seconds; large polygons can take tens of seconds on a slow server
    connection_timeout = 120.0

    def on_start(self):
        """Read the collections (once per process), pick the collection, CRS and area, and a random stream."""
        opts = self.environment.parsed_options
        if opts.auth_password and not opts.auth_user:
            ArgError("--auth-password is set but --auth-user is not").handle_error(self.environment)
        if opts.limit <= 0 or opts.bbox_area <= 0 or opts.bbox_ratio <= 0:
            ArgError(
                f"--limit ({opts.limit}), --bbox-area ({opts.bbox_area}) and --bbox-ratio ({opts.bbox_ratio}) "
                "must be greater than 0"
            ).handle_error(self.environment)
        self.headers = request_headers(opts)
        try:
            self.setup(opts)
        except ValueError as e:  # a wrong option or metadata the benchmark cannot use
            ArgError(str(e)).handle_error(self.environment)
        except Exception as e:  # unreachable host, HTTP 401, not an OGC API: stop now instead of running with no users
            ArgError(f"Cannot read the OGC API collections of {self.host}: {e}").handle_error(self.environment)
        self.rng = user_rng(self.environment, opts.random_seed)
        logger.info(f"collection {self.collection}, CRS {self.crs}, area {self.area}, seed {opts.random_seed}")

    def setup(self, opts):
        """Set collection, items URL, CRS and area (in that CRS); ValueError if unusable."""
        col = pick_collection(collections(self.host, self.headers, opts.verify_tls), opts.collection, "items")
        self.collection = col["id"]
        self.items_url = link(col, "items", "application/geo+json") or link(col, "items")
        self.crs = crs_uri(opts.crs) if opts.crs else CRS84
        offered = sorted({crs_uri(c) for c in col.get("crs") or [CRS84]})
        if self.crs not in offered:
            raise ValueError(f"Collection '{self.collection}' does not offer CRS {self.crs}; offered: {offered}")
        if not (is_metric(self.crs) or is_geographic(self.crs)):
            raise ValueError(f"CRS {self.crs} is neither in metres nor in degrees")
        box = lonlat_area(col, opts.extent)
        self.area = box if self.crs == CRS84 else to_crs_box(box, self.crs)

    @task
    def load_features(self):
        """The items in a random bbox; anything but a 200 JSON answer is a failure, no features is '(empty)'."""
        opts = self.environment.parsed_options
        box = random_bbox(self.rng, self.area, opts.bbox_area, opts.bbox_ratio, geographic=not is_metric(self.crs))
        if is_yx(self.crs):  # e.g. EPSG:4326: the bbox goes latitude first, as OGC API Features Part 2 requires
            box = (box[1], box[0], box[3], box[2])
        params = {"f": "json", "bbox": ",".join(map(str, box)), "limit": str(opts.limit)}
        if self.crs != CRS84:
            params["bbox-crs"] = params["crs"] = self.crs
        url = with_params(self.items_url, params)
        with self.client.get(url, headers=self.headers, catch_response=True, name="GetFeature") as resp:
            if check_response(resp, FEATURES) and is_empty_collection(resp.content or b""):
                mark_empty(resp)


# Example: one items request against the public pygeoapi demo (Dutch windmills, 100 km2 bbox)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[OGCAPIFeaturesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--collection", "dutch_windmills")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception)
    )

    user = OGCAPIFeaturesBenchmark(env)
    user.host = "https://demo.pygeoapi.io/master"
    user.on_start()
    user.load_features()
    env.runner.quit()
