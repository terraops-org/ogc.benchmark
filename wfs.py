"""WFS GetFeature load test (1.0.0, 1.1.0, 2.0.0): features inside random bboxes of a feature type's extent."""

# gevent patches the standard library before owslib imports requests (needed for `python wfs.py`)
from gevent import monkey

monkey.patch_all()

import logging  # noqa: E402

from exceptions import ArgError  # noqa: E402
from locust import FastHttpUser, between, events, task  # noqa: E402
from owslib.wfs import WebFeatureService  # noqa: E402
from pyproj import Transformer  # noqa: E402

from utils.ogc import (
    FEATURES,
    add_common_args,
    cached_capabilities,
    check_response,
    default_options,
    owslib_auth,
    request_headers,
    user_rng,
    with_params,
)  # noqa: E402
from utils.random_bbox import random_bbox  # noqa: E402

logger = logging.getLogger(__name__)

# WFS 1.1.0 / 2.0.0 BBOX with an explicit CRS; for this URN the axis order is latitude, longitude. Without
# the CRS a server reads the bbox in its default CRS (PDOK BAG: RD metres, so every request found nothing).
# WFS 1.0.0 has no CRS in BBOX: the corners go in the feature type's SRS, x/y (probed 2026-11-17).
BBOX_CRS = "urn:ogc:def:crs:EPSG::4326"
VERSIONS = {"1.0.0": ("TYPENAME", "MAXFEATURES"), "1.1.0": ("TYPENAME", "MAXFEATURES"), "2.0.0": ("TYPENAMES", "COUNT")}


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser)
    parser.add_argument(
        "--feature-name",
        type=str,
        default=None,
        env_var="LOCUST_FEATURE_NAME",
        help="Feature type with namespace, e.g. 'au:AU.AdministrativeUnits' (default: the first in GetCapabilities)",
    )
    parser.add_argument(
        "--wfs-version",
        type=str,
        default="2.0.0",
        choices=list(VERSIONS),
        env_var="LOCUST_WFS_VERSION",
        help="WFS version for GetCapabilities and GetFeature (default 2.0.0)",
    )


class WFSBenchmark(FastHttpUser):
    """GetFeature requests (up to 1000 features) inside random bboxes."""

    wait_time = between(1, 2)  # 1-2 s between requests per simulated user
    host = ""  # without a class attribute the __main__ example cannot set the host

    def on_start(self):
        """Read GetCapabilities, then pick the feature type, its WGS84 extent and this user's random stream."""
        opts = self.environment.parsed_options
        self.headers = request_headers(opts)
        self.version = opts.wfs_version
        try:  # once per process; unreachable host or not a WFS: stop now instead of running with no users
            self.wfs20, _ = cached_capabilities(
                ("wfs", self.host, self.version),
                lambda: WebFeatureService(
                    self.host,
                    parse_remote_metadata=False,
                    version=self.version,
                    auth=owslib_auth(opts),
                    headers={"User-Agent": opts.user_agent},
                ),
            )
        except Exception as e:
            ArgError(f"Cannot read the WFS GetCapabilities of {self.host}: {e}").handle_error(self.environment)
        self.feature_names = list(self.wfs20.contents)
        self.feature_name = opts.feature_name
        if self.feature_name:
            if self.feature_name not in self.feature_names:
                ArgError(
                    f"Feature '{self.feature_name}' is not available. Available: {self.feature_names}"
                ).handle_error(self.environment)
        else:
            logger.info("No --feature-name argument, using the first feature type.")
            self.feature_name = self.feature_names[0]
        meta = self.wfs20.contents[self.feature_name]
        # lon/lat. WFS 1.0.0: OWSLib 0.36 "reprojects" the LatLongBoundingBox as if it were in the native SRS
        # and gets a point; its boundingBox holds the right degrees.
        self.extent = tuple((meta.boundingBox if self.version == "1.0.0" else meta.boundingBoxWGS84)[:4])
        self.to_native = None
        if self.version == "1.0.0":
            if not meta.crsOptions:
                ArgError(f"Feature '{self.feature_name}' advertises no SRS, needed for a WFS 1.0.0 BBOX").handle_error(
                    self.environment
                )
            native = meta.crsOptions[0]
            native = native.getcodeurn() if hasattr(native, "getcodeurn") else str(native)
            self.to_native = Transformer.from_crs("EPSG:4326", native, always_xy=True)
        self.rng = user_rng(self.environment, opts.random_seed)

    @task
    def load_features(self):
        """A GetFeature for a random bbox; anything but HTTP 200 with GML/XML/JSON features is a failure."""
        opts = self.environment.parsed_options
        minx, miny, maxx, maxy = random_bbox(self.rng, self.extent, opts.bbox_area, opts.bbox_ratio, geographic=True)
        if self.to_native:  # WFS 1.0.0
            bbox = ",".join(map(str, self.to_native.transform_bounds(minx, miny, maxx, maxy)))
        else:
            bbox = f"{miny},{minx},{maxy},{maxx},{BBOX_CRS}"
        typename, limit = VERSIONS[self.version]
        params = {"SERVICE": "WFS", "VERSION": self.version, "REQUEST": "GetFeature", typename: self.feature_name}
        url_path = with_params(self.host, {**params, "BBOX": bbox, limit: "1000"})
        logger.debug(f"URL for request: {url_path}")
        with self.client.get(url_path, headers=self.headers, catch_response=True, name="GetFeature") as response:
            check_response(response, FEATURES)


# Example: one GetFeature of buildings in a 1 km2 bbox of the public PDOK BAG WFS
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[WFSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--feature-name", "bag:pand", "--bbox-area", "1")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception, len(response.content))
    )

    user = WFSBenchmark(env)
    user.host = "https://service.pdok.nl/lv/bag/wfs/v2_0"
    user.on_start()
    user.extent = (4.88, 52.36, 4.90, 52.38)  # central Amsterdam, so the bbox surely holds buildings
    user.load_features()
    env.runner.quit()
