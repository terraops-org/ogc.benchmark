# Arguments not shown on --help bug: https://github.com/locustio/locust/issues/2917
import sys, os
import random
import urllib.parse
from exceptions import LayerNameArgError
from locust import FastHttpUser, events, task, between, run_single_user
from owslib.wms import WebMapService

from utils.random_bbox import generate_random_bbox

import requests
import logging
logger = logging.getLogger(__name__)

# extend command line to allow a random-seed (for random bbox creation) and
# argument for bbox area
@events.init_command_line_parser.add_listener
def init_parser(parser):
    parser.add_argument(
        "--random-seed",
        type=int,
        default=1640,
        help="Random seed used to generate the bbox, this is important to prevent server/client caching. Usefull to replicate random bbox. If no seed then 1640 value will be used",
    )
    parser.add_argument(
        "--bbox-area",
        type=float,
        default=100.0,
        help="Bounding box area in square meters, depending on projection used. Defaults is 100km2 or 10x10km on bbox-ratio=1.0",
    )
    parser.add_argument(
        "--bbox-ratio",
        type=float,
        default=1.0,
        help="Bounding box width/height ratio, better to keep it as 1.0 (square)",
    )
    parser.add_argument(
        "--layer-name",
        type=str,
        help="Layer to be used for benchmarking. if not set, will default to service's first layer",
    )
    parser.add_argument(
        "--extra-payload",
        type=str,
        help="Extra parameters to be added to url request e.g 'api_key=4565'",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="API key to be passed as X-API-Key header for authentication",
    )


class WMSBenchmark(FastHttpUser):
    """
    A class to model the benchmarking of a WMS layer using Locust.
    """

    # TODO: extend code to allow seetting CRS's EPGS available

    # The base URL for the host
    # Without this predefined class atribute the __main__ code can't set the host. It seems like a bug on locust
    host = ""

    # Defines a wait time of 1 to 2 seconds between consecutive tasks executed by a simulated user
    wait_time = between(1, 2)

    def on_start(self):
        """
        On start, fetch the WMS capabilities to determine layers and other parameters.
        """
        extra_payload = self.environment.parsed_options.extra_payload
        if extra_payload:
            url_with_payload = f"{self.host}?{extra_payload}"
            print(extra_payload)
        else:
            url_with_payload = f"{self.host}?"

        # Build headers dict if API key is provided
        self.api_key = self.environment.parsed_options.api_key
        self.headers = {}
        if self.api_key:
            self.headers["X-API-Key"] = self.api_key
            logger.info(f"Using X-API-Key header for authentication")

        self.wms = WebMapService(url_with_payload, headers=self.headers)
        
        self.layers = self.get_layers()
        self.layer_name = self.environment.parsed_options.layer_name
        if self.layer_name:
            if self.layer_name not in self.layers:
                error_message = (
                    f"The specified WMS layer '{self.layer_name}' is not available WMS service'{self.wms.identification.title}'. "
                    f"Please check the layer name for any typos or omit the '--layer-name' argument to use the first available layer.  "
                    f"Available layers: {self.layers}"
                )
                raise LayerNameArgError(error_message)
            else:
                pass  # all good with layer name
        else:  # user didnt defined layer
            logger.info(f"No --layer-name argument, using first layer available in service")
            self.layer_name = self.layers[0]  # we will only use the first layer

        self.layer = self.wms.contents[self.layer_name]

        # get layej proj
        # self.srs = "EPSG:3763"  #'EPSG:3763' layer.boundingBox[-1] # Assuming EPSG:3763 for demonstration. Extract from GetCapabilities for dynamic approach.
        self.crs = self.get_crs()
        # self.layer_mimetype = urllib.parse.quote(
        # For now the first format available later to do also as argument
        self.layer_mimetype = self.wms.getOperationByName("GetMap").formatOptions[0]  # ['image/jpeg']

        self.bbox = self.get_bbox()  # A generic BBOX, ideally parse GetCapabilities for valid ranges.

        self.bbox_generator = generate_random_bbox(
            *self.get_bbox(),
            area=self.environment.parsed_options.bbox_area,
            ratio=self.environment.parsed_options.bbox_ratio,
            seed=self.environment.parsed_options.random_seed,
        )
        logger.info(f"bbox area in km2:{self.environment.parsed_options.bbox_area}")
        logger.info(f"bbox aspect ration:{self.environment.parsed_options.bbox_ratio}")
        logger.info(f"bbox random seed:{self.environment.parsed_options.random_seed}")

    def get_bbox(self):
        """
        Get the bbox from layer.boundinBox
        """
        return (
            self.layer.boundingBox[0],
            self.layer.boundingBox[1],
            self.layer.boundingBox[2],
            self.layer.boundingBox[3],
        )

    def get_crs(self):
        """
        Gets the src from owslib layer's bbox, the last element is the srs information as EPSG:3763
        """
        # TODO: check if we get a real EPSG code
        # ['EPSG:4258', 'EPSG:3857', 'EPSG:25831', 'EPSG:25832', 'EPSG:28992', 'EPSG:4326']
        epgs_code = self.layer.boundingBox[-1]
        return epgs_code

    def get_layers(self):
        """
        Fetch and parse the GetCapabilities document to retrieve layer names.
        """
        layers = list()
        for layer in self.wms.contents:
            layers.append(layer)
        return layers

    @task
    def load_map(self):
        """
        A task that loads a map image from the WMS layer using a random BBOX.
        """
        # For simplicity, selecting the first layer. Adjust as needed.
        # layer = self.layers[0] if self.layers else "no-layer-available"

        # Lets get a random bbx
        bbox = next(self.bbox_generator)
        # bbox = next(generate_random_bbox(*self.bbox))
        bbox_str = ",".join(map(str, bbox))

        # Constructing the URL path #&srs={self.srs}
        url_path = f"{self.host}?service=WMS&version=1.3.0&request=GetMap&layers={self.layer_name}&styles=&bbox={bbox_str}&width=512&height=512&crs={self.crs}&format={self.layer_mimetype}"
        if self.environment.parsed_options.extra_payload:
            url_path=f"{url_path}&{self.environment.parsed_options.extra_payload}"         


        # Making the GET request to load the map
        logger.debug(f"URL for request: {url_path}")
        response = self.client.get(url_path, headers=self.headers)


# Example running code snippet would be similar to your WMTS benchmark
if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust import run_single_user
    from locust.env import Environment

    # Initialize the environment
    env = Environment(user_classes=[WMSBenchmark])
    env.create_local_runner()

    wms_benchmark = WMSBenchmark(env)
    env.parsed_options = type("", (), {})()  # Create a simple class to hold options

    # wms_benchmark.host = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
    wms_benchmark.host = "https://ortos.dgterritorio.gov.pt/wms/ortoimagens2023"
    env.parsed_options.random_seed = 72
    env.parsed_options.bbox_area = 100  # square km (10km x 10km)
    env.parsed_options.bbox_ratio = 1.0
    env.parsed_options.layer_name = "Ortoimagens2023-RGB"
    env.parsed_options.extra_payload = None
    env.parsed_options.api_key = None

    wms_benchmark.environment = env
    # Directly call the on_start to use the setup (if any exception handling, do here)
    try:
        wms_benchmark.on_start()
    except Exception as e:
        print("An error occurred during setup:", str(e))
        exit()

    # Assuming we are just testing the setup, you might not need to run any tasks, but if you do:
    wms_benchmark.load_map()  # Directly run the task method
    env.runner.quit()
