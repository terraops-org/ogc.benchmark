# OGC Benchmark Test Suite

## Test Structure

```
tests/
├── conftest.py              # gevent patch first, LOCUST_* env cleared
├── test_ogc.py              # utils/ogc.py: response checks, CRS facts, capabilities cache, random streams
├── test_utils.py            # random_bbox() and get_feature_count
├── test_exceptions.py       # ArgError: logs, sets exit code 1, stops a real run
├── test_wms_benchmark.py    # wms.py with a mocked OWSLib service and Locust client
├── test_wmts_benchmark.py   # wmts.py, same approach
├── test_wfs_benchmark.py    # wfs.py, same approach, WFS 1.0.0 / 1.1.0 / 2.0.0
├── test_live_services.py    # real requests to public PDOK services (marker: network)
└── test_live_auth.py        # basic auth against a protected WMS (marker: network, needs AUTH_TEST_*)
```

## Running Tests

Tests run in the uv environment (`uv sync` once, see the main README).

```bash
make test        # unit tests, no network   (uv run pytest -m "not network")
make test-live   # live PDOK services        (uv run pytest -m network)
make coverage    # unit tests + coverage report in htmlcov/

uv run pytest tests/test_wfs_benchmark.py -v   # one file
```

The basic-auth live test runs only when a protected WMS and its credentials are given as environment
variables (never in a file):

```bash
AUTH_TEST_WMS_URL=https://... AUTH_TEST_USER=... AUTH_TEST_PASSWORD=... uv run pytest tests/test_live_auth.py
```

`AUTH_TEST_*` rather than `LOCUST_*`, because `conftest.py` clears `LOCUST_*` so that option defaults come
from the code. `conftest.py` also patches gevent before any test module imports `requests` (on Python
3.14+ a late patch makes urllib3's SSL setup fail with RecursionError). pytest settings live in
`pyproject.toml`.

## How the tests work

- **OWSLib services** (`WebMapService`, `WebMapTileService`, `WebFeatureService`) are patched with mocks
  shaped like OWSLib's objects (`crs_list`, `boundingBox`, `tilematrixsetlinks`, ...).
- **The Locust client** is replaced with a `MagicMock` whose `get()` context manager yields a fake
  response; the tests read the URL that was requested and the failure message that was set.
- **Options** come from the real parser (`utils.ogc.default_options(init_parser, *args)`), so defaults,
  environment variables and choices are tested as Locust would parse them.
- `responses` is used only for `get_feature_count`, which goes through `requests`.
- **Live tests** skip only when a service is unreachable (connection error, timeout). Anything else, an
  HTTP 404 or 500 included, fails them.
