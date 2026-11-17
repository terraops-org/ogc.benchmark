"""Look at a WFS before benchmarking it: feature types, default CRS, WGS84 extent and number of features.

    uv run python main.py [WFS_URL]          (default: the public PDOK BAG WFS)

The names it prints are what `wfs.py --feature-name` expects.
"""

import sys

from owslib.wfs import WebFeatureService

from utils import get_feature_count

DEFAULT_WFS = "https://service.pdok.nl/lv/bag/wfs/v2_0"

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WFS
    service = WebFeatureService(url=host, version="2.0.0")
    print(f"{len(service.contents)} feature types in {host}")
    for name, meta in service.contents.items():
        crs = meta.crsOptions[0] if meta.crsOptions else "no CRS"
        print(
            f"{name}: default CRS {crs}, WGS84 bbox {meta.boundingBoxWGS84}, features {get_feature_count(name, host)}"
        )
