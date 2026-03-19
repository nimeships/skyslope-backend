"""
Timeout Utilities for Pipeline Operations
Provides timeout wrapper to prevent long-running requests
"""

import signal
import functools
from typing import Callable, Any
from .logger import get_logger
from .exceptions import ConfigurationError

logger = get_logger(__name__)


class TimeoutError(Exception):
    """Raised when operation exceeds timeout."""
    pass


def timeout_handler(signum, frame):
    """Signal handler for SIGALRM (timeout)."""
    raise TimeoutError("Operation timed out")


def with_timeout(seconds: int):
    """
    Decorator to add timeout to a function.

    NOTE: Only works on Unix/Linux systems (uses SIGALRM).
    On Windows, this decorator logs a warning and runs without timeout.

    Args:
        seconds: Maximum execution time in seconds

    Raises:
        TimeoutError: If function exceeds timeout

    Example:
        @with_timeout(300)  # 5 minute timeout
        def process_pipeline():
            # ... long running operation
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Check if SIGALRM is available (Unix/Linux only)
            if not hasattr(signal, 'SIGALRM'):
                logger.warning(
                    f"SIGALRM not available (Windows system) - running {func.__name__} without timeout",
                    extra={'timeout_seconds': seconds}
                )
                return func(*args, **kwargs)

            # Set alarm signal handler
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)

            try:
                # Set alarm
                signal.alarm(seconds)

                logger.info(
                    f"Starting {func.__name__} with {seconds}s timeout",
                    extra={'timeout_seconds': seconds, 'function': func.__name__}
                )

                # Execute function
                result = func(*args, **kwargs)

                # Cancel alarm
                signal.alarm(0)

                return result

            except TimeoutError:
                logger.error(
                    f"{func.__name__} exceeded timeout of {seconds}s",
                    extra={
                        'timeout_seconds': seconds,
                        'function': func.__name__,
                        'error_type': 'TimeoutError'
                    }
                )
                raise

            finally:
                # Always restore old handler and cancel alarm
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

        return wrapper
    return decorator


class TimeoutContext:
    """
    Context manager for timeout operations.

    NOTE: Only works on Unix/Linux systems (uses SIGALRM).
    On Windows, logs warning and runs without timeout.

    Example:
        with TimeoutContext(300) as timeout:
            # ... long running operation
            pass
    """

    def __init__(self, seconds: int):
        """
        Initialize timeout context.

        Args:
            seconds: Maximum execution time in seconds
        """
        self.seconds = seconds
        self.old_handler = None

    def __enter__(self):
        """Enter timeout context."""
        # Check if SIGALRM is available
        if not hasattr(signal, 'SIGALRM'):
            logger.warning(
                f"SIGALRM not available (Windows system) - running without timeout",
                extra={'timeout_seconds': self.seconds}
            )
            return self

        self.old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(self.seconds)

        logger.info(
            f"Timeout context started: {self.seconds}s",
            extra={'timeout_seconds': self.seconds}
        )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit timeout context."""
        # Only restore if SIGALRM is available
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)
            if self.old_handler:
                signal.signal(signal.SIGALRM, self.old_handler)

        # Don't suppress exceptions
        return False


def validate_timeout(timeout: int, min_timeout: int = 1, max_timeout: int = 3600):
    """
    Validate timeout value is within acceptable range.

    Args:
        timeout: Timeout value to validate
        min_timeout: Minimum allowed timeout (default: 1 second)
        max_timeout: Maximum allowed timeout (default: 1 hour)

    Raises:
        ConfigurationError: If timeout is invalid
    """
    if not isinstance(timeout, int):
        raise ConfigurationError(f"Timeout must be integer, got {type(timeout)}")

    if timeout < min_timeout:
        raise ConfigurationError(
            f"Timeout {timeout}s too short (minimum: {min_timeout}s)"
        )

    if timeout > max_timeout:
        raise ConfigurationError(
            f"Timeout {timeout}s too long (maximum: {max_timeout}s)"
        )
