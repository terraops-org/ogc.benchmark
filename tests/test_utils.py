"""Unit tests for utility functions"""
import pytest
import responses
from unittest.mock import patch
from utils import get_feature_count
from utils.random_bbox import generate_random_bbox, generate_random_bbox_degrees, BBoxError


class TestGetFeatureCount:
    """Test the get_feature_count function"""

    @responses.activate
    def test_get_feature_count_success(self):
        """Test successful feature count retrieval"""
        # Mock XML response
        xml_response = '''<?xml version="1.0" encoding="UTF-8"?>
        <wfs:FeatureCollection
            xmlns:wfs="http://www.opengis.net/wfs/2.0"
            numberMatched="42"
            numberReturned="0">
        </wfs:FeatureCollection>'''

        responses.add(
            responses.GET,
            "http://example.com/wfs",
            body=xml_response,
            status=200,
            content_type="application/xml"
        )

        result = get_feature_count("test:feature", "http://example.com/wfs")
        assert result == 42

    @responses.activate
    def test_get_feature_count_http_error(self):
        """Test handling of HTTP errors"""
        responses.add(
            responses.GET,
            "http://example.com/wfs",
            status=404
        )

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


class TestRandomBbox:
    """Test the random bounding box generation functions"""

    def test_generate_random_bbox_basic(self):
        """Test basic bbox generation"""
        bbox_gen = generate_random_bbox(0, 0, 1000, 1000, seed=42, area=100, ratio=1.0)
        bbox = next(bbox_gen)

        assert len(bbox) == 4
        assert bbox[0] < bbox[2]  # minx < maxx
        assert bbox[1] < bbox[3]  # miny < maxy

    def test_generate_random_bbox_deterministic(self):
        """Test that same seed produces same results"""
        bbox_gen1 = generate_random_bbox(0, 0, 1000, 1000, seed=42, area=100, ratio=1.0)
        bbox_gen2 = generate_random_bbox(0, 0, 1000, 1000, seed=42, area=100, ratio=1.0)

        bbox1 = next(bbox_gen1)
        bbox2 = next(bbox_gen2)

        assert bbox1 == bbox2

    def test_generate_random_bbox_different_seeds(self):
        """Test that different seeds produce different results"""
        bbox_gen1 = generate_random_bbox(0, 0, 1000, 1000, seed=42, area=100, ratio=1.0)
        bbox_gen2 = generate_random_bbox(0, 0, 1000, 1000, seed=43, area=100, ratio=1.0)

        bbox1 = next(bbox_gen1)
        bbox2 = next(bbox_gen2)

        assert bbox1 != bbox2

    def test_generate_random_bbox_invalid_params(self):
        """Test error handling for invalid parameters"""
        with pytest.raises(BBoxError):
            # Invalid bbox dimensions
            next(generate_random_bbox(1000, 0, 0, 1000, seed=42, area=100, ratio=1.0))

        with pytest.raises(BBoxError):
            # Negative area
            next(generate_random_bbox(0, 0, 1000, 1000, seed=42, area=-100, ratio=1.0))

        with pytest.raises(BBoxError):
            # Invalid ratio
            next(generate_random_bbox(0, 0, 1000, 1000, seed=42, area=100, ratio=0))

    def test_generate_random_bbox_degrees_basic(self):
        """Test basic degree-based bbox generation"""
        bbox_gen = generate_random_bbox_degrees(-9.5, 36.8, -6.2, 42.2, seed=42, area=100, ratio=1.0)
        bbox = next(bbox_gen)

        assert len(bbox) == 4
        assert -9.5 <= bbox[0] < bbox[2] <= -6.2  # Within longitude bounds
        assert 36.8 <= bbox[1] < bbox[3] <= 42.2  # Within latitude bounds

    def test_generate_random_bbox_degrees_deterministic(self):
        """Test deterministic behavior with degrees"""
        bbox_gen1 = generate_random_bbox_degrees(-9.5, 36.8, -6.2, 42.2, seed=42, area=100, ratio=1.0)
        bbox_gen2 = generate_random_bbox_degrees(-9.5, 36.8, -6.2, 42.2, seed=42, area=100, ratio=1.0)

        bbox1 = next(bbox_gen1)
        bbox2 = next(bbox_gen2)

        assert bbox1 == bbox2