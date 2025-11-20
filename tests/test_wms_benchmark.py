"""Integration tests for WMS benchmark"""
import pytest
import responses
from unittest.mock import Mock, patch
from locust.env import Environment

from wms import WMSBenchmark
from exceptions import LayerNameArgError


class TestWMSBenchmark:
    """Test WMS benchmark functionality"""

    def setup_method(self):
        """Setup test environment"""
        self.env = Environment(user_classes=[WMSBenchmark])
        self.env.create_local_runner()

        # Create mock parsed options
        self.env.parsed_options = Mock()
        self.env.parsed_options.random_seed = 42
        self.env.parsed_options.bbox_area = 100.0
        self.env.parsed_options.bbox_ratio = 1.0
        self.env.parsed_options.layer_name = None

    @patch('wms.WebMapService')
    @patch('wms.generate_random_bbox')
    def test_on_start_success(self, mock_bbox_gen, mock_wms):
        """Test successful WMS initialization"""
        # Mock WMS service
        mock_service = Mock()
        mock_service.contents = {
            'layer1': Mock(),
            'layer2': Mock()
        }

        # Mock layer properties
        mock_layer = Mock()
        mock_layer.boundingBox = [0, 0, 1000, 1000, 'EPSG:3857']
        mock_service.contents = {'layer1': mock_layer}

        # Mock get operation
        mock_operation = Mock()
        mock_operation.formatOptions = ['image/jpeg', 'image/png']
        mock_service.getOperationByName.return_value = mock_operation

        mock_wms.return_value = mock_service

        # Mock bbox generator
        mock_bbox_gen.return_value = iter([(100, 100, 200, 200)])

        user = WMSBenchmark(self.env)
        user.host = "http://example.com/wms"

        # Should not raise exception
        user.on_start()

        # Verify service was initialized
        mock_wms.assert_called_once_with("http://example.com/wms")
        assert hasattr(user, 'wms')
        assert hasattr(user, 'layers')
        assert hasattr(user, 'layer_name')

    @patch('wms.WebMapService')
    def test_on_start_connection_failure(self, mock_wms):
        """Test handling of WMS connection failures"""
        mock_wms.side_effect = Exception("WMS connection failed")

        user = WMSBenchmark(self.env)
        user.host = "http://invalid.com/wms"

        with pytest.raises(Exception):
            user.on_start()

    @patch('wms.WebMapService')
    def test_invalid_layer_name(self, mock_wms):
        """Test error handling for invalid layer names"""
        mock_service = Mock()
        mock_service.contents = {'valid_layer': Mock()}
        mock_service.identification.title = "Test WMS Service"
        mock_wms.return_value = mock_service

        # Set invalid layer name
        self.env.parsed_options.layer_name = "invalid_layer"

        user = WMSBenchmark(self.env)
        user.host = "http://example.com/wms"

        with pytest.raises(LayerNameArgError):
            user.on_start()

    def test_get_layers(self):
        """Test layer extraction"""
        user = WMSBenchmark(self.env)
        user.wms = Mock()
        user.wms.contents = {
            'layer1': Mock(),
            'layer2': Mock(),
            'layer3': Mock()
        }

        layers = user.get_layers()
        assert len(layers) == 3
        assert 'layer1' in layers
        assert 'layer2' in layers
        assert 'layer3' in layers

    def test_get_crs(self):
        """Test CRS extraction"""
        user = WMSBenchmark(self.env)
        user.layer = Mock()
        user.layer.boundingBox = [0, 0, 1000, 1000, 'EPSG:4326']

        crs = user.get_crs()
        assert crs == 'EPSG:4326'

    def test_get_bbox(self):
        """Test bounding box extraction"""
        user = WMSBenchmark(self.env)
        user.layer = Mock()
        user.layer.boundingBox = [10.5, 20.5, 30.5, 40.5, 'EPSG:4326']

        bbox = user.get_bbox()
        assert bbox == (10.5, 20.5, 30.5, 40.5)

    @responses.activate
    @patch('wms.WebMapService')
    @patch('wms.generate_random_bbox')
    def test_load_map_success(self, mock_bbox_gen, mock_wms):
        """Test successful map loading"""
        # Setup mocked WMS service
        self._setup_mock_wms_service(mock_wms, mock_bbox_gen)

        # Mock successful map response
        responses.add(
            responses.GET,
            "http://example.com/wms",
            body=b"fake_image_data",
            status=200,
            content_type="image/jpeg"
        )

        user = WMSBenchmark(self.env)
        user.host = "http://example.com/wms"
        user.on_start()

        # Execute the task
        user.load_map()

        # Verify request was made
        assert len(responses.calls) == 1
        request_url = responses.calls[0].request.url
        assert "service=WMS" in request_url
        assert "request=GetMap" in request_url

    @responses.activate
    @patch('wms.WebMapService')
    @patch('wms.generate_random_bbox')
    def test_load_map_ogc_exception(self, mock_bbox_gen, mock_wms):
        """Test handling of OGC exceptions"""
        self._setup_mock_wms_service(mock_wms, mock_bbox_gen)

        user = WMSBenchmark(self.env)
        user.host = "http://example.com/wms"
        user.on_start()

        # Mock response with OGC exception
        with patch.object(user.client, 'get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/xml'}
            mock_response.text = '<?xml version="1.0"?><ServiceException>Layer not found</ServiceException>'
            mock_context = Mock()
            mock_context.__enter__ = Mock(return_value=mock_response)
            mock_context.__exit__ = Mock(return_value=None)
            mock_get.return_value = mock_context

            user.load_map()

            # Verify failure was recorded for OGC exception
            mock_response.failure.assert_called_once()

    def _setup_mock_wms_service(self, mock_wms, mock_bbox_gen):
        """Helper method to setup mocked WMS service"""
        mock_service = Mock()

        # Mock layer
        mock_layer = Mock()
        mock_layer.boundingBox = [0, 0, 1000, 1000, 'EPSG:3857']

        mock_service.contents = {'test_layer': mock_layer}

        # Mock get operation
        mock_operation = Mock()
        mock_operation.formatOptions = ['image/jpeg']
        mock_service.getOperationByName.return_value = mock_operation

        mock_wms.return_value = mock_service

        # Mock bbox generator
        mock_bbox_gen.return_value = iter([(100, 100, 200, 200)])

        return mock_service