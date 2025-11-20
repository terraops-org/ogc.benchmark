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
        env_var="LOCUST_RANDOM_SEED",
        help="Random seed used to generate the bbox, this is important to prevent server/client caching. Usefull to replicate random bbox. If no seed then 1640 value will be used",
    )
    parser.add_argument(
        "--bbox-area",
        type=float,
        default=100.0,
        env_var="LOCUST_BBOX_AREA",
        help="Bounding box area in square meters, depending on projection used. Defaults is 100km2 or 10x10km on bbox-ratio=1.0",
    )
    parser.add_argument(
        "--bbox-ratio",
        type=float,
        default=1.0,
        env_var="LOCUST_BBOX_RATIO",
        help="Bounding box width/height ratio, better to keep it as 1.0 (square)",
    )
    parser.add_argument(
        "--layer-name",
        type=str,
        env_var="LOCUST_LAYER_NAME",
        help="Layer to be used for benchmarking. if not set, will default to service's first layer",
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
        try:
            self.wms = WebMapService(self.host)
        except Exception as e:
            logger.error(f"Failed to connect to WMS service at {self.host}: {e}")
            self.environment.runner.quit()
            raise

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
        else:  # user didn't define layer - intelligently select best one
            logger.info(f"No --layer-name argument, selecting best layer from service")
            self.layer_name = self._select_best_layer()
            logger.info(f"Auto-selected layer: '{self.layer_name}'")

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
        epsg_code = self.layer.boundingBox[-1]
        return epsg_code

    def get_layers(self):
        """
        Fetch and parse the GetCapabilities document to retrieve layer names.
        """
        layers = list()
        for layer in self.wms.contents:
            layers.append(layer)
        return layers

    def _select_best_layer(self):
        """
        Select the best layer for benchmarking when no layer is specified.
        Prefers layers with projected coordinate systems (EPSG:3xxx, 2xxx, 32xxx)
        over geographic (EPSG:4xxx) or web mercator (EPSG:3857).
        Skips parent/group layers that may have overly broad extents.
        """
        preferred_layers = []
        fallback_layers = []

        for layer_name in self.layers:
            layer = self.wms.contents[layer_name]
            crs = layer.boundingBox[-1] if layer.boundingBox else None

            if not crs:
                continue

            # Extract EPSG code number
            epsg_code = int(crs.split(":")[1]) if ":" in crs else 0

            # Prefer projected coordinate systems (better for area-based bbox generation)
            # EPSG:3xxx (except 3857), EPSG:2xxx, EPSG:32xxx (UTM)
            if (2000 <= epsg_code < 4000 and epsg_code != 3857) or (32600 <= epsg_code < 32800):
                preferred_layers.append(layer_name)
            else:
                fallback_layers.append(layer_name)

        # Return first preferred layer, or fallback to first available
        if preferred_layers:
            return preferred_layers[0]
        elif fallback_layers:
            return fallback_layers[0]
        else:
            return self.layers[0]

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

        # Making the GET request to load the map
        logger.debug(f"Layer: {self.layer_name}, CRS: {self.crs}, BBOX: {bbox_str}")
        logger.debug(f"URL for request: {url_path}")

        with self.client.get(url_path, catch_response=True, name="GetMap") as response:
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


# Example running code for testing the WMS benchmark
if __name__ == "__main__":
    print("WMS Benchmark class - Use with locust command line tool:")
    print("locust -f wms.py --host https://example.com/wms")
    print("Available options: --layer-name, --bbox-area, --bbox-ratio, --random-seed")
    print("Environment variables: LOCUST_LAYER_NAME, LOCUST_BBOX_AREA, etc.")
