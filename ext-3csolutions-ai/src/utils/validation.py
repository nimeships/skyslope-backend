"""
Input Validation Utilities for Document Extraction Pipeline
Provides secure validation for S3 keys, file formats, and other inputs
"""

import re
from typing import Tuple
from .exceptions import ValidationError


# Validation Constants
# NOTE: Extension is now OPTIONAL (Windows-friendly)
# File type validated by content_type (upload) and magic bytes (processing)
VALID_S3_KEY_PATTERN = r'^input/([a-zA-Z0-9_-]{10,128}(?:_\d{8}T\d{6}Z)?)/([a-zA-Z0-9_.-]{1,255})$'
VALID_REQUEST_ID_PATTERN = r'^[a-zA-Z0-9_-]{10,128}(?:_\d{8}T\d{6}Z)?$'
VALID_PDF_EXTENSIONS = {'.pdf', '.PDF'}
VALID_ZIP_EXTENSIONS = {'.zip', '.ZIP'}
MAX_FILENAME_LENGTH = 255

# SECURITY: Whitelist for schema S3 keys (prevents SSRF)
ALLOWED_SCHEMA_PREFIXES = [
    'schemas/',
    'config/schemas/',
    'templates/schemas/'
]


def validate_s3_key(s3_key: str) -> Tuple[str, str]:
    """
    Validate S3 key format and extract request ID.

    NOTE: File extensions are now OPTIONAL. File type is validated by:
    - content_type during upload (presigned URL)
    - Magic bytes during processing (file_type_detector)

    Accepted formats:
    - input/{request_id}/{filename}  (no extension - Windows friendly)
    - input/{request_id}/{filename}.zip
    - input/{request_id}/{filename}.pdf
    - input/{request_id}_{timestamp}/{filename}
    - input/{request_id}_{timestamp}/{filename}.zip

    Args:
        s3_key: S3 object key from environment

    Returns:
        Tuple of (validated_key, request_id_base)
        Where request_id_base is the ID without timestamp

    Raises:
        ValidationError: If key format is invalid or contains malicious patterns
    """
    if not s3_key or not isinstance(s3_key, str):
        raise ValidationError("S3 object key cannot be empty or non-string")

    # Check for path traversal attempts
    if '..' in s3_key or s3_key.startswith('/'):
        raise ValidationError(f"S3 key contains path traversal patterns: {s3_key}")

    # Validate format with regex
    match = re.match(VALID_S3_KEY_PATTERN, s3_key)

    if not match:
        raise ValidationError(
            f"Invalid S3 key format: {s3_key}. "
            f"Expected: input/{{request_id}}/{{filename}} (extension optional)"
        )

    # Extract components
    request_id_with_timestamp = match.group(1)
    filename = match.group(2)

    # Extract base request_id (remove timestamp if present)
    # Format: abc123def456_20260205T234831Z -> abc123def456
    if '_' in request_id_with_timestamp:
        parts = request_id_with_timestamp.rsplit('_', 1)
        # Check if last part is timestamp format (15 chars, contains T and Z)
        if len(parts) == 2 and len(parts[1]) == 15 and 'T' in parts[1] and parts[1].endswith('Z'):
            # It's a timestamp, use base ID
            request_id = parts[0]
        else:
            # Underscore is part of the ID itself
            request_id = request_id_with_timestamp
    else:
        request_id = request_id_with_timestamp

    # Validate base request_id format
    if not re.match(r'^[a-zA-Z0-9_-]{10,128}$', request_id):
        raise ValidationError(
            f"Invalid request_id format: {request_id}. "
            f"Must be 10-128 alphanumeric characters, hyphens, or underscores"
        )

    # Validate filename doesn't contain dangerous characters
    dangerous_chars = ['<', '>', '|', '\0', '\n', '\r']
    if any(char in filename for char in dangerous_chars):
        raise ValidationError(f"Filename contains dangerous characters: {filename}")

    return s3_key, request_id


def validate_request_id(request_id: str) -> str:
    """
    Validate request ID format and extract base ID if timestamp present.

    Accepts:
    - abc123def456 (base ID)
    - abc123def456_20260205T234831Z (ID with timestamp)

    Args:
        request_id: Request identifier (with or without timestamp)

    Returns:
        Base request_id (without timestamp)

    Raises:
        ValidationError: If request_id is invalid
    """
    if not request_id or not isinstance(request_id, str):
        raise ValidationError("Request ID cannot be empty or non-string")

    # Extract base ID if timestamp present
    if '_' in request_id:
        parts = request_id.rsplit('_', 1)
        # Check if last part is timestamp format
        if len(parts) == 2 and len(parts[1]) == 15 and 'T' in parts[1] and parts[1].endswith('Z'):
            base_id = parts[0]
        else:
            base_id = request_id
    else:
        base_id = request_id

    # Validate base ID format
    if not re.match(r'^[a-zA-Z0-9_-]{10,128}$', base_id):
        raise ValidationError(
            f"Invalid request_id format: {base_id}. "
            f"Must be 10-128 alphanumeric characters, hyphens, or underscores"
        )

    return base_id


def validate_schema_key(schema_key: str) -> str:
    """
    Validate and normalize schema S3 key with SSRF protection.

    SECURITY FIX: Now enforces whitelist to prevent SSRF attacks.
    Schema files MUST be in approved directories only.

    Handles both S3 URI and plain key formats:
    - s3://bucket/schemas/file.json -> schemas/file.json
    - schemas/file.json -> schemas/file.json

    Args:
        schema_key: Schema S3 key or URI

    Returns:
        Normalized schema key

    Raises:
        ValidationError: If schema key is invalid or not in whitelist
    """
    if not schema_key or not isinstance(schema_key, str):
        raise ValidationError("Schema key cannot be empty or non-string")

    # Parse S3 URI if provided
    if schema_key.startswith("s3://"):
        parts = schema_key.split("/", 3)
        if len(parts) < 4:
            raise ValidationError(f"Invalid S3 URI format: {schema_key}")
        schema_key = parts[3]  # Extract key after s3://bucket/

    # Validate key format
    if not schema_key.endswith('.json'):
        raise ValidationError(f"Schema key must end with .json: {schema_key}")

    # Check for path traversal
    if '..' in schema_key:
        raise ValidationError(f"Schema key contains path traversal: {schema_key}")

    # SECURITY: Whitelist validation (prevents SSRF)
    # Schema MUST start with one of the allowed prefixes
    if not any(schema_key.startswith(prefix) for prefix in ALLOWED_SCHEMA_PREFIXES):
        raise ValidationError(
            f"Schema key '{schema_key}' not in allowed directories. "
            f"Allowed prefixes: {', '.join(ALLOWED_SCHEMA_PREFIXES)}"
        )

    return schema_key


def validate_file_extension(filename: str, allowed_extensions: set) -> bool:
    """
    Validate file has an allowed extension.

    Args:
        filename: File name to validate
        allowed_extensions: Set of allowed extensions (e.g., {'.pdf', '.PDF'})

    Returns:
        True if extension is valid

    Raises:
        ValidationError: If extension is invalid
    """
    if not filename:
        raise ValidationError("Filename cannot be empty")

    import os
    _, ext = os.path.splitext(filename)

    if ext not in allowed_extensions:
        raise ValidationError(
            f"Invalid file extension: {ext}. "
            f"Allowed: {', '.join(allowed_extensions)}"
        )

    return True


def is_pdf_file(filename: str) -> bool:
    """Check if filename has a PDF extension."""
    import os
    _, ext = os.path.splitext(filename)
    return ext in VALID_PDF_EXTENSIONS


def is_zip_file(filename: str) -> bool:
    """Check if filename has a ZIP extension."""
    import os
    _, ext = os.path.splitext(filename)
    return ext in VALID_ZIP_EXTENSIONS
