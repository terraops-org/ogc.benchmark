"""OGC API - Tiles load test (vector tiles): random tiles of one tile matrix of a collection, inside its extent."""

# gevent patches the standard library before requests is imported (needed for `python ogcapi_tiles.py`)
from gevent import monkey

monkey.patch_all()

import logging  # noqa: E402

from exceptions import ArgError  # noqa: E402
from locust import FastHttpUser, between, events, task  # noqa: E402

from utils.ogc import (  # noqa: E402
    add_auth_args,
    add_common_args,
    add_extent_arg,
    check_response,
    default_options,
    mark_empty,
    parse_extent,
    request_headers,
    user_rng,
)
from utils.ogcapi import MVT, cached_json, collections, link, lonlat_area, pick_collection  # noqa: E402
from utils.tiles import tile_range, tms_matrix, to_crs_box  # noqa: E402

logger = logging.getLogger(__name__)


@events.init_command_line_parser.add_listener
def init_parser(parser):
    add_common_args(parser, bbox=False)
    parser.add_argument(
        "--collection",
        type=str,
        default="",
        env_var="LOCUST_COLLECTION",
        help="Collection to benchmark (default: the first collection with vector tiles)",
    )
    parser.add_argument(
        "--tile-matrix-set",
        type=str,
        default="",
        env_var="LOCUST_TILE_MATRIX_SET",
        help="Tile matrix set of the collection, e.g. WebMercatorQuad (default: the collection's first)",
    )
    parser.add_argument(
        "--tile-matrix",
        type=str,
        default="",
        env_var="LOCUST_TILE_MATRIX",
        help="Tile matrix (zoom level) of the tile matrix set (default: the middle level)",
    )
    add_extent_arg(parser)
    add_auth_args(parser)


def tms_id(tileset):
    """WebMercatorQuad from a tileset's tileMatrixSetId or tileMatrixSetURI."""
    return (tileset.get("tileMatrixSetId") or tileset.get("tileMatrixSetURI", "")).rstrip("/").rsplit("/", 1)[-1]


class OGCAPITilesBenchmark(FastHttpUser):
    """Vector tile requests for random tiles of one tile matrix, inside the collection's extent."""

    wait_time = between(1, 2)  # 1-2 s between requests per simulated user
    host = ""  # without a class attribute the __main__ example cannot set the host
    network_timeout = 120.0  # seconds; a dev server can take tens of seconds for a low-zoom tile
    connection_timeout = 120.0

    def on_start(self):
        """Read the metadata (once per process), pick collection, tile matrix set, level and tile range."""
        opts = self.environment.parsed_options
        if opts.auth_password and not opts.auth_user:
            ArgError("--auth-password is set but --auth-user is not").handle_error(self.environment)
        self.headers = request_headers(opts)
        try:
            self.setup(opts)
        except ValueError as e:  # a wrong option or metadata the benchmark cannot use
            ArgError(str(e)).handle_error(self.environment)
        except Exception as e:  # unreachable host, HTTP 401, not an OGC API: stop now instead of running with no users
            ArgError(f"Cannot read the OGC API metadata of {self.host}: {e}").handle_error(self.environment)
        self.rng = user_rng(self.environment, opts.random_seed)
        logger.info(
            f"collection {self.collection}, tile matrix set {self.tile_matrix_set}, tile matrix {self.target.id}, "
            f"tiles (cols, rows) {self.range}, seed {opts.random_seed}"
        )

    def setup(self, opts):
        """Set collection, tile matrix set, target level, URL template and tile range; ValueError if unusable."""
        col = pick_collection(collections(self.host, self.headers, opts.verify_tls), opts.collection, "tilesets-vector")
        self.collection = col["id"]
        tilesets = cached_json(link(col, "tilesets-vector"), self.headers, opts.verify_tls)["tilesets"]
        ids = [tms_id(ts) for ts in tilesets]
        self.tile_matrix_set = opts.tile_matrix_set or ids[0]
        if self.tile_matrix_set not in ids:
            raise ValueError(f"Collection '{self.collection}' has no tile matrix set '{self.tile_matrix_set}': {ids}")
        tileset_ref = tilesets[ids.index(self.tile_matrix_set)]
        if tileset_ref.get("dataType", "vector") != "vector":
            raise ValueError(f"Tileset {self.tile_matrix_set}: only vector tilesets are supported")
        tileset = cached_json(
            link(tileset_ref, "self", "application/json") or link(tileset_ref, "self"), self.headers, opts.verify_tls
        )
        self.template = link(tileset, "item", MVT[0]) or link(tileset, "item")
        if not self.template:
            raise ValueError(f"Tileset {self.tile_matrix_set} of '{self.collection}' has no tile URL template")

        tms_url = link(tileset, "tiling-scheme") or f"{self.host.rstrip('/')}/TileMatrixSets/{self.tile_matrix_set}"
        tms = cached_json(tms_url, self.headers, opts.verify_tls)
        crs = tms["crs"]["uri"] if isinstance(tms["crs"], dict) else tms["crs"]
        levels = [tms_matrix(level, crs) for level in tms["tileMatrices"]]
        ids = [m.id for m in levels]
        wanted = opts.tile_matrix or ids[len(ids) // 2]
        if wanted not in ids:
            raise ValueError(f"Tile matrix '{wanted}' is not in {self.tile_matrix_set}. Available values are {ids}")
        self.target = levels[ids.index(wanted)]

        limits = {lim["tileMatrix"]: lim for lim in tileset.get("tileMatrixSetLimits") or []}
        if self.target.id in limits:
            lim = limits[self.target.id]
            self.range = (lim["minTileCol"], lim["maxTileCol"], lim["minTileRow"], lim["maxTileRow"])
            extent = parse_extent(opts.extent)
            if extent:  # --extent narrows the limits too
                r = tile_range(self.target, to_crs_box(extent, crs))
                c0, c1, r0, r1 = self.range
                self.range = r and (max(c0, r[0]), min(c1, r[1]), max(r0, r[2]), min(r1, r[3]))
                if not self.range or self.range[0] > self.range[1] or self.range[2] > self.range[3]:
                    raise ValueError(
                        f"--extent {opts.extent} does not overlap the tileMatrixSetLimits of {self.target.id}"
                    )
            return
        box = lonlat_area(col, opts.extent)
        self.range = tile_range(self.target, to_crs_box(box, crs))
        if self.range is None:
            raise ValueError(f"The extent {box} is outside tile matrix {self.target.id} of {self.tile_matrix_set}")

    @task
    def load_tile(self):
        """A random tile inside the range; a 204 or an empty tile is reported as 'GetTile (empty)'."""
        col = self.rng.randint(self.range[0], self.range[1])
        row = self.rng.randint(self.range[2], self.range[3])
        url = (
            self.template.replace("{tileMatrix}", self.target.id)
            .replace("{tileRow}", str(row))
            .replace("{tileCol}", str(col))
        )
        with self.client.get(url, headers=self.headers, catch_response=True, name="GetTile") as resp:
            if check_response(resp, MVT, empty_ok=True) and (resp.status_code == 204 or not resp.content):
                mark_empty(resp)


# Example: one vector tile from the public pygeoapi demo (world lakes, zoom level 2)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    from locust.env import Environment

    env = Environment(user_classes=[OGCAPITilesBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(init_parser, "--collection", "lakes", "--tile-matrix", "2")
    env.events.request.add_listener(
        lambda name, response, exception, **kw: print(name, response.status_code, exception)
    )

    user = OGCAPITilesBenchmark(env)
    user.host = "https://demo.pygeoapi.io/master"
    user.on_start()
    user.load_tile()
    env.runner.quit()
