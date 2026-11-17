"""WMTS GetTile load test (KVP): random tiles of one tile matrix of a layer."""

import logging
import urllib.parse

from exceptions import ArgError
from locust import FastHttpUser, between, events, task
from owslib.wmts import WebMapTileService

from utils.ogc import IMAGE, add_common_args, cached_capabilities, check_response, default_options, user_rng

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


class WMTSBenchmark(FastHttpUser):
    """GetTile requests for random tiles (col, row) of one tile matrix."""

    wait_time = between(1, 2)  # 1-2 s between requests per simulated user
    host = ""  # without a class attribute the __main__ example cannot set the host

    def on_start(self):
        """Read GetCapabilities, then pick layer, TileMatrixSet, TileMatrix and this user's random stream."""
        opts = self.environment.parsed_options
        try:  # once per process; unreachable host or not a WMTS: stop now instead of running with no users
            self.wmts, _ = cached_capabilities(("wmts", self.host), lambda: WebMapTileService(self.host))
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
        self.rng = user_rng(self.environment, opts.random_seed)
        logger.info(
            f"layer {self.layer_name}, TileMatrixSet {self.tile_matrix_set}, TileMatrix {self.tile_matrix_value} "
            f"({self.layer_width} x {self.layer_height} tiles), seed {opts.random_seed}"
        )

    @task
    def load_tile(self):
        """A GetTile (KVP) for a random column and row; anything but a 200 image is a failure."""
        tile_col = self.rng.randint(0, self.layer_width - 1)
        tile_row = self.rng.randint(0, self.layer_height - 1)
        url_path = (
            f"{self.host}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER={self.layer_name}&STYLE=default"
            f"&FORMAT={self.layer_mimetype}&TILEMATRIXSET={self.tile_matrix_set}&TILEMATRIX={self.tile_matrix_value}"
            f"&TILEROW={tile_row}&TILECOL={tile_col}"
        )
        with self.client.get(url_path, catch_response=True, name="GetTile") as response:
            check_response(response, IMAGE)


# Example: one GetTile against a public PDOK WMTS (first TileMatrixSet, middle TileMatrix)
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
