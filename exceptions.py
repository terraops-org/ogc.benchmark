import sys
import logging

logger = logging.getLogger(__name__)


# Base class for all custom exceptions
class BaseArgError(Exception):
    """
    Base class for handling argument errors in a consistent way.
    It provides a method to log the error and stop the environment.
    """

    def handle_error(self, environment):
        """
        Handle the error by logging and stopping the environment.
        """
        logger.error(str(self))  # Log the error
        environment.runner.quit()  # Gracefully stop the Locust runner if needed
        sys.exit(str(self))  # Provide an error message and exit


# Specific exception classes inheriting from BaseArgError
class FeatureNameArgError(BaseArgError):
    """Raised when there is an issue with a Feature name argument."""

    pass


class LayerNameArgError(BaseArgError):
    """Raised when there is an issue with a Layer name argument."""

    pass


class TileMatrixSetArgError(BaseArgError):
    """Raised when there is an issue with a TileMatrixSet argument."""

    pass


class TileMatrixArgError(BaseArgError):
    """Raised when there is an issue with a TileMatrix argument."""

    pass


# import sys
# import logging

# logger = logging.getLogger(__name__)
# TODO Class abstraction to reduce contents
# class FeatureNameArgError(Exception):
#    def handle_error(self, environment):
#        """
#        Handle the error by logging and stopping the environment.
#        """
#        logger.error(str(self))
#        environment.runner.quit()  # Gracefully stop the Locust runner if you need to terminate the entire test
#        sys.exit(str(self))  # Provide an error message directly to the console if running in a standalone mode

# class LayerNameArgError(Exception):
#    def handle_error(self, environment):
#        """
#        Handle the error by logging and stopping the environment.
#        """
#        logger.error(str(self))
#        environment.runner.quit()  # Gracefully stop the Locust runner if you need to terminate the entire test
#        sys.exit(str(self))  # Provide an error message directly to the console if running in a standalone mode


# class TileMatrixSetArgError(Exception):
#    def handle_error(self, environment):
#        """
#        Handle the error by logging and stopping the environment.
#        """
#        logger.error(str(self))
#        environment.runner.quit()  # Gracefully stop the Locust runner if you need to terminate the entire test
#        sys.exit(str(self))  # Provide an error message directly to the console if running in a standalone mode

# class TileMatrixArgError(Exception):
#    def handle_error(self, environment):
#        """
#        Handle the error by logging and stopping the environment.
#        """
#        logger.error(str(self))
#        environment.runner.quit()  # Gracefully stop the Locust runner if you need to terminate the entire test
#        sys.exit(str(self))  # Provide an error message directly to the console if running in a standalone mode
