"""
Retry Decorator and Error Handling Utilities
Provides configurable retry logic with exponential backoff for AWS services
"""

import time
import functools
from typing import Callable, Type, Tuple
from botocore.exceptions import ClientError
from .exceptions import MaxRetriesExceededError
from .logger import get_logger

logger = get_logger(__name__)


# Retry Configuration Constants
MAX_RETRY_ATTEMPTS = 5  # AWS SDK default
EXPONENTIAL_BACKOFF_BASE = 2  # Standard exponential backoff
MAX_BACKOFF_TIME = 60  # Maximum wait time between retries (seconds)


def retry_on_throttling(
    max_attempts: int = MAX_RETRY_ATTEMPTS,
    backoff_base: int = EXPONENTIAL_BACKOFF_BASE,
    max_backoff: int = MAX_BACKOFF_TIME,
    retriable_exceptions: Tuple[Type[Exception], ...] = (ClientError,)
):
    """
    Decorator to retry AWS API calls on throttling with exponential backoff.

    Automatically retries on:
    - ThrottlingException
    - ProvisionedThroughputExceededException
    - RequestLimitExceeded

    Args:
        max_attempts: Maximum number of retry attempts (default: 5)
        backoff_base: Base for exponential backoff calculation (default: 2)
        max_backoff: Maximum backoff time in seconds (default: 60)
        retriable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorated function with retry logic

    Raises:
        MaxRetriesExceededError: If max_attempts exceeded
        Original exception: If error is not retriable

    Example:
        @retry_on_throttling(max_attempts=3, backoff_base=2)
        def invoke_model(self, payload):
            return bedrock_client.invoke_model(...)
    """
    # Throttling error codes to retry
    THROTTLING_ERROR_CODES = {
        'ThrottlingException',
        'ProvisionedThroughputExceededException',
        'RequestLimitExceeded',
        'TooManyRequestsException',
        'SlowDown'
    }

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    # Attempt the function call
                    return func(*args, **kwargs)

                except retriable_exceptions as e:
                    last_exception = e

                    # Check if this is a throttling error
                    is_throttling = False
                    error_code = None

                    if isinstance(e, ClientError):
                        error_code = e.response.get('Error', {}).get('Code', '')
                        is_throttling = error_code in THROTTLING_ERROR_CODES

                    # If not retriable or last attempt, raise immediately
                    if not is_throttling or attempt >= max_attempts - 1:
                        logger.error(
                            f"Error in {func.__name__}: {e}",
                            extra={
                                'function': func.__name__,
                                'attempt': attempt + 1,
                                'error_code': error_code,
                                'is_throttling': is_throttling
                            },
                            exc_info=True
                        )
                        raise

                    # Calculate backoff time (exponential with cap)
                    wait_time = min(backoff_base ** attempt, max_backoff)

                    logger.warning(
                        f"Throttled in {func.__name__}, retry {attempt + 1}/{max_attempts} "
                        f"in {wait_time}s (error: {error_code})",
                        extra={
                            'function': func.__name__,
                            'attempt': attempt + 1,
                            'max_attempts': max_attempts,
                            'wait_time': wait_time,
                            'error_code': error_code
                        }
                    )

                    # Wait before retrying
                    time.sleep(wait_time)

                except Exception as e:
                    # Non-retriable exception - fail immediately
                    logger.error(
                        f"Non-retriable error in {func.__name__}: {e}",
                        extra={'function': func.__name__, 'attempt': attempt + 1},
                        exc_info=True
                    )
                    raise

            # If we get here, all retries were exhausted
            raise MaxRetriesExceededError(
                f"Max retries ({max_attempts}) exceeded for {func.__name__}"
            ) from last_exception

        return wrapper
    return decorator


def with_error_context(error_type: str, operation: str):
    """
    Decorator to add error context to exceptions.

    Args:
        error_type: Type of operation (e.g., 'S3', 'Textract', 'Bedrock')
        operation: Specific operation (e.g., 'download', 'upload', 'invoke')

    Example:
        @with_error_context('S3', 'download')
        def download_file(self, key):
            return s3_client.get_object(...)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Add context to error message
                context_msg = f"{error_type} {operation} failed in {func.__name__}"
                logger.error(
                    context_msg,
                    extra={
                        'error_type': error_type,
                        'operation': operation,
                        'function': func.__name__
                    },
                    exc_info=True
                )
                # Re-raise with context preserved
                raise type(e)(f"{context_msg}: {str(e)}") from e

        return wrapper
    return decorator
