import sys, random
import urllib.parse
from exceptions import LayerNameArgError, TileMatrixSetArgError, TileMatrixArgError
from locust import FastHttpUser, events, task, between
from owslib.wmts import WebMapTileService

import logging

logger = logging.getLogger(__name__)


# extend command line to allow a bbox-random-seed
@events.init_command_line_parser.add_listener
def init_parser(parser):
    parser.add_argument(
        "--random-seed",
        type=int,
        default=1640,
        env_var="LOCUST_RANDOM_SEED",
        help="Random seed used determine a random col and tile, this is important to prevent server/client caching. If No seed is set then 1640 will be the default value",
    )
    parser.add_argument(
        "--layer-name",
        type=str,
        default=None,
        env_var="LOCUST_LAYER_NAME",
        help="Layer to be used for benchmarking. if not set, will default to service's first layer",
    )

    parser.add_argument(
        "--tile-matrix-set",
        type=str,
        default=None,
        env_var="LOCUST_TILE_MATRIX_SET",
        help="Tilematrixset of layer to be used. In not set, if not set, will default to service's first layer",
    )
    parser.add_argument(
        "--tile-matrix",
        type=str,
        default=None,
        env_var="LOCUST_TILE_MATRIX",
        help="Tilematrix of Tilematrixset to be used. In not set, if not set, will determine the median level of pyramid (Tilematrixset) and use it",
    )


# Ex
class WMTSBenchmark(FastHttpUser):
    """
    A class to model the benchmarking of a WMS layer using Locust.
    """

    # The base URL for the host

    # Defines a wait time of 1 to 2 seconds between consecutive tasks executed by a simulated user
    wait_time = between(1, 2)
    # host = "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0"  # Uncomment and set this as the default
    host = ""

    def on_start(self):
        """
        On start, fetch the WMtS capabilities to determine layers and tilematrixsets.
        /hwh/luchtfotorgb/wmts/v1_0/{layer_identifier}/{tile_matrix_set}/{tile_matrix}/{tile_col}/{tile_row}.jpeg"
        """
        try:
            self.wmts = WebMapTileService(self.host)
        except Exception as e:
            logger.error(f"Failed to connect to WMTS service at {self.host}: {e}")
            self.environment.runner.quit()
            raise

        self.layers = list(self.wmts.contents.keys())
        ##### TO BE REFACTORED ####
        self.layer_name = self.environment.parsed_options.layer_name
        if self.layer_name:  # user implemented argument

            if self.layer_name not in self.layers:
                error_message = (
                    f"The specified layer '{self.layer_name}' is not available in the WMTS service. "
                    f"Please check the layer name for any typos or omit the '--layer-name' argument to use the first available layer.  "
                    f"Available layers in service: {self.layers}"
                )

                raise LayerNameArgError(error_message)

        else:  # user didnt defined layer
            logger.info(f"No --layer-name argument, using first layer available in service")
            self.layer_name = self.layers[0]
        self.layer = self.wmts.contents[self.layer_name]
        self.layer_mimetype = urllib.parse.quote(
            self.layer.formats[0]
        )  # For now the first format available later to do also as argument
        ###### END TO BE REFACTORED ####
        self.tile_matrix_sets = list(self.layer.tilematrixsetlinks.keys())
        self.tile_matrix_set = self.environment.parsed_options.tile_matrix_set
        if self.tile_matrix_set:
            if self.tile_matrix_set not in self.tile_matrix_sets:
                error_message = (
                    f"The specified tilematrixset '{self.tile_matrix_set}' is not available in layer '{self.layer_name}'. "
                    f"Please check the layer name for any typos or omit the '--tile-matrix-set' argument to use the first available tilematrixset of the  layer.  "
                    f"Available tilematrixsets in layer: {self.tile_matrix_sets}"
                )

                raise TileMatrixSetArgError(error_message)
        else:

            self.tile_matrix_set = self.tile_matrix_sets[
                0
            ]  # Picking up 1st tile matrix, assuming all layers have the same matrix set (this is an incorrect assumption)
            logger.info(
                f"No --tile-matrix-set argument, using tilematrixset {self.tile_matrix_set}, first available in layer"
            )
        self.tile_matrix_value = self.environment.parsed_options.tile_matrix

        if self.tile_matrix_value:
            # Let op!  self.tile_matrix_set --> name of tilematrixset
            tile_matrix_set_obj = self.wmts.tilematrixsets.get(self.tile_matrix_set)

            if (
                self.tile_matrix_value not in tile_matrix_set_obj.tilematrix.keys()
            ):  # tile_matrix_obj=self.layer.tilematrixsets[self.tile_matrix_set]
                error_message = (
                    f"The specificed tilematrix argument value is not on tilematrixset {self.tile_matrix_set}   "
                    f"Available values are {tile_matrix_set_obj.tilematrix.keys()}"
                )
                raise TileMatrixArgError(error_message)
        else:

            tile_matrix_set_obj = self.wmts.tilematrixsets.get(self.tile_matrix_set)
            tile_matrix_list = list(tile_matrix_set_obj.tilematrix.keys())
            middle_index = len(tile_matrix_list) // 2
            self.tile_matrix_value = tile_matrix_list[middle_index]

            logger.info(f"No --tile-matrix argument, using mid tilematrix value of {self.tile_matrix_value}")

        self.layer_tiles = self.wmts.tilematrixsets.get(self.tile_matrix_set).tilematrix[self.tile_matrix_value]

        self.layer_width = self.layer_tiles.matrixwidth  # max number cols
        self.layer_height = self.layer_tiles.matrixheight  # max number rows
        # wmts has row-1 and col-1 as standard #
        self.gen_col = random_number_generator(
            0, self.layer_width - 1, seed=self.environment.parsed_options.random_seed
        )
        # self.gen_col=random_number_generator(14, 490-1, seed=self.environment.parsed_options.random_seed)
        # seed for row is seed+1 otherwise cols and row sequence would be identical, one init seed value used for 2 purposes
        self.gen_row = random_number_generator(
            0,
            self.layer_height - 1,
            seed=self.environment.parsed_options.random_seed + 1,
        )
        # self.gen_row=random_number_generator(4, 985-1, seed=self.environment.parsed_options.random_seed+1)
        logger.info(f"Using tile random seed:{self.environment.parsed_options.random_seed}")
        logger.info(f"Using layer named: {self.layer_name}")
        logger.info(f"Using TileMatrixSet: {self.tile_matrix_set}")
        logger.info(f"Using TileMatrix: {self.tile_matrix_value}")
        logger.info(f"Using TileMatrixWidth:{self.layer_width}")
        logger.info(f"Using TileMatrixHeight:{self.layer_height}")

    def get_layer_tiles(self):
        """Having a matrix set get the middle zoom set"""
        layer_tile_matrixset = self.wmts.tilematrixsets.get(self.tile_matrix_set)
        layer_tile_matrixes = list(layer_tile_matrixset.tilematrix.keys())
        middle_index = len(layer_tile_matrixes) // 2
        layer_tile_matrix = layer_tile_matrixes[middle_index]

        layer_tiles = layer_tile_matrixset.tilematrix.get(layer_tile_matrix)
        return layer_tiles

    @task
    def load_tile(self):
        """
        A task that loads a specific tile from the WMTS layer using random TileCol and TileRow values.
        Information on tile_matrix, tile_col and row obtain from GetCapabilities request
        https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0
        /hwh/luchtfotorgb/wmts/v1_0/{layer_identifier}/{tile_matrix_set}/{tile_matrix}/{tile_col}/{tile_row}.jpeg"

        url_path = f"https://cartografia.dgterritorio.gov.pt/ortos2021/service?SERVICE=WMTS&REQUEST=GetTile
        &VERSION=1.0.0&LAYER={layer_identifier}&STYLE=default
        &FORMAT=image%2Fpng
        &TILEMATRIXSET={tile_matrix_set}&TILEMATRIX={tile_matrix}&TILEROW={tile_row}&TILECOL={tile_col}"

        """
        # Constructing the URL path for the specific tile request

        tile_col = next(self.gen_col)
        tile_row = next(self.gen_row)

        # url_path = f"{self.host}/{self.layer_name}/{self.tile_matrix_set}/{self.tile_matrix_value}/{tile_col}/{tile_row}.jpeg"
        url_path = f"{self.host}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER={self.layer_name}&STYLE=default&FORMAT={self.layer_mimetype}&TILEMATRIXSET={self.tile_matrix_set}&TILEMATRIX={self.tile_matrix_value}&TILEROW={tile_row}&TILECOL={tile_col}"
        # url_path=f"{self.host}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=Interface_E2018&STYLE=AE-Interface_Estrutural_2018&FORMAT=image%2Fpng&TILEMATRIXSET=EPSG:3763&TILEMATRIX=PT-TM06:9&TILEROW={tile_row}&TILECOL={tile_col}"

        with self.client.get(url_path, catch_response=True, name="GetTile") as response:
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("image/"):
                    response.success()
                elif "xml" in content_type and "exception" in response.text.lower():
                    response.failure(f"OGC Exception: {response.text[:200]}")
                else:
                    response.failure(f"Invalid content type: {content_type}")
            else:
                # Categorize errors for better reporting
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code in [502, 503, 504]:
                    # Server overload/unavailability - common during load testing
                    response.failure(f"Server Overload: HTTP {response.status_code}")
                elif "xml" in content_type and response.text:
                    # OGC service exception
                    response.failure(f"OGC Error {response.status_code}: {response.text[:300]}")
                else:
                    # Other HTTP errors
                    response.failure(f"HTTP {response.status_code}")


def random_number_generator(min_value, max_value, seed=None):
    """
    A generator function that yields random numbers between min_value and max_value,
    always producing the same sequence for a given seed.

    Parameters:
    - min_value: the minimum value in the range.
    - max_value: the maximum value in the range (included in distribution).
    - seed: an optional seed to initialize the random number generator.
    """
    # Initialize the random number generator with the specified seed
    random.seed(seed)

    # Infinite loop to continuously yield random numbers
    while True:
        yield random.randint(min_value, max_value)


# Example running code for testing the WMTS benchmark
if __name__ == "__main__":
    print("WMTS Benchmark class - Use with locust command line tool:")
    print("locust -f wmts.py --host https://example.com/wmts")
    print("Available options: --layer-name, --tile-matrix-set, --tile-matrix, --random-seed")
    print("Environment variables: LOCUST_LAYER_NAME, LOCUST_TILE_MATRIX_SET, etc.")
