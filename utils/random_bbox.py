from typing import Generator, Tuple
import random
from decimal import Decimal, getcontext
import math
# Set precision for Decimal calculations
getcontext().prec = 6 # 12

# Constants
KM_PER_DEGREE_LAT = Decimal('111.321')  # Approximate kilometers per degree of latitude at the equator

class BBoxError(Exception):
    """Custom exception for bounding box errors."""
    pass

def calculate_area_km2(lat: Decimal, width_deg: Decimal, height_deg: Decimal) -> Decimal:
    """
    Calculate the approximate area of a bounding box at a specific latitude.
    Area = (width in km at a specific latitude) * (height in km)
    """
    # Convert Decimal latitude to float for trigonometric calculations
    width_km = width_deg * KM_PER_DEGREE_LAT * Decimal(math.cos(math.radians(float(lat))))
    height_km = height_deg * KM_PER_DEGREE_LAT
    return width_km * height_km

def generate_random_bbox_degrees(
    full_bbox_minx: float,
    full_bbox_miny: float,
    full_bbox_maxx: float,
    full_bbox_maxy: float,
    seed: int,
    area: float,  # in square kilometers
    ratio: float,
) -> Generator[Tuple[float, float, float, float], None, None]:
    """
    Generate a random sub-bounding box in degrees (EPSG:4326) within the full extent of the input bounding box.
    It tries to approximate the desired area in square kilometers.
    
    Parameters:
    - full_bbox_minx, full_bbox_miny, full_bbox_maxx, full_bbox_maxy (float): Coordinates of the full extent bounding box (in degrees).
    - area (float): Desired area of the sub-bounding box in square kilometers.
    - ratio (float): Width to height ratio of the sub-bounding box.
    - seed (int): Optional. Seed value for deterministic random bbox generation.
    
    Yields:
    - A tuple of floats representing the sub-bounding box in the format (minx, miny, maxx, maxy).
    """

    if seed is not None:
        random.seed(seed)  # Seed for deterministic randomness

    # Convert parameters to Decimal for higher precision
    full_bbox_minx = Decimal(full_bbox_minx)
    full_bbox_miny = Decimal(full_bbox_miny)
    full_bbox_maxx = Decimal(full_bbox_maxx)
    full_bbox_maxy = Decimal(full_bbox_maxy)
    area = Decimal(area)
    ratio = Decimal(ratio)

    if full_bbox_maxx <= full_bbox_minx or full_bbox_maxy <= full_bbox_miny:
        raise ValueError("Invalid full bounding box dimensions.")
    if area <= 0:
        raise ValueError("Area must be greater than 0.")
    if ratio <= 0:
        raise ValueError("Aspect ratio must be greater than 0.")

    while True:
        # Random latitude within the bbox
        random_lat = Decimal(random.uniform(float(full_bbox_miny), float(full_bbox_maxy)))
        #random_lat = Decimal(random.uniform(full_bbox_miny, full_bbox_maxy))
        
        # Height and width based on the input area and ratio
        height_km = (area / ratio).sqrt()
        width_km = ratio * height_km
        
        # Convert height from km to degrees of latitude
        height_deg = height_km / KM_PER_DEGREE_LAT
        
        # Convert width from km to degrees of longitude (depends on latitude)
        width_deg = width_km / (KM_PER_DEGREE_LAT * Decimal(math.cos(math.radians(float(random_lat)))))


        # Ensure the bbox fits within the given extent
        max_start_x = full_bbox_maxx - width_deg
        max_start_y = full_bbox_maxy - height_deg

        random_start_x = Decimal(random.uniform(float(full_bbox_minx), float(max_start_x)))
        random_start_y = Decimal(random.uniform(float(full_bbox_miny), float(max_start_y)))

        # Yield the random bounding box
        yield (
            float(random_start_x),
            float(random_start_y),
            float(random_start_x + width_deg),
            float(random_start_y + height_deg),
        )



def generate_random_bbox(
    full_bbox_minx: float,
    full_bbox_miny: float,
    full_bbox_maxx: float,
    full_bbox_maxy: float,
    seed: int,
    area: float,  # in square kilometers
    ratio: float,  # width-to-height ratio
) -> Generator[Tuple[float, float, float, float], None, None]:
    """
    Generate a deterministic random sub-bounding box within a specified full extent bounding box using Decimal for high precision.

    Parameters:
    - full_bbox_minx, full_bbox_miny, full_bbox_maxx, full_bbox_maxy (float): Coordinates of the full extent bounding box.
    - area (float): The area of the sub-bounding box in square kilometers.
    - ratio (float): The width to height ratio of the sub-bounding box.
    - seed (int): Optional. A seed value for the random number generator to ensure deterministic output.

    Yields:
    - A tuple of floats representing the sub-bounding box in the format (minx, miny, maxx, maxy).

    Raises:
    - BBoxError: If the full bounding box dimensions or the specified area are invalid.
    """

    if seed is not None:
        random.seed(seed)  # Seed the random number generator for deterministic output

    # Convert parameters to Decimal for precision
    full_bbox_minx = Decimal(full_bbox_minx)
    full_bbox_miny = Decimal(full_bbox_miny)
    full_bbox_maxx = Decimal(full_bbox_maxx)
    full_bbox_maxy = Decimal(full_bbox_maxy)
    area = Decimal(area)
    ratio = Decimal(ratio)

    if full_bbox_maxx <= full_bbox_minx or full_bbox_maxy <= full_bbox_miny:
        raise BBoxError("Invalid full bounding box dimensions.")
    if area <= 0:
        raise BBoxError("Area must be greater than 0.")
    if ratio <= 0:
        raise BBoxError("Aspect ratio must be greater than 0.")

    # Calculate height and width in kilometers
    height_km = (area / ratio).sqrt()  # height = sqrt(area / ratio)
    width_km = ratio * height_km        # width = ratio * height

    # Convert km to meters
    side_length_x = width_km * 1000
    side_length_y = height_km * 1000

    # Calculate the max starting point for the bounding box
    max_start_x = full_bbox_maxx - side_length_x
    max_start_y = full_bbox_maxy - side_length_y

    while True:
        # Generate random starting points within the full bounding box
        random_start_x = Decimal(random.uniform(float(full_bbox_minx), float(max_start_x)))
        random_start_y = Decimal(random.uniform(float(full_bbox_miny), float(max_start_y)))

        yield (
            float(random_start_x),
            float(random_start_y),
            float(random_start_x + side_length_x),
            float(random_start_y + side_length_y),
        )


# Example usage and testing
if __name__ == "__main__":
    try:
        # Test case with area of 5 square kilometers and ratio of 1.2
        test1 = {"area": 5, "ratio": 1.2, "seed": 42}
        test1_result = (
            50106.69464503156,
            -311016.67483456887,
            52556.184387814734,
            -308975.43338224955,
        )
        bbox_generator = generate_random_bbox(
            -144205.734375, -326024.8125, 162129.09375, 276083.78125, **test1
        )
        result_bbox = next(bbox_generator)
        
        #assert test1_result == result_bbox
        
        print(f"Generated BBox: {result_bbox}")

    except BBoxError as e:
        print(f"Error generating bbox: {e}")

# now for the degree function testing

    bbox_generator_degrees = generate_random_bbox_degrees(
        -9.5, 36.8, -6.2, 42.2, seed=42, area=1000, ratio=1.0
    )
    random_bbox = next(bbox_generator_degrees)
    print(f"Random Bounding Box in EPSG:4326: {random_bbox}")

    # Calculate the actual area of the generated bbox
    
    lat_center = Decimal((random_bbox[1] + random_bbox[3]) / 2)
    actual_area = calculate_area_km2(lat_center, Decimal(random_bbox[2] - random_bbox[0]), Decimal(random_bbox[3] - random_bbox[1]))
    print(f"Actual Area of Generated BBox: {actual_area:.2f} km²")
    print(f"Generated BBOX: {random_bbox[0]},{random_bbox[1]},{random_bbox[2]},{random_bbox[3]}")