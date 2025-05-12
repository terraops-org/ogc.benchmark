# webservice.benchmark

- [English](README.md)
- [Português Europeu](README_PT.md)

Benchmark tools for OGC webservice benchmark, scripts based on [locust - A modern load testing framework](https://locust.io/)  

Introduction to locust: [quickstart](https://docs.locust.io/en/stable/quickstart.html)
General configuration and command arguments: [Configuration and commands](https://docs.locust.io/en/stable/configuration.html)

The benchmarking script, e.g., `wmts.py`, adapts Locust to specifically test OGC services (e.g., `GetTiles`). It analyzes the `GetCapabilities` document and enables customization of request parameters such as `TileMatrixSet` and `TileMatrix`.

During tests, Locust will randomly request a tile (`GetTile`). The script supports random seed parameters to ensure reproducibility by generating a consistent sequence of random numbers.

## pyenv virtualenv

Instruction for pyenv install: [Install Multiple Python Versions for Specific Project](https://gist.github.com/trongnghia203/9cc8157acb1a9faad2de95c3175aa875)  

pyenv virtual env install:

```bash
# pyenv python version install
export PYENV_PYTHON_VERSION="3.12.10"
export PYTHON_CONFIGURE_OPTS=" --enable-optimizations --with-lto"
export CPPFLAGS="-march=native -O3" 
export CXXFLAGS=${CFLAGS} 
pyenv install -v $PYENV_PYTHON_VERSION

pyenv virtualenv 3.12.10 ogc.benchmark
pyenv local ogc.benchmark 
pyenv shell ogc.benchmark
pyenv which pip # check if paths/shims are correct
#/home/mende012/.pyenv/versions/webservice.benchmark/bin/pip
```

## Install python packages

requirements.txt file pins the necessary packages and version for script to run

```bash
pip install -r requirements.txt
```

And for development:

```bash
pip install -r requirements-dev.txt
```

## Commands (headless)

script `wmts.py` contains the code to test a WMTS webservice, it creates a locust webtest frameworks, extends and validates argument user input specifically for wmts.

```bash
# all configurations possible
locust -f wmts.py --help
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
locust -f wmts.py --headless --host https://cartografia.dgterritorio.gov.pt/ortos2021/service  --random-seed 2129  -u 10 -r 1 -t 2m --layer-name Ortos2021-RGB  --tile-matrix-set "PTTM_06" --tile-matrix "07"  --html wmts.ortos2021.r1.u1.s1640.report.html --loglevel DEBUG --logfile wmts.ortos2021.r1.u1.s1640.log 2>&1 | tee wmts.ortos2021.r1.u1.s1640.txt
```

Note: `2>&1 | tee wmts.ortos2021.r1.u1.s1640.txt` will pipe bash consolte content to file `tee wmts.ortos2021.r1.u1.s1640.txt`

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

## Code Debug

Currentely there is no pytest code, but there is proto-pytest code on `__main__`, the following lines can be ajusted to make script test, with these default links:

```python
wmts_benchmark.host="https://cartografia.dgterritorio.gov.pt/ortos2021/service"
env.parsed_options.tile_random_seed = 1234
env.parsed_options.layer_name = "Ortos2021-RGB"
env.parsed_options.tile_matrix_set = "PTTM_06" # Most extreme case
env.parsed_options.tile_matrix = "07"
```

Note: Attribute values as in the command locust command line structure.

To run tests: `python wmts.py`.

## Code lint

Implemented [black](https://black.readthedocs.io/en/stable/index.html) linter

```bash
❯ black --version
black, 24.10.0 (compiled: yes)
Python (CPython) 3.12.7
```

And then the followin black parameters:

```bash
black --safe --target-version=py312 --line-length 120 *.py
```
