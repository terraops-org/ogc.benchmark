import logging
import requests
from owslib.wfs import WebFeatureService
from utils import get_feature_count
from utils.random_bbox import generate_random_bbox

# Set up logging for debug messages
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# WFS service URL
HOST = "https://geo2.dgterritorio.gov.pt/geoserver/au/wfs"

# Initialize the WebFeatureService with WFS version 2.0.0
wfs20 = WebFeatureService(url=HOST, version="2.0.0")

# Get the available feature types
feature_types = wfs20.contents

# Print the available feature types, their default CRS, bbox, and number of features
print("Feature Types, Default CRS, Bounding Box (bbox), and Number of Features:")

for feature_name, feature_metadata in feature_types.items():
    # Get the list of available CRS for each feature type
    crs_list = feature_metadata.crsOptions

    # Check if CRS list is available and get the first one as the default
    default_crs = crs_list[0] if crs_list else "No CRS available"

    # Get the bounding box (in WGS84 coordinates)
    bbox = feature_metadata.boundingBoxWGS84

    # Format bbox as a string for readability
    bbox_str = f"({bbox[0]}, {bbox[1]}) to ({bbox[2]}, {bbox[3]})" if bbox else "No bbox available"

    # Get the number of features using a custom GetFeature request with resultType=hits
    number_of_features = get_feature_count(feature_name, host=HOST)

    print(f"\nFeature: {feature_name}")
    print(f"Default CRS: {default_crs}")
    print(f"Bounding Box (WGS84): {bbox_str}")
    print(f"Number of Features: {number_of_features}")

    print("Second part, getting the features on the bbox")

# Use the first feature type for demonstration
first_feature_name = list(feature_types.keys())[0]
first_feature_metadata = feature_types[first_feature_name]

# Extract the bounding box of the first feature in WGS84 coordinates
full_bbox = first_feature_metadata.boundingBoxWGS84

# Parameters for random bbox generation
area = 1  # Example: 50 square kilometers
ratio = 1.0  # A square bbox (width/height = 1.0)
seed = 42  # Seed for deterministic randomness

# Generate a random bbox using the bounding box of the first feature
random_bbox_gen = generate_random_bbox(
    full_bbox_minx=full_bbox[0],
    full_bbox_miny=full_bbox[1],
    full_bbox_maxx=full_bbox[2],
    full_bbox_maxy=full_bbox[3],
    seed=seed,
    area=area,
    ratio=ratio,
)


# WFS GetFeature request using owslib, returning raw XML response
def get_features_in_bbox(feature_name, bbox, wfs_service):
    # Perform the GetFeature request using the generated random bbox
    try:
        # response = wfs_service.getfeature(typename=feature_name, bbox=bbox, outputFormat="json")
        response = wfs_service.getfeature(typename=feature_name, bbox=bbox, outputFormat="text/xml; subtype=gml/3.2")

        # Return the raw XML content directly (without parsing)
        return response.read().decode("utf-8")
    except Exception as e:
        logging.error(f"Error fetching features for {feature_name} within bbox {bbox}: {e}")
        return None


random_bbox = next(random_bbox_gen)
print(f"Random Bounding Box: {random_bbox}")
features_response = get_features_in_bbox(first_feature_name, random_bbox, wfs20)
# Check and display the response content
# if features_response:
#    print(f"Raw XML response for feature {first_feature_name} in random bbox:\n{features_response}")
# else:
#    print(f"No features found for feature {first_feature_name} in random bbox: {random_bbox}")

# Feature: au:AU.AdministrativeBoundary
# Default CRS: urn:ogc:def:crs:EPSG::4326
# Bounding Box (WGS84): (-31.268815408039654, 30.028778278307783) to (-6.189159307482949, 42.154311127409436)
# Number of Features: 10439
# 2024-10-09 11:51:32,923 - DEBUG - Starting new HTTPS connection (1): geo2.dgterritorio.gov.pt:443
# 2024-10-09 11:51:33,115 - DEBUG - https://geo2.dgterritorio.gov.pt:443 "GET /geoserver/au/wfs?service=WFS&version=2.0.0&request=GetFeature&typenames=au%3AAU.AdministrativeUnits&resultType=hits HTTP/11" 200 None

# Feature: au:AU.AdministrativeUnits
# Default CRS: urn:ogc:def:crs:EPSG::4326
# Bounding Box (WGS84): (-31.268815408039654, 30.028778278307783) to (-6.189159307482949, 42.154311127409436)
# Number of Features: 3916
