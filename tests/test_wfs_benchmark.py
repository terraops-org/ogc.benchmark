"""Integration tests for WFS benchmark"""
import pytest
import responses
from unittest.mock import Mock, patch
from locust.env import Environment

from wfs import WFSBenchmark
from exceptions import LayerNameArgError


class TestWFSBenchmark:
    """Test WFS benchmark functionality"""

    def setup_method(self):
        """Setup test environment"""
        self.env = Environment(user_classes=[WFSBenchmark])
        self.env.create_local_runner()

        # Create mock parsed options
        self.env.parsed_options = Mock()
        self.env.parsed_options.random_seed = 42
        self.env.parsed_options.bbox_area = 100.0
        self.env.parsed_options.bbox_ratio = 1.0
        self.env.parsed_options.feature_name = None

    @patch('wfs.WebFeatureService')
    @patch('wfs.generate_random_bbox_degrees')
    def test_on_start_success(self, mock_bbox_gen, mock_wfs):
        """Test successful WFS initialization"""
        # Mock WFS service
        mock_service = Mock()
        mock_service.contents = {
            'feature1': Mock(),
            'feature2': Mock()
        }

        # Mock feature properties
        mock_feature = Mock()
        mock_feature.crsOptions = ['EPSG:4326', 'EPSG:3857']
        mock_feature.boundingBoxWGS84 = [-9.5, 36.8, -6.2, 42.2]
        mock_service.contents = {'feature1': mock_feature}

        mock_wfs.return_value = mock_service

        # Mock bbox generator
        mock_bbox_gen.return_value = iter([(-8.0, 37.0, -7.0, 38.0)])

        user = WFSBenchmark(self.env)
        user.host = "http://example.com/wfs"

        # Should not raise exception
        user.on_start()

        # Verify service was initialized
        mock_wfs.assert_called_once_with("http://example.com/wfs", parse_remote_metadata=False, version="2.0.0")
        assert hasattr(user, 'wfs20')
        assert hasattr(user, 'feature_names')

    @patch('wfs.WebFeatureService')
    def test_on_start_connection_failure(self, mock_wfs):
        """Test handling of WFS connection failures"""
        mock_wfs.side_effect = Exception("WFS connection failed")

        user = WFSBenchmark(self.env)
        user.host = "http://invalid.com/wfs"

        with pytest.raises(Exception):
            user.on_start()

    @patch('wfs.WebFeatureService')
    def test_invalid_feature_name(self, mock_wfs):
        """Test error handling for invalid feature names"""
        mock_service = Mock()
        mock_service.contents = {'valid_feature': Mock()}
        mock_wfs.return_value = mock_service

        # Set invalid feature name
        self.env.parsed_options.feature_name = "invalid_feature"

        user = WFSBenchmark(self.env)
        user.host = "http://example.com/wfs"

        with pytest.raises(LayerNameArgError):
            user.on_start()

    def test_get_features(self):
        """Test feature extraction"""
        user = WFSBenchmark(self.env)
        user.wfs20 = Mock()
        user.wfs20.contents = {
            'au:feature1': Mock(),
            'au:feature2': Mock(),
            'other:feature3': Mock()
        }

        features = user.get_features()
        assert len(features) == 3
        assert 'au:feature1' in features
        assert 'au:feature2' in features
        assert 'other:feature3' in features

    def test_get_crs(self):
        """Test CRS extraction"""
        user = WFSBenchmark(self.env)
        user.feature = Mock()
        user.feature.crsOptions = ['EPSG:4326', 'EPSG:3857']

        crs = user.get_crs()
        assert crs == 'EPSG:4326'

    def test_get_crs_no_options(self):
        """Test CRS extraction when no options available"""
        user = WFSBenchmark(self.env)
        user.feature = Mock()
        user.feature.crsOptions = []

        crs = user.get_crs()
        assert crs == "No CRS available"

    def test_get_bbox(self):
        """Test bounding box extraction"""
        user = WFSBenchmark(self.env)
        user.feature = Mock()
        user.feature.boundingBoxWGS84 = [-9.5, 36.8, -6.2, 42.2]

        bbox = user.get_bbox()
        assert bbox == (-9.5, 36.8, -6.2, 42.2)

    @responses.activate
    @patch('wfs.WebFeatureService')
    @patch('wfs.generate_random_bbox_degrees')
    def test_load_features_success(self, mock_bbox_gen, mock_wfs):
        """Test successful feature loading"""
        # Setup mocked WFS service
        self._setup_mock_wfs_service(mock_wfs, mock_bbox_gen)

        # Mock successful GML response
        gml_response = '''<?xml version="1.0" encoding="UTF-8"?>
        <wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0">
            <wfs:member>
                <au:AdministrativeUnit>
                    <au:geometry>...</au:geometry>
                </au:AdministrativeUnit>
            </wfs:member>
        </wfs:FeatureCollection>'''

        responses.add(
            responses.GET,
            "http://example.com/wfs",
            body=gml_response,
            status=200,
            content_type="application/gml+xml"
        )

        user = WFSBenchmark(self.env)
        user.host = "http://example.com/wfs"
        user.on_start()

        # Execute the task
        user.load_features()

        # Verify request was made
        assert len(responses.calls) == 1
        request_url = responses.calls[0].request.url
        assert "SERVICE=WFS" in request_url
        assert "REQUEST=GetFeature" in request_url

    @responses.activate
    @patch('wfs.WebFeatureService')
    @patch('wfs.generate_random_bbox_degrees')
    def test_load_features_json_response(self, mock_bbox_gen, mock_wfs):
        """Test feature loading with JSON response"""
        self._setup_mock_wfs_service(mock_wfs, mock_bbox_gen)

        user = WFSBenchmark(self.env)
        user.host = "http://example.com/wfs"
        user.on_start()

        # Mock JSON response
        with patch.object(user.client, 'get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/json'}
            mock_context = Mock()
            mock_context.__enter__ = Mock(return_value=mock_response)
            mock_context.__exit__ = Mock(return_value=None)
            mock_get.return_value = mock_context

            user.load_features()

            # Verify success was recorded
            mock_response.success.assert_called_once()

    @responses.activate
    @patch('wfs.WebFeatureService')
    @patch('wfs.generate_random_bbox_degrees')
    def test_load_features_wfs_exception(self, mock_bbox_gen, mock_wfs):
        """Test handling of WFS exceptions"""
        self._setup_mock_wfs_service(mock_wfs, mock_bbox_gen)

        user = WFSBenchmark(self.env)
        user.host = "http://example.com/wfs"
        user.on_start()

        # Mock response with WFS exception
        with patch.object(user.client, 'get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/xml'}
            mock_response.text = '<?xml version="1.0"?><ows:ExceptionReport><ows:Exception>Invalid BBOX</ows:Exception></ows:ExceptionReport>'
            mock_context = Mock()
            mock_context.__enter__ = Mock(return_value=mock_response)
            mock_context.__exit__ = Mock(return_value=None)
            mock_get.return_value = mock_context

            user.load_features()

            # Verify failure was recorded for WFS exception
            mock_response.failure.assert_called_once()

    def _setup_mock_wfs_service(self, mock_wfs, mock_bbox_gen):
        """Helper method to setup mocked WFS service"""
        mock_service = Mock()

        # Mock feature
        mock_feature = Mock()
        mock_feature.crsOptions = ['EPSG:4326']
        mock_feature.boundingBoxWGS84 = [-9.5, 36.8, -6.2, 42.2]

        mock_service.contents = {'au:AdministrativeUnits': mock_feature}

        mock_wfs.return_value = mock_service

        # Mock bbox generator
        mock_bbox_gen.return_value = iter([(-8.0, 37.0, -7.0, 38.0)])

        return mock_service