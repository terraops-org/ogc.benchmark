"""WMTS GetTile load test (KVP): random tiles of one tile matrix of a layer, drawn where the layer has images."""

import logging
import time
import urllib.parse

from exceptions import ArgError
from locust import FastHttpUser, between, events, task
import requests
from owslib.wmts import WebMapTileService

from utils.ogc import (
    IMAGE,
    add_common_args,
    add_extent_arg,
    cached_capabilities,
    check_response,
    crs_known,
    default_options,
    intersect,
    is_geographic,
    is_metric,
    mark_empty,
    owslib_auth,
    request_headers,
    parse_extent,
    user_rng,
)
from utils.tiles import (
    Coverage,
    digest,
    draw_tile,
    find_coverage,
    probe_level,
    tile_box,
    tile_range,
    to_crs_box,
    wmts_matrix,
)

logger = logging.getLogger(__name__)


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser, bbox=False)
    parser.add_argument(
        "--layer-name",
        type=str,
        default=None,
        env_var="LOCUST_LAYER_NAME",
        help="Layer to benchmark (default: the service's first layer)",
    )
    parser.add_argument(
        "--tile-matrix-set",
        type=str,
        default=None,
        env_var="LOCUST_TILE_MATRIX_SET",
        help="TileMatrixSet of the layer (default: the layer's first)",
    )
    parser.add_argument(
        "--tile-matrix",
        type=str,
        default=None,
        env_var="LOCUST_TILE_MATRIX",
        help="TileMatrix (zoom level) of the TileMatrixSet (default: the middle level of the pyramid)",
    )
    add_extent_arg(parser)


class WMTSBenchmark(FastHttpUser):
    """GetTile requests for random tiles (col, row) of one tile matrix."""

    wait_time = between(1, 2)  # 1-2 s between requests per simulated user
    host = ""  # without a class attribute the __main__ example cannot set the host

    def on_start(self):
        """Read GetCapabilities, then pick layer, TileMatrixSet, TileMatrix and this user's random stream."""
        opts = self.environment.parsed_options
        self.verify = opts.verify_tls
        self.headers = request_headers(opts)
        try:  # once per process; unreachable host or not a WMTS: stop now instead of running with no users
            self.wmts, _ = cached_capabilities(
                ("wmts", self.host),
                lambda: WebMapTileService(self.host, auth=owslib_auth(opts), headers={"User-Agent": opts.user_agent}),
            )
        except Exception as e:
            ArgError(f"Cannot read the WMTS GetCapabilities of {self.host}: {e}").handle_error(self.environment)

        self.layers = list(self.wmts.contents.keys())
        self.layer_name = opts.layer_name
        if self.layer_name:
            if self.layer_name not in self.layers:
                ArgError(
                    f"The specified layer '{self.layer_name}' is not available in the WMTS service. "
                    f"Available layers in service: {self.layers}"
                ).handle_error(self.environment)
        else:
            logger.info("No --layer-name argument, using first layer available in service")
            self.layer_name = self.layers[0]
        self.layer = self.wmts.contents[self.layer_name]
        self.layer_mimetype = urllib.parse.quote(self.layer.formats[0])  # the layer's first format

        self.tile_matrix_sets = list(self.layer.tilematrixsetlinks.keys())
        self.tile_matrix_set = opts.tile_matrix_set
        if self.tile_matrix_set:
            if self.tile_matrix_set not in self.tile_matrix_sets:
                ArgError(
                    f"The specified tilematrixset '{self.tile_matrix_set}' is not available in layer "
                    f"'{self.layer_name}'. Available tilematrixsets in layer: {self.tile_matrix_sets}"
                ).handle_error(self.environment)
        else:
            self.tile_matrix_set = self.tile_matrix_sets[0]
            logger.info(f"No --tile-matrix-set argument, using {self.tile_matrix_set}, first available in layer")

        matrices = self.wmts.tilematrixsets.get(self.tile_matrix_set).tilematrix
        self.tile_matrix_value = opts.tile_matrix
        if self.tile_matrix_value:
            if self.tile_matrix_value not in matrices:
                ArgError(
                    f"The specified tilematrix '{self.tile_matrix_value}' is not in tilematrixset "
                    f"{self.tile_matrix_set}. Available values are {list(matrices)}"
                ).handle_error(self.environment)
        else:
            self.tile_matrix_value = list(matrices)[len(matrices) // 2]
            logger.info(f"No --tile-matrix argument, using the middle tilematrix {self.tile_matrix_value}")

        matrix = matrices[self.tile_matrix_value]
        self.layer_width = matrix.matrixwidth  # number of columns
        self.layer_height = matrix.matrixheight  # number of rows
        self.find_images(opts, list(matrices.values()))
        self.rng = user_rng(self.environment, opts.random_seed)
        logger.info(
            f"layer {self.layer_name}, TileMatrixSet {self.tile_matrix_set}, TileMatrix {self.tile_matrix_value} "
            f"({self.layer_width} x {self.layer_height} tiles), seed {opts.random_seed}"
        )

    def find_images(self, opts, matrices):
        """
        Set self.target, self.area and self.coverage: where on the grid the layer has images.

        The area is the TileMatrixSetLimits of the chosen matrix if the layer has them, else its WGS84 bbox
        narrowed by --extent. A one-off probe (once per process) then fetches the tiles of a coarse level inside
        it, so the draws land under tiles with an image instead of all over the grid.
        """
        crs = self.wmts.tilematrixsets[self.tile_matrix_set].crs
        try:
            extent = parse_extent(opts.extent)
        except ValueError as e:
            ArgError(str(e)).handle_error(self.environment)
        known = crs_known(crs)
        if not known:  # the grid still works in its own units; only lon/lat areas need the CRS
            if extent:
                ArgError(
                    f"--extent needs a CRS pyproj knows; TileMatrixSet {self.tile_matrix_set} has {crs}"
                ).handle_error(self.environment)
            logger.warning(f"pyproj does not know CRS {crs}: grid taken in metres, area = the whole tile matrix")
        elif not (is_metric(crs) or is_geographic(crs)):
            ArgError(
                f"TileMatrixSet {self.tile_matrix_set}: CRS {crs} is neither in metres nor in degrees"
            ).handle_error(self.environment)
        levels = [wmts_matrix(tm, crs) for tm in matrices]
        self.target = next(m for m in levels if m.id == str(self.tile_matrix_value))
        limits = self.layer.tilematrixsetlinks[self.tile_matrix_set].tilematrixlimits or {}
        if self.tile_matrix_value in limits:
            lim = limits[self.tile_matrix_value]
            low = tile_box(self.target, lim.mintilecol, lim.maxtilerow)
            high = tile_box(self.target, lim.maxtilecol, lim.mintilerow)
            self.area = (low[0], low[1], high[2], high[3])
            if extent:  # --extent narrows the limits too
                self.area = intersect(self.area, to_crs_box(extent, crs))
                if self.area is None:
                    ArgError(
                        f"--extent {opts.extent} does not overlap the TileMatrixSetLimits of {self.target.id}"
                    ).handle_error(self.environment)
        elif not known:
            low = tile_box(self.target, 0, self.target.height - 1)
            high = tile_box(self.target, self.target.width - 1, 0)
            self.area = (low[0], low[1], high[2], high[3])
        else:
            wgs84 = tuple(self.layer.boundingBoxWGS84 or (-180.0, -90.0, 180.0, 90.0))[:4]
            box = intersect(wgs84, extent) if extent else wgs84
            if box is None:
                ArgError(f"--extent {opts.extent} does not overlap the layer's WGS84 bbox {wgs84}").handle_error(
                    self.environment
                )
            self.area = to_crs_box(box, crs)
        if tile_range(self.target, self.area) is None:
            ArgError(f"The area {self.area} is outside TileMatrix {self.target.id}").handle_error(self.environment)

        level = probe_level(levels, self.target, self.area)
        if level is None:
            self.coverage = Coverage(self.target, (), None, 0)
            logger.warning(
                "no level small enough to probe (at most 256 tiles in the area); drawing over the whole area"
            )
            return
        key = ("wmts-probe", self.host, self.layer_name, self.tile_matrix_set, self.target.id, self.area)
        started = time.monotonic()
        self.coverage, probed_now = cached_capabilities(
            key, lambda: find_coverage(self.probe_fetch(level), level, self.area)
        )
        if probed_now:
            logger.info(
                f"probe level {level.id}: {len(self.coverage.tiles)} of {self.coverage.fetched} tiles have images "
                f"({time.monotonic() - started:.1f} s)"
            )
            if not self.coverage.tiles:
                logger.warning(f"no tile with an image at probe level {level.id}; drawing over the whole area")

    def probe_fetch(self, level):
        def fetch(col, row):
            resp = requests.get(self.tile_url(level.id, row, col), headers=self.headers, timeout=60, verify=self.verify)
            return resp.status_code, resp.content

        return fetch

    def tile_url(self, matrix_id, row, col):
        """The GetTile (KVP) URL of one tile of this layer and TileMatrixSet."""
        return (
            f"{self.host}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER={self.layer_name}&STYLE=default"
            f"&FORMAT={self.layer_mimetype}&TILEMATRIXSET={self.tile_matrix_set}&TILEMATRIX={matrix_id}"
            f"&TILEROW={row}&TILECOL={col}"
        )

    @task
    def load_tile(self):
        """A GetTile (KVP) for a random tile where the layer has images; anything but a 200 image is a failure."""
        tile_col, tile_row = draw_tile(self.rng, self.target, self.area, self.coverage)
        url_path = self.tile_url(self.tile_matrix_value, tile_row, tile_col)
        with self.client.get(url_path, headers=self.headers, catch_response=True, name="GetTile") as response:
            if check_response(response, IMAGE) and self.coverage.blank == digest(response.content or b""):
                mark_empty(response)


# Example: one GetTile against a public PDOK WMTS (first TileMatrixSet, middle TileMatrix, after the probe)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[WMTSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--layer-name", "Actueel_orthoHR")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception)
    )

    user = WMTSBenchmark(env)
    user.host = "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0"
    user.on_start()
    user.load_tile()
    env.runner.quit()
