"""WMS GetMap load test: random bboxes on one layer or a weighted layer mix, at one or several zoom levels."""

import logging
import math
import os
from base64 import b64encode
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from exceptions import LayerNameArgError
from locust import FastHttpUser, between, constant_throughput, events, task
from owslib.util import Authentication
from owslib.wms import WebMapService

from utils.ogc import CAPABILITIES, IMAGE, check_response, default_options, is_metric, is_yx, user_rng
from utils.random_bbox import random_bbox

logger = logging.getLogger(__name__)

_CAPABILITIES = {}  # GetCapabilities parsed once per process, not once per user


@events.init_command_line_parser.add_listener
def init_parser(parser):
    parser.add_argument(
        "--random-seed",
        type=int,
        default=1640,
        env_var="LOCUST_RANDOM_SEED",
        help="Seed for the random bboxes: the same seed replays the same requests (default 1640)",
    )
    parser.add_argument(
        "--layer-name",
        type=str,
        default="",
        env_var="LOCUST_LAYER_NAME",
        help="Layer to benchmark (default: the service's first layer)",
    )
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
    parser.add_argument(
        "--layers",
        type=str,
        default="",
        env_var="LOCUST_LAYERS",
        help="Weighted layer mix, e.g. 'layerA:55,layerB:15'. Overrides --layer-name.",
    )
    parser.add_argument(
        "--bbox-areas",
        type=str,
        default="",
        env_var="LOCUST_BBOX_AREAS",
        help="Comma-separated bbox areas in km2, one picked at random per request (zoom mix), e.g. '1,10,100'. "
        "Overrides --bbox-area.",
    )
    parser.add_argument(
        "--crs",
        type=str,
        default="",
        env_var="LOCUST_CRS",
        help="CRS for GetMap, e.g. EPSG:3857 (must be advertised for the layer; EPSG:3857 is derived from the "
        "WGS84 extent if missing). Default: the layer's first CRS in metres.",
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
    parser.add_argument(
        "--caps-ratio",
        type=float,
        default=0.0,
        env_var="LOCUST_CAPS_RATIO",
        help="Fraction of requests that are GetCapabilities instead of GetMap, e.g. 0.001 (default 0).",
    )
    parser.add_argument(
        "--wms-user",
        type=str,
        default=os.getenv("WMS_USER", ""),
        help="HTTP Basic Auth user (default: env WMS_USER)",
    )
    parser.add_argument(
        "--wms-password",
        type=str,
        default=os.getenv("WMS_PASSWORD", ""),
        is_secret=True,
        help="HTTP Basic Auth password (default: env WMS_PASSWORD; prefer the env var, the command line is visible in ps)",
    )


@events.init.add_listener
def on_init(environment, **kwargs):
    """Apply --user-rps before any user starts (wait_time is a class attribute)."""
    opts = environment.parsed_options
    if opts is not None and getattr(opts, "user_rps", 0) > 0:
        WMSBenchmark.wait_time = constant_throughput(opts.user_rps)


def mercator(lon, lat):
    """WGS84 lon/lat to EPSG:3857 metres."""
    return lon * 20037508.342789244 / 180, math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137.0


class WMSBenchmark(FastHttpUser):
    """GetMap requests for random bboxes inside a layer's extent."""

    # Without a class attribute the __main__ example cannot set the host
    host = ""

    # 1-2 s between requests, replaced by constant_throughput when --user-rps is set
    wait_time = between(1, 2)

    # Timeouts extended for very slow servers (seconds)
    network_timeout = 120.0
    connection_timeout = 120.0

    def on_start(self):
        """Read GetCapabilities (once per process), then pick layers, extents and this user's random stream."""
        opts = self.environment.parsed_options
        self.headers = {}
        if opts.wms_user:
            token = b64encode(f"{opts.wms_user}:{opts.wms_password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"

        first_user = (self.host, opts.wms_user) not in _CAPABILITIES
        self.wms = self.get_wms(opts)
        self.layers = list(self.wms.contents)

        # Layer mix: --layers 'a:55,b:15', else the single --layer-name, else the first layer
        if opts.layers:
            pairs = [item.rsplit(":", 1) for item in opts.layers.split(",") if item.strip()]
            mix = [(name.strip(), float(weight)) for name, weight in pairs]
        else:
            if not opts.layer_name:
                logger.info("No --layer-name argument value, using first layer available in service")
            mix = [(opts.layer_name or self.layers[0], 1.0)]
        for name, _ in mix:
            if name not in self.layers:
                raise LayerNameArgError(
                    f"The specified WMS layer '{name}' is not available in WMS service "
                    f"'{self.wms.identification.title}'. Available layers: {self.layers}"
                )
        self.layer_names = [name for name, _ in mix]
        self.layer_weights = [weight for _, weight in mix]
        self.extents = {name: self.get_extent(name, opts.crs) for name in self.layer_names}
        self.areas = [float(a) for a in opts.bbox_areas.split(",")] if opts.bbox_areas else [opts.bbox_area]
        self.rng = user_rng(self.environment, opts.random_seed)
        if first_user:
            logger.info(f"layers (weight): {list(zip(self.layer_names, self.layer_weights))}")
            logger.info(f"bbox areas in km2: {self.areas}, aspect ratio: {opts.bbox_ratio}, seed: {opts.random_seed}")
            logger.info(f"extents: {self.extents}")

    def get_wms(self, opts):
        key = (self.host, opts.wms_user)
        if key not in _CAPABILITIES:
            auth = Authentication(opts.wms_user, opts.wms_password) if opts.wms_user else None
            _CAPABILITIES[key] = WebMapService(self.host, version="1.3.0", auth=auth, timeout=120)
        return _CAPABILITIES[key]

    def get_extent(self, layer_name, crs):
        """
        (minx, miny, maxx, maxy, crs) of a layer, in OWSLib's x/y order.

        With --crs: the layer's box in that CRS (EPSG:3857 derived from WGS84 if the server has none).
        Without: the first box in a metre CRS, else the first box, else (a layer that inherits its
        extent) the WGS84 box in CRS:84.
        """
        layer = self.wms.contents[layer_name]
        boxes = getattr(layer, "crs_list", None) or []
        wgs84 = getattr(layer, "boundingBoxWGS84", None)
        if not crs:
            for box in boxes:
                if is_metric(box[4]):
                    return tuple(box[:5])
            if boxes:
                return tuple(boxes[0][:5])
            if wgs84:
                return (*wgs84[:4], "CRS:84")
            raise LayerNameArgError(f"Layer '{layer_name}' advertises no extent")
        for box in boxes:
            if box[4] == crs:
                return tuple(box[:5])
        if crs == "EPSG:3857" and wgs84:  # servers often advertise only the native CRS box; derive Web Mercator
            west, south, east, north = wgs84[:4]
            return (*mercator(west, south), *mercator(east, north), crs)
        raise LayerNameArgError(f"Layer '{layer_name}' has no BoundingBox in {crs}; available: {[b[4] for b in boxes]}")

    @task
    def load_map(self):
        """
        A GetMap of a random bbox on a layer from the mix, or (with --caps-ratio) a GetCapabilities.
        Anything but a 200 image is a failure (MapServer can answer 200 with an XML exception).
        """
        opts = self.environment.parsed_options
        if opts.caps_ratio and self.rng.random() < opts.caps_ratio:
            url = self.service_url({"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"})
            with self.client.get(url, name="GetCapabilities", headers=self.headers, catch_response=True) as resp:
                check_response(resp, CAPABILITIES)
            return

        layer = self.rng.choices(self.layer_names, weights=self.layer_weights)[0]
        minx, miny, maxx, maxy, crs = self.extents[layer]
        area = self.rng.choice(self.areas)
        box = random_bbox(self.rng, (minx, miny, maxx, maxy), area, opts.bbox_ratio, geographic=not is_metric(crs))
        if is_yx(crs):  # WMS 1.3.0 wants latitude/northing first for these CRSs; OWSLib gives x/y
            box = (box[1], box[0], box[3], box[2])
        url_path = self.get_url(",".join(map(str, box)), layer=layer, crs=crs)
        logger.debug(f"URL for request: {url_path}")
        with self.client.get(url_path, name=f"GetMap {layer}", headers=self.headers, catch_response=True) as resp:
            check_response(resp, IMAGE)

    def service_url(self, params):
        """
        The host URL with params merged into its query string (the host may already carry ?map=...).
        """
        url_parts = urlparse(self.host)
        query_params = parse_qs(url_parts.query)
        query_params.update(params)
        return urlunparse(url_parts._replace(query=urlencode(query_params, doseq=True)))

    def get_url(self, bbox_str, layer=None, crs=None):
        """
        The GetMap URL for bbox_str ("minx,miny,maxx,maxy" in the axis order the CRS needs).

        layer defaults to the first layer of the mix, crs to that layer's extent CRS. Example:
        get_url("120000,480000,130000,490000", "Actueel_orthoHR", "EPSG:28992") on host
        https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0 gives
        https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0?service=WMS&version=1.3.0&request=GetMap&layers=Actueel_orthoHR&styles=&bbox=120000%2C480000%2C130000%2C490000&width=512&height=512&crs=EPSG%3A28992&format=image%2Fpng
        """
        layer = layer or self.layer_names[0]
        opts = self.environment.parsed_options
        params = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": layer,
            "styles": "",
            "bbox": bbox_str,
            "width": str(opts.width),
            "height": str(opts.height),
            "crs": crs or self.extents[layer][4],
            "format": "image/png",
        }
        return self.service_url(params)


# Example: one GetMap against a public PDOK WMS, CRS chosen automatically
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--layer-name", "Actueel_orthoHR")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception)
    )

    user = WMSBenchmark(env)
    user.host = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
    user.on_start()
    user.load_map()
    env.runner.quit()
