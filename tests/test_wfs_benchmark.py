"""Unit tests for wfs.py: feature type choice, WFS 2.0 bbox with its CRS, per-user streams, failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import gevent
import pytest
from locust.env import Environment

import wfs
from exceptions import ArgError
from utils import ogc
from utils.ogc import default_options
from wfs import WFSBenchmark

WGS84 = (4.0, 52.0, 5.0, 53.0)  # lon/lat, as OWSLib reports boundingBoxWGS84


def make_user(*args, contents=None, wgs84=WGS84):
    ogc._CAPS.clear()
    env = Environment(user_classes=[WFSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wfs.init_parser, *args)
    feature = Mock()
    feature.boundingBoxWGS84 = list(wgs84)  # OWSLib gives a list
    feature.boundingBox = (*WGS84, "urn:ogc:def:crs:EPSG::28992")  # WFS 1.0.0: the LatLongBoundingBox degrees
    feature.crsOptions = ["urn:ogc:def:crs:EPSG::28992"]
    service = Mock()
    service.contents = contents or {"ns:a": feature, "ns:b": feature}
    user = WFSBenchmark(env)
    user.host = "http://example.com/wfs"
    with patch("wfs.WebFeatureService", return_value=service) as webfeatureservice:
        user.on_start()
    user.webfeatureservice = webfeatureservice  # test-only handle on the OWSLib constructor call
    user.client = MagicMock()
    return user


def fake_resp(status=200, ctype="text/xml; subtype=gml/3.2", content=b'<wfs:FeatureCollection numberReturned="3">'):
    return SimpleNamespace(status_code=status, headers={"content-type": ctype}, content=content, failure=Mock())


def run_features(user, resp=None):
    """One load_features(); returns the GetFeature query parameters and the response it checked."""
    resp = resp or fake_resp()
    user.client.get.return_value.__enter__.return_value = resp
    user.load_features()
    return parse_qs(urlparse(user.client.get.call_args.args[0]).query), resp


def test_default_is_first_feature_type():
    user = make_user()
    assert user.feature_name == "ns:a"
    assert user.extent == WGS84  # a tuple, whatever OWSLib hands over


def test_unknown_feature_raises():
    with pytest.raises(ArgError):
        make_user("--feature-name", "ns:nope")


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_bbox_has_crs_suffix(version):
    query, _ = run_features(make_user("--bbox-area", "1", "--wfs-version", version))
    *values, crs = query["BBOX"][0].split(",")
    lat1, lon1, lat2, lon2 = map(float, values)
    assert crs == "urn:ogc:def:crs:EPSG::4326"  # WFS 2.0: latitude first for this CRS
    assert 52.0 <= lat1 < lat2 <= 53.0 and 4.0 <= lon1 < lon2 <= 5.0
    assert query["REQUEST"] == ["GetFeature"]
    assert not any(key.lower() == "crs" for key in query)  # not a WFS GetFeature parameter


def test_two_users_different_bboxes():
    first, _ = run_features(make_user())
    second, _ = run_features(make_user())
    assert first["BBOX"] != second["BBOX"]


def test_json_accepted():
    _, resp = run_features(make_user(), fake_resp(200, "application/json", b'{"type": "FeatureCollection"}'))
    resp.failure.assert_not_called()


def test_exception_message_stable():
    body = b'<ows:ExceptionReport><ows:Exception exceptionCode="InvalidParameterValue">BBOX 1,2</ows:Exception>'
    _, resp = run_features(make_user(), fake_resp(200, "text/xml", body))
    resp.failure.assert_called_once_with("OGC Exception")


def test_connection_failure_stops_the_run():
    ogc._CAPS.clear()
    env = Environment(user_classes=[WFSBenchmark])
    env.create_local_runner()
    env.parsed_options = default_options(wfs.init_parser)
    env.runner.quit = Mock()
    user = WFSBenchmark(env)
    user.host = "http://example.com/wfs"
    with patch("wfs.WebFeatureService", side_effect=ConnectionError("down")), pytest.raises(ArgError, match="down"):
        user.on_start()
    gevent.sleep(0)  # quit() runs in its own greenlet
    env.runner.quit.assert_called_once()
    assert env.process_exit_code == 1


def test_capabilities_fetched_once_per_process():
    first = make_user()
    user = WFSBenchmark(first.environment)
    user.host = "http://example.com/wfs"
    with patch("wfs.WebFeatureService") as service:
        user.on_start()
    service.assert_not_called()


@pytest.mark.parametrize(
    "version, typename, limit",
    [("1.0.0", "TYPENAME", "MAXFEATURES"), ("1.1.0", "TYPENAME", "MAXFEATURES"), ("2.0.0", "TYPENAMES", "COUNT")],
)
def test_version_parameters(version, typename, limit):
    user = make_user("--wfs-version", version)
    query, _ = run_features(user)
    assert query["VERSION"] == [version] and query[typename] == ["ns:a"] and query[limit] == ["1000"]
    assert user.webfeatureservice.call_args.kwargs["version"] == version


def test_wfs_100_bbox_in_native_srs():
    """WFS 1.0.0 has no CRS in BBOX: corners in the feature type's SRS (here RD metres), x/y. The extent comes
    from boundingBox, because OWSLib 0.36 turns the 1.0.0 boundingBoxWGS84 into a point."""
    point = (4.5, 52.5, 4.5000001, 52.5000001)
    user = make_user("--wfs-version", "1.0.0", "--bbox-area", "1", wgs84=point)
    assert user.extent == WGS84
    query, _ = run_features(user)
    x1, y1, x2, y2 = map(float, query["BBOX"][0].split(","))
    assert 0 < x1 < x2 < 300000 and 300000 < y1 < y2 < 650000


def test_unknown_wfs_version_refused():
    with pytest.raises(SystemExit):
        default_options(wfs.init_parser, "--wfs-version", "3.0.0")


def test_tls_not_verified_by_default():
    assert make_user().webfeatureservice.call_args.kwargs["auth"].verify is False
    assert make_user("--verify-tls").webfeatureservice.call_args.kwargs["auth"].verify is True


def test_user_agent_sent():
    user = make_user()
    run_features(user)
    assert (
        user.client.get.call_args.kwargs["headers"]["User-Agent"]
        == "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    )
    assert user.webfeatureservice.call_args.kwargs["headers"] == {
        "User-Agent": "ogc.benchmark/0.1.0 (+https://github.com/terraops-org/ogc.benchmark)"
    }
