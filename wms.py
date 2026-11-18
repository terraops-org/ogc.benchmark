"""WMS GetMap load test: random bboxes on one layer or a weighted layer mix, at one or several zoom levels."""

import logging
import math

from exceptions import ArgError
from locust import FastHttpUser, between, constant_throughput, events, task
from owslib.wms import WebMapService

from utils.ogc import (
    CAPABILITIES,
    IMAGE,
    add_auth_args,
    add_common_args,
    add_map_args,
    bbox_areas,
    cached_capabilities,
    check_response,
    default_options,
    is_geographic,
    is_metric,
    is_yx,
    owslib_auth,
    request_headers,
    user_rng,
    with_params,
)
from utils.random_bbox import random_bbox

logger = logging.getLogger(__name__)


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser)
    parser.add_argument(
        "--layer-name",
        type=str,
        default="",
        env_var="LOCUST_LAYER_NAME",
        help="Layer to benchmark (default: the service's first layer)",
    )
    parser.add_argument(
        "--layers",
        type=str,
        default="",
        env_var="LOCUST_LAYERS",
        help="Weighted layer mix, e.g. 'layerA:55,layerB:15'. Overrides --layer-name.",
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
        "--caps-ratio",
        type=float,
        default=0.0,
        env_var="LOCUST_CAPS_RATIO",
        help="Fraction of requests that are GetCapabilities instead of GetMap, e.g. 0.001 (default 0).",
    )
    add_map_args(parser)
    add_auth_args(parser)


@events.init.add_listener
def on_init(environment, **kwargs):
    """Apply --user-rps before any user starts (wait_time is a class attribute)."""
    opts = environment.parsed_options
    if opts is not None and getattr(opts, "user_rps", 0) > 0:
        WMSBenchmark.wait_time = constant_throughput(opts.user_rps)


def mercator(lon, lat):
    """WGS84 lon/lat to EPSG:3857 metres; latitudes beyond Web Mercator's +-85.05 degrees are clamped."""
    lat = max(-85.0511287798, min(85.0511287798, lat))
    return lon * 20037508.342789244 / 180, math.log(math.tan((90 + lat) * math.pi / 360)) * 6378137.0


def mix_item(item):
    """'layer:55' -> ('layer', 55.0); a name without a numeric weight ('layer', 'ns:layer') gets weight 1."""
    name, sep, weight = item.strip().rpartition(":")
    try:
        return (name, float(weight)) if sep else (item.strip(), 1.0)
    except ValueError:
        return (item.strip(), 1.0)


def inherited_boxes(layer):
    """The layer's BoundingBoxes, or those of the nearest ancestor that has some (WMS 1.3.0 inheritance)."""
    while layer is not None:
        boxes = getattr(layer, "crs_list", None) or []
        if boxes:
            return boxes
        layer = getattr(layer, "parent", None)
    return []


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
        """Check the options, read GetCapabilities (once per process), then pick layers, extents and a random stream."""
        opts = self.environment.parsed_options
        self.areas, mix = self.check_options(opts)
        self.headers = request_headers(opts)

        self.wms, first_user = self.get_wms(opts)
        self.layers = list(self.wms.contents)

        # Layer mix: --layers 'a:55,b:15', else the single --layer-name, else the first layer
        if not mix:
            if not opts.layer_name:
                logger.info("No --layer-name argument value, using first layer available in service")
            mix = [(opts.layer_name or self.layers[0], 1.0)]
        for name, _ in mix:
            if name not in self.layers:
                ArgError(
                    f"The specified WMS layer '{name}' is not available in WMS service "
                    f"'{self.wms.identification.title}'. Available layers: {self.layers}"
                ).handle_error(self.environment)
        self.layer_names = [name for name, _ in mix]
        self.layer_weights = [weight for _, weight in mix]
        self.extents = {name: self.get_extent(name, opts.crs) for name in self.layer_names}
        for name, (*_, crs) in self.extents.items():
            if not (is_metric(crs) or is_geographic(crs)):  # bboxes are sized in metres or in degrees, nothing else
                ArgError(
                    f"Layer '{name}': CRS {crs} is neither in metres nor in degrees; choose one with --crs"
                ).handle_error(self.environment)
        self.rng = user_rng(self.environment, opts.random_seed)
        if first_user:
            logger.info(f"layers (weight): {list(zip(self.layer_names, self.layer_weights))}")
            logger.info(f"bbox areas in km2: {self.areas}, aspect ratio: {opts.bbox_ratio}, seed: {opts.random_seed}")
            logger.info(f"extents: {self.extents}")

    def check_options(self, opts):
        """(bbox areas, layer mix), checked once at start: a bad value stops the run here, not on every request."""
        try:
            areas = bbox_areas(opts)
        except ValueError as e:
            ArgError(str(e)).handle_error(self.environment)
        mix = [mix_item(item) for item in opts.layers.split(",") if item.strip()]
        problems = []
        if min(areas) <= 0 or opts.bbox_ratio <= 0:
            problems.append(f"bbox areas {areas} and ratio {opts.bbox_ratio} must be greater than 0")
        if any(weight <= 0 for _, weight in mix):
            problems.append(f"--layers weights must be greater than 0: {mix}")
        if opts.auth_password and not opts.auth_user:
            problems.append("--auth-password is set but --auth-user is not")
        if problems:
            ArgError("; ".join(problems)).handle_error(self.environment)
        return areas, mix

    def get_wms(self, opts):
        """(WebMapService, fetched_now): GetCapabilities once per process; a failure stops the whole run."""

        def fetch():
            if opts.auth_user:
                logger.info(f"basic auth as {opts.auth_user}")
                logging.getLogger("owslib").setLevel(logging.WARNING)  # owslib's DEBUG lines may carry auth details
            return WebMapService(
                self.host,
                version="1.3.0",
                auth=owslib_auth(opts),
                headers={"User-Agent": opts.user_agent},
                timeout=120,
            )

        try:
            return cached_capabilities(("wms", self.host, opts.auth_user), fetch)
        except Exception as e:  # unreachable host, 401, not a WMS: stop now instead of running with no users
            ArgError(f"Cannot read the WMS GetCapabilities of {self.host}: {e}").handle_error(self.environment)

    def get_extent(self, layer_name, crs):
        """
        (minx, miny, maxx, maxy, crs) of a layer, in OWSLib's x/y order.

        Boxes are the layer's own, or its nearest ancestor's (WMS 1.3.0 inheritance). With --crs: the box in
        that CRS (EPSG:3857 derived from WGS84 if the server has none). Without: the first box in a metre
        CRS, else the first box, else the WGS84 box in CRS:84 (or EPSG:4326 if CRS:84 is not offered).
        """
        layer = self.wms.contents[layer_name]
        boxes = inherited_boxes(layer)
        wgs84 = getattr(layer, "boundingBoxWGS84", None)
        if not crs:
            for box in boxes:
                if is_metric(box[4]):
                    return tuple(box[:5])
            if boxes:
                return tuple(boxes[0][:5])
            if wgs84:
                offered = getattr(layer, "crsOptions", None) or []
                return (*wgs84[:4], "CRS:84" if "CRS:84" in offered else "EPSG:4326")
            ArgError(f"Layer '{layer_name}' advertises no extent").handle_error(self.environment)
        for box in boxes:
            if box[4] == crs:
                return tuple(box[:5])
        if crs == "EPSG:3857" and wgs84:  # servers often advertise only the native CRS box; derive Web Mercator
            west, south, east, north = wgs84[:4]
            return (*mercator(west, south), *mercator(east, north), crs)
        ArgError(f"Layer '{layer_name}' has no BoundingBox in {crs}; available: {[b[4] for b in boxes]}").handle_error(
            self.environment
        )

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
        return with_params(self.host, params)

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
