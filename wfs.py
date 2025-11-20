# Ensure monkey-patching is done early
from gevent import monkey

monkey.patch_all()

import logging
import requests
from owslib.wfs import WebFeatureService
from exceptions import LayerNameArgError
from locust import FastHttpUser, events, task, between, run_single_user
from utils import get_feature_count
from utils.random_bbox import generate_random_bbox_degrees

# Set up logging for debug messages
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")


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
        "--feature-name",
        type=str,
        default=None,
        env_var="LOCUST_FEATURE_NAME",
        help="Feature name (layer) to be used for benchmarking, with namespace e.g: 'au:AU.AdministrativeUnits'. If not set, will default to service's first  Feature on GetCapabilities (layer)",
    )


class WFSBenchmark(FastHttpUser):
    # Defines a wait time of 1 to 2 seconds between consecutive tasks executed by a simulated user
    wait_time = between(1, 2)
    host = ""

    def on_start(self):
        # Fetch WFS Capabilities to determine features and parameters
        try:
            self.wfs20 = WebFeatureService(self.host, parse_remote_metadata=False, version="2.0.0")
        except Exception as e:
            logging.error(f"Failed to connect to WFS service at {self.host}: {e}")
            self.environment.runner.quit()
            raise
        self.feature_names = self.get_features()
        self.feature_name = self.environment.parsed_options.feature_name
        if self.feature_name:
            if self.feature_name not in self.feature_names:
                raise LayerNameArgError(
                    f"Feature '{self.feature_name}' is not available. Available: {self.feature_names}"
                )
        else:
            logging.info("No --feature-name argument, using the first feature.")
            self.feature_name = self.feature_names[0]

        self.feature = self.wfs20.contents[self.feature_name]
        self.crs = self.get_crs()
        self.bbox_generator = generate_random_bbox_degrees(
            *self.get_bbox(),
            area=self.environment.parsed_options.bbox_area,
            ratio=self.environment.parsed_options.bbox_ratio,
            seed=self.environment.parsed_options.random_seed,
        )

    def get_features(self):
        return list(self.wfs20.contents.keys())

    def get_crs(self):
        crs_list = self.feature.crsOptions
        return crs_list[0] if crs_list else "No CRS available"

    def get_bbox(self):
        # -9.426773760966627,38.207022489844576,-9.05457,38.4911
        # bbox = min Longitude , min Latitude , max Longitude , max Latitude
        return self.feature.boundingBoxWGS84

    @task
    def load_features(self):
        bbox = next(self.bbox_generator)
        bbox_str = ",".join(map(str, bbox))
        url_path = f"{self.host}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAME={self.feature_name}&BBOX={bbox_str}&COUNT=1000&crs={self.crs}"
        logging.debug(f"URL for request: {url_path}")

        with self.client.get(url_path, catch_response=True, name="GetFeature") as response:
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "").lower()
                if "xml" in content_type or "gml" in content_type:
                    if "exception" in response.text.lower():
                        response.failure(f"OGC Exception: {response.text[:200]}")
                    else:
                        response.success()
                elif "json" in content_type:
                    response.success()
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


# Example running code for testing the WFS benchmark
if __name__ == "__main__":
    print("WFS Benchmark class - Use with locust command line tool:")
    print("locust -f wfs.py --host https://example.com/wfs")
    print("Available options: --feature-name, --bbox-area, --bbox-ratio, --random-seed")
    print("Environment variables: LOCUST_FEATURE_NAME, LOCUST_BBOX_AREA, etc.")
