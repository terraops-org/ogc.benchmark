"""The start-up (metadata, WMTS probe) runs before Locust's -t clock: a real headless run against a local server."""

import csv
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
METADATA_DELAY = 4  # seconds the fake server takes for /collections; longer than the -t below


class SlowMetadata(BaseHTTPRequestHandler):
    def do_GET(self):
        host = f"http://127.0.0.1:{self.server.server_port}"
        path = self.path.split("?")[0]
        if path in ("", "/"):
            body = {"title": "fake", "links": []}
        elif path == "/collections":
            time.sleep(METADATA_DELAY)
            body = {
                "collections": [
                    {
                        "id": "c",
                        "links": [
                            {"rel": "items", "type": "application/geo+json", "href": f"{host}/collections/c/items"}
                        ],
                        "extent": {"spatial": {"bbox": [[0, 50, 1, 51]]}},
                    }
                ]
            }
        else:
            body = {"type": "FeatureCollection", "features": [{}], "numberReturned": 1}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.mark.slow
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")  # the child execs locust at once
def test_slow_metadata_does_not_eat_the_run_time(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowMetadata)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cmd = [
            sys.executable,
            "-m",
            "locust",
            "-f",
            "ogcapi_features.py",
            "--headless",
            "--only-summary",
            "--host",
            f"http://127.0.0.1:{server.server_port}",
            "-u",
            "1",
            "-r",
            "1",
            "-t",
            "3s",
            "--csv",
            str(tmp_path / "run"),
        ]
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr[-2000:]
    rows = {r["Name"]: r for r in csv.DictReader(open(tmp_path / "run_stats.csv"))}
    assert int(rows["GetFeature"]["Request Count"]) >= 1  # the 3 s of -t went to load, not to the 4 s start-up
