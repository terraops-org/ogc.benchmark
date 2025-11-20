"""Tests for custom exceptions"""
import pytest
from unittest.mock import Mock
from exceptions import (
    BaseArgError,
    LayerNameArgError,
    TileMatrixSetArgError,
    TileMatrixArgError,
    FeatureNameArgError
)


class TestBaseArgError:
    """Test the base argument error class"""

    def test_base_arg_error_inheritance(self):
        """Test that custom exceptions inherit from BaseArgError"""
        assert issubclass(LayerNameArgError, BaseArgError)
        assert issubclass(TileMatrixSetArgError, BaseArgError)
        assert issubclass(TileMatrixArgError, BaseArgError)
        assert issubclass(FeatureNameArgError, BaseArgError)

    def test_handle_error_method(self):
        """Test the handle_error method"""
        # Create mock environment
        mock_env = Mock()
        mock_runner = Mock()
        mock_env.runner = mock_runner

        # Create exception instance
        error = BaseArgError("Test error message")

        # Test that handle_error method exists and can be called
        with pytest.raises(SystemExit):
            error.handle_error(mock_env)

        # Verify that runner.quit() was called
        mock_runner.quit.assert_called_once()


class TestSpecificExceptions:
    """Test specific exception classes"""

    def test_layer_name_arg_error(self):
        """Test LayerNameArgError"""
        error_msg = "Invalid layer name: test_layer"
        error = LayerNameArgError(error_msg)

        assert str(error) == error_msg
        assert isinstance(error, BaseArgError)

    def test_tile_matrix_set_arg_error(self):
        """Test TileMatrixSetArgError"""
        error_msg = "Invalid tile matrix set: EPSG:9999"
        error = TileMatrixSetArgError(error_msg)

        assert str(error) == error_msg
        assert isinstance(error, BaseArgError)

    def test_tile_matrix_arg_error(self):
        """Test TileMatrixArgError"""
        error_msg = "Invalid tile matrix: 99"
        error = TileMatrixArgError(error_msg)

        assert str(error) == error_msg
        assert isinstance(error, BaseArgError)

    def test_feature_name_arg_error(self):
        """Test FeatureNameArgError"""
        error_msg = "Invalid feature name: test:feature"
        error = FeatureNameArgError(error_msg)

        assert str(error) == error_msg
        assert isinstance(error, BaseArgError)