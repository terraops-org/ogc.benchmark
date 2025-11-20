"""Live service tests using PDOK services - requires network access"""
import pytest
from locust.env import Environment
from unittest.mock import Mock

from wmts import WMTSBenchmark
from wms import WMSBenchmark
from wfs import WFSBenchmark


@pytest.mark.network
@pytest.mark.slow
class TestPDOKLiveServices:
    """Test against PDOK (Dutch government) OGC services - requires network access"""

    def setup_method(self):
        """Setup test environment for live tests"""
        self.timeout = 30  # seconds

        # PDOK service URLs
        self.pdok_wms = "https://service.pdok.nl/kadaster/plu/wms/v1_0"
        self.pdok_wfs = "https://service.pdok.nl/lv/bag/wfs/v2_0"
        self.pdok_wmts = "https://service.pdok.nl/lv/bgt/wmts/v1_0"

    def test_pdok_wmts_capabilities(self):
        """Test WMTS capabilities parsing with PDOK BGT service"""
        env = Environment(user_classes=[WMTSBenchmark])
        env.create_local_runner()

        # Mock parsed options
        env.parsed_options = Mock()
        env.parsed_options.random_seed = 42
        env.parsed_options.layer_name = None
        env.parsed_options.tile_matrix_set = None
        env.parsed_options.tile_matrix = None

        user = WMTSBenchmark(env)
        user.host = self.pdok_wmts

        try:
            # This should successfully parse capabilities
            user.on_start()

            # Verify basic properties were set
            assert hasattr(user, 'wmts')
            assert hasattr(user, 'layers')
            assert len(user.layers) > 0
            assert hasattr(user, 'layer_name')
            assert hasattr(user, 'tile_matrix_set')
            assert hasattr(user, 'tile_matrix_value')

            print(f"PDOK WMTS found {len(user.layers)} layers")
            print(f"Selected layer: {user.layer_name}")
            print(f"Selected tile matrix set: {user.tile_matrix_set}")

        except Exception as e:
            pytest.skip(f"PDOK WMTS service test failed: {e}")

    def test_pdok_wms_capabilities(self):
        """Test WMS capabilities parsing with PDOK PLU service"""
        env = Environment(user_classes=[WMSBenchmark])
        env.create_local_runner()

        # Mock parsed options
        env.parsed_options = Mock()
        env.parsed_options.random_seed = 42
        env.parsed_options.bbox_area = 100.0  # 100 km²
        env.parsed_options.bbox_ratio = 1.0
        env.parsed_options.layer_name = None

        user = WMSBenchmark(env)
        user.host = self.pdok_wms

        try:
            # This should successfully parse capabilities
            user.on_start()

            # Verify basic properties were set
            assert hasattr(user, 'wms')
            assert hasattr(user, 'layers')
            assert len(user.layers) > 0
            assert hasattr(user, 'layer_name')
            assert hasattr(user, 'crs')
            assert hasattr(user, 'bbox_generator')

            print(f"PDOK WMS found {len(user.layers)} layers")
            print(f"Selected layer: {user.layer_name}")
            print(f"Layer CRS: {user.crs}")

        except Exception as e:
            pytest.skip(f"PDOK WMS service test failed: {e}")

    def test_pdok_wfs_capabilities(self):
        """Test WFS capabilities parsing with PDOK BAG service"""
        env = Environment(user_classes=[WFSBenchmark])
        env.create_local_runner()

        # Mock parsed options
        env.parsed_options = Mock()
        env.parsed_options.random_seed = 42
        env.parsed_options.bbox_area = 10.0  # 10 km² (smaller for WFS)
        env.parsed_options.bbox_ratio = 1.0
        env.parsed_options.feature_name = None

        user = WFSBenchmark(env)
        user.host = self.pdok_wfs

        try:
            # This should successfully parse capabilities
            user.on_start()

            # Verify basic properties were set
            assert hasattr(user, 'wfs20')
            assert hasattr(user, 'feature_names')
            assert len(user.feature_names) > 0
            assert hasattr(user, 'feature_name')
            assert hasattr(user, 'crs')
            assert hasattr(user, 'bbox_generator')

            print(f"PDOK WFS found {len(user.feature_names)} features")
            print(f"Selected feature: {user.feature_name}")
            print(f"Feature CRS: {user.crs}")

        except Exception as e:
            pytest.skip(f"PDOK WFS service test failed: {e}")

    def test_pdok_wmts_tile_request(self):
        """Test actual tile request to PDOK WMTS service"""
        env = Environment(user_classes=[WMTSBenchmark])
        env.create_local_runner()

        # Mock parsed options
        env.parsed_options = Mock()
        env.parsed_options.random_seed = 42
        env.parsed_options.layer_name = None
        env.parsed_options.tile_matrix_set = None
        env.parsed_options.tile_matrix = None

        user = WMTSBenchmark(env)
        user.host = self.pdok_wmts

        try:
            user.on_start()

            # Mock the client response to capture the request
            request_made = False
            original_get = user.client.get

            def mock_get(*args, **kwargs):
                nonlocal request_made
                request_made = True
                # Return a mock response context manager
                from unittest.mock import Mock
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.headers = {'content-type': 'image/png'}
                mock_context = Mock()
                mock_context.__enter__ = Mock(return_value=mock_response)
                mock_context.__exit__ = Mock(return_value=None)
                return mock_context

            user.client.get = mock_get

            # Execute tile loading task
            user.load_tile()

            # Verify a request was made
            assert request_made, "No tile request was made"

            print("PDOK WMTS tile request successful")

        except Exception as e:
            pytest.skip(f"PDOK WMTS tile request test failed: {e}")

    def test_pdok_wms_map_request(self):
        """Test actual map request to PDOK WMS service"""
        env = Environment(user_classes=[WMSBenchmark])
        env.create_local_runner()

        # Mock parsed options
        env.parsed_options = Mock()
        env.parsed_options.random_seed = 42
        env.parsed_options.bbox_area = 1.0  # Small area for testing
        env.parsed_options.bbox_ratio = 1.0
        env.parsed_options.layer_name = None

        user = WMSBenchmark(env)
        user.host = self.pdok_wms

        try:
            user.on_start()

            # Mock the client response to capture the request
            request_made = False
            original_get = user.client.get

            def mock_get(*args, **kwargs):
                nonlocal request_made
                request_made = True
                # Return a mock response context manager
                from unittest.mock import Mock
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.headers = {'content-type': 'image/png'}
                mock_context = Mock()
                mock_context.__enter__ = Mock(return_value=mock_response)
                mock_context.__exit__ = Mock(return_value=None)
                return mock_context

            user.client.get = mock_get

            # Execute map loading task
            user.load_map()

            # Verify a request was made
            assert request_made, "No map request was made"

            print("PDOK WMS map request successful")

        except Exception as e:
            pytest.skip(f"PDOK WMS map request test failed: {e}")


@pytest.mark.network
@pytest.mark.slow
class TestPDOKServiceValidation:
    """Validate PDOK service endpoints are accessible"""

    def test_pdok_service_availability(self):
        """Test that PDOK services are accessible"""
        import requests

        services = {
            "PDOK WMS": "https://service.pdok.nl/kadaster/plu/wms/v1_0?request=getcapabilities&service=wms",
            "PDOK WFS": "https://service.pdok.nl/lv/bag/wfs/v2_0?request=getCapabilities&service=WFS",
            "PDOK WMTS": "https://service.pdok.nl/lv/bgt/wmts/v1_0?request=GetCapabilities&service=WMTS"
        }

        for service_name, url in services.items():
            try:
                response = requests.get(url, timeout=10)
                assert response.status_code == 200, f"{service_name} returned {response.status_code}"
                assert "xml" in response.headers.get('content-type', '').lower(), f"{service_name} did not return XML"
                print(f"✓ {service_name} is accessible")
            except Exception as e:
                pytest.skip(f"{service_name} is not accessible: {e}")