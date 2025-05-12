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
        help="Random seed used determine a random col and tile, this is important to prevent server/client caching. If No seed is set then 1640 will be the default value",
    )
    parser.add_argument(
        "--layer-name",
        type=str,
        default=None,
        help="Layer to be used for benchmarking. if not set, will default to service's first layer",
    )

    parser.add_argument(
        "--tile-matrix-set",
        type=str,
        default=None,
        help="Tilematrixset of layer to be used. In not set, if not set, will default to service's first layer",
    )
    parser.add_argument(
        "--tile-matrix",
        type=str,
        default=None,
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
        self.wmts = WebMapTileService(self.host)

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
        response = self.client.get(url_path)


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


# Example running code snippet would be similar to your WMTS benchmark
if __name__ == "__main__":
    # Initialize the environment and parser for Locust
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust import run_single_user
    from locust.env import Environment

    # Initialize the environment
    env = Environment(user_classes=[WMTSBenchmark])
    env.create_local_runner()

    # Manually create an instance of your Locust user class

    wmts_benchmark = WMTSBenchmark(env)
    env.parsed_options = type("", (), {})()  # Create a simple class to hold options

    # Uncomment the host on class (seems there is a bug on locutus to set the host)
    # wmts_benchmark.host = "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0"  # Set the host explicitly
    wmts_benchmark.host = "https://cartografia.dgterritorio.gov.pt/ortos2021/service"

    # Simulate command line arguments
    env.parsed_options.random_seed = 1234

    # PDOK
    # env.parsed_options.layer_name = "I_dont_exist"  #['Actueel_orthoHR', 'Actueel_ortho25', '2024_quickorthoHR', '2023_orthoHR', '2023_ortho25', '2022_orthoHR', '2022_ortho25', '2021_orthoHR', '2020_ortho25', '2019_ortho25', '2018_ortho25', '2017_ortho25', '2016_ortho25']
    # env.parsed_options.layer_name = "2017_ortho25"
    # env.parsed_options.tile_matrix_set= "test" ['EPSG:28992', 'EPSG:3857', 'EPSG:4258', 'EPSG:4326', 'EPSG:25831', 'EPSG:25832', 'OGC:1.0:GoogleMapsCompatible']
    # env.parsed_options.tile_matrix_set = "OGC:1.0:GoogleMapsCompatible" # Most extreme case
    # env.parsed_options.tile_matrix = "07"

    # DGT
    env.parsed_options.random_seed = 1234
    # env.parsed_options.layer_name = "I_dont_exist"  #['Actueel_orthoHR', 'Actueel_ortho25', '2024_quickorthoHR', '2023_orthoHR', '2023_ortho25', '2022_orthoHR', '2022_ortho25', '2021_orthoHR', '2020_ortho25', '2019_ortho25', '2018_ortho25', '2017_ortho25', '2016_ortho25']
    env.parsed_options.layer_name = "Ortos2021-RGB"
    # env.parsed_options.tile_matrix_set= "test" ['EPSG:28992', 'EPSG:3857', 'EPSG:4258', 'EPSG:4326', 'EPSG:25831', 'EPSG:25832', 'OGC:1.0:GoogleMapsCompatible']
    env.parsed_options.tile_matrix_set = "PTTM_06"  # Most extreme case
    env.parsed_options.tile_matrix = "07"

    wmts_benchmark.environment = env

    # Directly call the on_start to use the setup (if any exception handling, do here)
    try:
        wmts_benchmark.on_start()
    except Exception as e:
        print("An error occurred during setup:", str(e))
        exit()

    # Assuming we are just testing the setup, you might not need to run any tasks, but if you do:
    wmts_benchmark.load_tile()  # Directly run the task method
    env.runner.quit()
