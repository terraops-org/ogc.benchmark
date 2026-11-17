"""Random bounding boxes of a given area inside an extent, drawn from a caller-owned random.Random."""

import math

KM_PER_DEGREE_LAT = 111.321  # km per degree of latitude (and of longitude at the equator)


def random_bbox(rng, extent, area_km2, ratio, geographic=False):
    """
    One random bbox of area_km2 (width/height = ratio) inside extent (minx, miny, maxx, maxy), drawn from rng.

    Map units are metres, or degrees with geographic=True (the width then follows the latitude). A bbox
    larger than the extent starts at the extent's min corner.
    """
    if area_km2 <= 0 or ratio <= 0:
        raise ValueError(f"bbox area ({area_km2} km2) and ratio ({ratio}) must be greater than 0")
    minx, miny, maxx, maxy = extent
    height_km = math.sqrt(area_km2 / ratio)
    if geographic:
        height = height_km / KM_PER_DEGREE_LAT
        y = rng.uniform(miny, max(miny, maxy - height))
        width = ratio * height_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(y + height / 2)))
    else:
        height = height_km * 1000
        width = ratio * height
        y = rng.uniform(miny, max(miny, maxy - height))
    x = rng.uniform(minx, max(minx, maxx - width))
    return (x, y, x + width, y + height)


# Example and self-check: a 10 x 10 km box in metres, then 1000 km2 in degrees over mainland Portugal
if __name__ == "__main__":
    import random

    rng = random.Random(42)

    metres = random_bbox(rng, (-144205.7, -326024.8, 162129.1, 276083.8), 100, 1.0)
    print(f"EPSG:3763 bbox (100 km2): {metres}")
    assert math.isclose(metres[2] - metres[0], 10000) and math.isclose(metres[3] - metres[1], 10000)

    degrees = random_bbox(rng, (-9.5, 36.8, -6.2, 42.2), 1000, 1.0, geographic=True)
    print(f"EPSG:4326 bbox (1000 km2, lon/lat): {degrees}")
    lat_centre = (degrees[1] + degrees[3]) / 2
    width_km = (degrees[2] - degrees[0]) * KM_PER_DEGREE_LAT * math.cos(math.radians(lat_centre))
    height_km = (degrees[3] - degrees[1]) * KM_PER_DEGREE_LAT
    print(f"area of the degrees bbox: {width_km * height_km:.1f} km2")
    assert math.isclose(width_km * height_km, 1000, rel_tol=0.01)
    print("ok")
