"""Unit tests for utility functions"""

import pytest
import responses
from utils import get_feature_count
import math
import random

from utils.random_bbox import random_bbox


class TestRandomBboxRng:
    """random_bbox: one bbox per call from a caller-owned random.Random"""

    def test_random_bbox_metres(self):
        minx, miny, maxx, maxy = random_bbox(random.Random(1), (0, 0, 100000, 100000), 100, 1.0)
        assert maxx - minx == pytest.approx(10000.0, abs=1e-6)
        assert maxy - miny == pytest.approx(10000.0, abs=1e-6)
        assert 0 <= minx and maxx <= 100000 and 0 <= miny and maxy <= 100000

    def test_random_bbox_degrees(self):
        minx, miny, maxx, maxy = random_bbox(random.Random(1), (4.0, 52.0, 5.0, 53.0), 100, 1.0, geographic=True)
        w, h = maxx - minx, maxy - miny
        assert h == pytest.approx(100**0.5 / 111.321)
        lat_centre = (miny + maxy) / 2
        area = w * 111.321 * math.cos(math.radians(lat_centre)) * h * 111.321
        assert area == pytest.approx(100, rel=0.01)
        assert 4.0 <= minx and maxx <= 5.0 and 52.0 <= miny and maxy <= 53.0

    @pytest.mark.parametrize("area, ratio", [(0, 1.0), (-1, 1.0), (100, 0), (100, -2)])
    def test_random_bbox_rejects_bad_input(self, area, ratio):
        with pytest.raises(ValueError):
            random_bbox(random.Random(1), (0, 0, 1000, 1000), area, ratio)

    def test_random_bbox_bigger_than_extent(self):
        minx, miny, _, _ = random_bbox(random.Random(1), (0, 0, 1000, 1000), 1e6, 1.0)
        assert (minx, miny) == (0, 0)


class TestGetFeatureCount:
    """Test the get_feature_count function"""

    @responses.activate
    def test_get_feature_count_success(self):
        """Test successful feature count retrieval"""
        # Mock XML response
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <wfs:FeatureCollection
            xmlns:wfs="http://www.opengis.net/wfs/2.0"
            numberMatched="42"
            numberReturned="0">
        </wfs:FeatureCollection>"""

        responses.add(
            responses.GET, "http://example.com/wfs", body=xml_response, status=200, content_type="application/xml"
        )

        result = get_feature_count("test:feature", "http://example.com/wfs")
        assert result == 42

    @responses.activate
    def test_get_feature_count_http_error(self):
        """Test handling of HTTP errors"""
        responses.add(responses.GET, "http://example.com/wfs", status=404)

        result = get_feature_count("test:feature", "http://example.com/wfs")
        assert result is None

    def test_get_feature_count_parameters(self):
        """Test that correct parameters are sent"""
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, "http://example.com/wfs", status=200, body="<root numberMatched='10'/>")

            get_feature_count("au:AU.AdministrativeUnits", "http://example.com/wfs")

            # Check the request was made with correct parameters
            assert len(rsps.calls) == 1
            request = rsps.calls[0].request
            assert "service=WFS" in request.url
            assert "version=2.0.0" in request.url
            assert "request=GetFeature" in request.url
            assert "typenames=au%3AAU.AdministrativeUnits" in request.url
            assert "resultType=hits" in request.url
