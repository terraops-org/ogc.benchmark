# webservice.benchmark

- [English](README.md)
- [Português Europeu](README_PT.md)

Benchmark tools for OGC webservice benchmark, scripts based on [locust - A modern load testing framework](https://locust.io/)  

Introduction to locust: [quickstart](https://docs.locust.io/en/stable/quickstart.html)
General configuration and command arguments: [Configuration and commands](https://docs.locust.io/en/stable/configuration.html)

The benchmarking script, e.g., `wmts.py`, adapts Locust to specifically test OGC services (e.g., `GetTiles`). It analyzes the `GetCapabilities` document and enables customization of request parameters such as `TileMatrixSet` and `TileMatrix`.

During tests, Locust will randomly request a tile (`GetTile`). The script supports random seed parameters to ensure reproducibility by generating a consistent sequence of random numbers.

## Installation

The project uses [uv](https://docs.astral.sh/uv/). It installs the Python version in `.python-version`
(3.15) and the exact package versions in `uv.lock` into a local `.venv`:

```bash
uv sync          # or: make install
```

Every command then runs through `uv run`, e.g. `uv run locust ...` (no virtualenv to activate).

## Commands (headless)

script `wmts.py` contains the code to test a WMTS webservice, it creates a locust webtest frameworks, extends and validates argument user input specifically for wmts.

```bash
# all configurations possible
uv run locust -f wmts.py --help
```

In a nutshell:

|Argument|Description| Example| Default |
|:------:|:---------:|:-------:|:---------:|
| `-h/-host`   | Host/Server will full OGCservice path | [https://cartografia.dgterritorio.gov.pt/ortos2021/service](https://cartografia.dgterritorio.gov.pt/ortos2021/service) | Mandatory NO default |
|`--random-seed`|  Random seed to generate random request |   --random-seed 2129 | 1640 |
|`--layer-name`| OGC service layer to be used| --layer-name Ortos2021-RGB | First layer in service found on GetCapabilites XML document|
|`--tile-matrix-set`| Layer's TileMatrixSet to test (Piramid Tile Type)| --tile-matrix-set "PTTM_06"| Layer's first TileMatrixSet found on GetCapabilities XML document|  
|`--tile-matrix`| TileMatrixSet's TileMatrix to use (Piramid's zoom/resolution level)| --tile-matrix "07"| Median value (normally 07 or 10)|
|`--headless`| For locust to run on command line mode | --headless | Default locust is working on webui|
|`--u` | Number of requests (Number of locust users) | -u 10| No default |
|`-r` | Increase rate of rquests (Number of locust users)| -r 1| No default|
|`-t`| Time of testing, full time to run test (s, m, h)| -t 4m | No default|
|`--loglevel`| Python's log level e.g DEBUG, INFO| --loglevel DEBUG | INFO |
|`--html`| HTML Filename with test results and graphics| --html wmts.ortos2021.r1.u1.s1640.report.html| No default |
|`--logfile`| Log file with extra information and requests| --logfile wmts.ortos2021.r1.u1.s1640.log| No default|

For example a full command would be like this:

```bash
uv run locust -f wmts.py --headless --host https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0 --random-seed 2129 -u 10 -r 1 -t 2m --layer-name Actueel_orthoHR --html wmts.luchtfoto.r1.u10.s2129.report.html --loglevel DEBUG --logfile wmts.luchtfoto.r1.u10.s2129.log 2>&1 | tee wmts.luchtfoto.r1.u10.s2129.txt
```

Note: `2>&1 | tee wmts.luchtfoto.r1.u10.s2129.txt` copies the console output to that file.

The same pattern runs the other services:

```bash
uv run locust -f wms.py --headless --host https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0 --layer-name Actueel_orthoHR -u 10 -r 1 -t 2m --html wms-report.html
uv run locust -f wfs.py --headless --host https://service.pdok.nl/lv/bag/wfs/v2_0 --feature-name bag:pand -u 5 -r 1 -t 2m --html wfs-report.html
```

## Comparing results across versions

Results compare only when the seed, the options and the tool version match. Since the move to
Locust 2.47 and Python 3.15, older reports are not comparable with new ones:

- Locust 2.43.4 changed the HTML report's req/s column from the current rate to the total rate.
- Locust 2.46 fixed response-time percentiles that were reported too low.
- Each simulated user now draws from its own random stream, so a seed gives other bboxes than before.

To re-run the old behaviour, check out the tag `v2025.11-locust2.37`.

## DGT WMTS

List of URLs and layer information:

| Name | URL |layer | TileMatrixSet | Proj| TileMatrix |
|:----:|:---:|:----:|:-------------:|:---:|:-----------|
|Ortos 2021 Portugal-Continente (Mapproxy)| [https://cartografia.dgterritorio.gov.pt/ortos2021/service?service=wmts&request=getcapabilities](https://cartografia.dgterritorio.gov.pt/ortos2021/service?service=wmts&request=getcapabilities)| Ortos2021-RGB| PTTM_06| EPSG:3763  | 9325x16384 |
|Areas edificadas (Geoserver)| [https://geo2.dgterritorio.gov.pt/geoserver/AE/gwc/service/wmts?service=wmts&request=getcapabilities](https://geo2.dgterritorio.gov.pt/geoserver/AE/gwc/service/wmts?service=wmts&request=getcapabilities)| AE-Interface_Estrutural_2018 | EPSG:3857 | EPSG:3857 | (140/31542,463/15683) |
| PDOK luchtfoto 2023 |[https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?service=wmts&request=getcapabilities]([https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?service=wmts&request=getcapabilities) | Actueel_orthoHR|EPSG:28992/10  |EPSG:28992 | 1024x1024 |

## 3rd party WMTS

| Name | URL |layer | Proj |  BBOX |
|:----:|:---:|:----:|:----:|:-----:|
|Carta do Regime de Uso do Solo - Portugal Continental| [https://servicos.dgterritorio.pt/SDISNITWMSCRUS/WMService.aspx?service=wmts&request=getcapabilities](https://servicos.dgterritorio.pt/SDISNITWMSCRUS/WMService.aspx?service=wmts&request=getcapabilities) |CRUS| EPSG:3763|minx="-325383.32" miny="-317959.32" maxx="369324.95" maxy="293637.73"|
|PDOK luchtfoto 2023 | [https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0?service=wmts&request=getcapabilities](https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0?service=wmts&request=getcapabilities) | Actueel_orthoHR |EPSG:28992|  minx="-2000.0" miny="290000.0" maxx="294000.0" maxy="630000.0" |

## Testing

Tests use pytest; tests that need live services are marked `network`.

```bash
make test        # unit tests, no network   (uv run pytest -m "not network")
make test-live   # live PDOK services        (uv run pytest -m network)
make coverage    # unit tests + coverage report in htmlcov/

uv run pytest tests/test_wmts_benchmark.py   # one test file
```

See [tests/README.md](tests/README.md) for detailed testing documentation.

## Code lint

Formatting with [black](https://black.readthedocs.io/en/stable/index.html) (line length 120, settings in
`pyproject.toml`):

```bash
make lint        # uv run black --line-length 120 *.py utils tests
```
