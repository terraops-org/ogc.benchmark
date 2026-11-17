import logging

import gevent

logger = logging.getLogger(__name__)


class ArgError(Exception):
    """A command-line option does not match what the service offers (layer, CRS, tile matrix, feature type)."""

    def handle_error(self, environment):
        """Log the message, stop the whole run (not only this simulated user), and re-raise."""
        logger.error(str(self))
        environment.process_exit_code = 1  # a run that stops itself must not report success
        if environment.runner is not None:
            # In its own greenlet: called from inside a user, quit() would kill its caller half-way
            # (Locust stops the calling user too) and leave the runner in "cleanup" until -t runs out.
            gevent.spawn(environment.runner.quit)
        raise self
