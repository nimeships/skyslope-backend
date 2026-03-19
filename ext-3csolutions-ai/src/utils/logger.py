"""
Enhanced Logging with Contextual Request ID Tracking
Provides structured logging with request context for distributed tracing
"""

import logging
import json
import contextvars
from typing import Any, Dict, Optional
from datetime import datetime


# Context variable for request ID (thread-safe)
request_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    'request_id',
    default='UNKNOWN'
)


class ContextualLoggerAdapter(logging.LoggerAdapter):
    """
    Custom logger adapter that automatically includes request context in all log messages.

    Adds request_id to every log entry for distributed tracing.
    """

    def process(self, msg, kwargs):
        """Add request_id context to log messages"""
        request_id = request_context.get()

        # Add request_id to extra fields if not already present
        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        if 'request_id' not in kwargs['extra']:
            kwargs['extra']['request_id'] = request_id

        # Format message with request_id prefix
        formatted_msg = f"[{request_id}] {msg}"

        return formatted_msg, kwargs


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging compatible with CloudWatch Insights.

    Outputs logs in JSON format for better querying and analysis.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', 'UNKNOWN')
        }

        # Standard LogRecord attributes to exclude from extra fields
        reserved_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName', 'levelname',
            'levelno', 'lineno', 'module', 'msecs', 'message', 'pathname', 'process',
            'processName', 'relativeCreated', 'thread', 'threadName', 'exc_info',
            'exc_text', 'stack_info', 'request_id', 'getMessage', 'taskName'
        }

        # Dynamically add ALL extra fields from the log record
        for attr_name in dir(record):
            if attr_name.startswith('_') or attr_name in reserved_attrs:
                continue

            try:
                attr_value = getattr(record, attr_name)
                # Skip callable attributes (methods)
                if callable(attr_value):
                    continue
                # Add to log data if not already present
                if attr_name not in log_data:
                    log_data[attr_name] = attr_value
            except Exception:
                # Skip attributes that raise exceptions when accessed
                continue

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data)


# Global logger configuration flag
_logger_configured = False


def configure_logging(
    level: str = 'INFO',
    structured: bool = True,
    log_format: Optional[str] = None
) -> None:
    """
    Configure global logging settings for the pipeline.

    Args:
        level: Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        structured: If True, use JSON structured logging (default: True)
        log_format: Custom log format string (ignored if structured=True)
    """
    global _logger_configured

    if _logger_configured:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Set formatter
    if structured:
        formatter = StructuredFormatter()
    else:
        format_string = log_format or '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
        formatter = logging.Formatter(format_string)

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    _logger_configured = True


def get_logger(name: str) -> ContextualLoggerAdapter:
    """
    Get a contextual logger instance with request ID tracking.

    Args:
        name: Logger name (typically __name__)

    Returns:
        ContextualLoggerAdapter instance
    """
    base_logger = logging.getLogger(name)
    return ContextualLoggerAdapter(base_logger, {})


def set_request_context(request_id: str) -> None:
    """
    Set the current request context for all subsequent log calls.

    Args:
        request_id: Unique request identifier

    Example:
        set_request_context('req-12345')
        logger.info('Processing started')  # Output: [req-12345] Processing started
    """
    request_context.set(request_id)


def get_request_context() -> str:
    """
    Get the current request ID from context.

    Returns:
        Current request_id or 'UNKNOWN' if not set
    """
    return request_context.get()


def clear_request_context() -> None:
    """Clear the request context (useful for cleanup)"""
    request_context.set('UNKNOWN')


# Default logger instance (backward compatibility)
logger = get_logger('pipeline')
