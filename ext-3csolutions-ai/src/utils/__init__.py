"""
Utility modules for the document extraction pipeline.

Provides:
- Custom exceptions
- Input validation
- Security utilities
- Retry decorators
- Contextual logging
- Configuration management
- Constants
"""

# Make key utilities available at package level
from .exceptions import (
    PipelineException,
    ValidationError,
    SecurityError,
    ConfigurationError,
    S3OperationError,
    TextractOperationError,
    BedrockOperationError,
    DynamoDBOperationError,
)

from .validation import (
    validate_s3_key,
    validate_request_id,
    validate_schema_key,
)

from .security import (
    sanitize_filename,
    validate_zip_size,
    validate_file_size,
    escape_xml_content,
    safe_log_dict,
)

from .logger import (
    get_logger,
    set_request_context,
    get_request_context,
)

from .config import (
    PipelineConfig,
    get_config,
)

__all__ = [
    # Exceptions
    'PipelineException',
    'ValidationError',
    'SecurityError',
    'ConfigurationError',
    'S3OperationError',
    'TextractOperationError',
    'BedrockOperationError',
    'DynamoDBOperationError',
    # Validation
    'validate_s3_key',
    'validate_request_id',
    'validate_schema_key',
    # Security
    'sanitize_filename',
    'validate_zip_size',
    'validate_file_size',
    'escape_xml_content',
    'safe_log_dict',
    # Logging
    'get_logger',
    'set_request_context',
    'get_request_context',
    # Config
    'PipelineConfig',
    'get_config',
]
