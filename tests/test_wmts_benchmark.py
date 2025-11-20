"""Integration tests for WMTS benchmark"""
import pytest
import responses
from unittest.mock import Mock, patch, MagicMock
from locust.env import Environment
from locust.exception import StopUser

from wmts import WMTSBenchmark
from exceptions import LayerNameArgError, TileMatrixSetArgError, TileMatrixArgError


class TestWMTSBenchmark:
    """Test WMTS benchmark functionality"""

    def setup_method(self):
        """Setup test environment"""
        self.env = Environment(user_classes=[WMTSBenchmark])
        self.env.create_local_runner()

        # Create mock parsed options
        self.env.parsed_options = Mock()
        self.env.parsed_options.random_seed = 42
        self.env.parsed_options.layer_name = None
        self.env.parsed_options.tile_matrix_set = None
        self.env.parsed_options.tile_matrix = None

    def test_user_initialization(self):
        """Test basic user initialization"""
        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"

        assert user.host == "http://example.com/wmts"
        assert user.environment == self.env
        assert hasattr(user, 'wait_time')

    @patch('wmts.WebMapTileService')
    def test_on_start_success(self, mock_wmts):
        """Test successful on_start initialization"""
        # Mock WMTS service
        mock_service = Mock()
        mock_service.contents = {
            'layer1': Mock(),
            'layer2': Mock()
        }

        # Mock layer with tile matrix sets
        mock_layer = Mock()
        mock_layer.tilematrixsets = ['EPSG:3857', 'EPSG:4326']
        mock_service.contents = {'layer1': mock_layer}

        # Mock tile matrix set
        mock_tms = Mock()
        mock_tms.tilematrix = {'05': Mock(), '06': Mock(), '07': Mock()}
        mock_service.tilematrixsets = {'EPSG:3857': mock_tms}

        # Mock tile matrix
        mock_tm = Mock()
        mock_tm.matrixwidth = 100
        mock_tm.matrixheight = 100
        mock_tms.tilematrix = {'07': mock_tm}

        mock_wmts.return_value = mock_service

        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"

        # Should not raise exception
        user.on_start()

        # Verify service was initialized
        mock_wmts.assert_called_once_with("http://example.com/wmts")
        assert hasattr(user, 'wmts')
        assert hasattr(user, 'layers')

    @patch('wmts.WebMapTileService')
    def test_on_start_connection_failure(self, mock_wmts):
        """Test handling of connection failures"""
        mock_wmts.side_effect = Exception("Connection failed")

        user = WMTSBenchmark(self.env)
        user.host = "http://invalid.com/wmts"

        with pytest.raises(Exception):
            user.on_start()

    @patch('wmts.WebMapTileService')
    def test_invalid_layer_name(self, mock_wmts):
        """Test error handling for invalid layer names"""
        # Mock service with specific layers
        mock_service = Mock()
        mock_service.contents = {'valid_layer': Mock()}
        mock_wmts.return_value = mock_service

        # Set invalid layer name
        self.env.parsed_options.layer_name = "invalid_layer"

        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"

        with pytest.raises(LayerNameArgError):
            user.on_start()

    @responses.activate
    @patch('wmts.WebMapTileService')
    def test_load_tile_success(self, mock_wmts):
        """Test successful tile loading"""
        # Setup mocked WMTS service
        self._setup_mock_wmts_service(mock_wmts)

        # Mock successful tile response
        responses.add(
            responses.GET,
            "http://example.com/wmts",
            body=b"fake_image_data",
            status=200,
            content_type="image/png"
        )

        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"
        user.on_start()

        # Execute the task
        user.load_tile()

        # Verify request was made
        assert len(responses.calls) == 1

    @responses.activate
    @patch('wmts.WebMapTileService')
    def test_load_tile_http_error(self, mock_wmts):
        """Test handling of HTTP errors in tile loading"""
        self._setup_mock_wmts_service(mock_wmts)

        # Mock HTTP error response
        responses.add(
            responses.GET,
            "http://example.com/wmts",
            status=404
        )

        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"
        user.on_start()

        # Mock the client.get to capture catch_response behavior
        with patch.object(user.client, 'get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.reason = "Not Found"
            mock_context = Mock()
            mock_context.__enter__ = Mock(return_value=mock_response)
            mock_context.__exit__ = Mock(return_value=None)
            mock_get.return_value = mock_context

            user.load_tile()

            # Verify failure was recorded
            mock_response.failure.assert_called_once()

    @responses.activate
    @patch('wmts.WebMapTileService')
    def test_load_tile_invalid_content_type(self, mock_wmts):
        """Test handling of invalid content types"""
        self._setup_mock_wmts_service(mock_wmts)

        user = WMTSBenchmark(self.env)
        user.host = "http://example.com/wmts"
        user.on_start()

        # Mock response with invalid content type
        with patch.object(user.client, 'get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'text/html'}
            mock_context = Mock()
            mock_context.__enter__ = Mock(return_value=mock_response)
            mock_context.__exit__ = Mock(return_value=None)
            mock_get.return_value = mock_context

            user.load_tile()

            # Verify failure was recorded for invalid content type
            mock_response.failure.assert_called_once()

    def _setup_mock_wmts_service(self, mock_wmts):
        """Helper method to setup mocked WMTS service"""
        mock_service = Mock()

        # Mock layer
        mock_layer = Mock()
        mock_layer.tilematrixsets = ['EPSG:3857']
        mock_layer.styles = {'default': Mock()}
        mock_layer.format = 'image/png'

        mock_service.contents = {'test_layer': mock_layer}

        # Mock tile matrix set
        mock_tms = Mock()
        mock_tm = Mock()
        mock_tm.matrixwidth = 100
        mock_tm.matrixheight = 100
        mock_tms.tilematrix = {'07': mock_tm}

        mock_service.tilematrixsets = {'EPSG:3857': mock_tms}

        mock_wmts.return_value = mock_service

        return mock_service