"""
Shared validator for unique_id format validation.
Used across multiple models (DownloadRequest, MonitorRequest).
"""
import re

UNIQUE_ID_PATTERN = r'^[a-f0-9]{32}_\d{8}T\d{6}Z$'
UNIQUE_ID_ERROR_MSG = (
    "unique_id must match format: {uuid}_{timestamp} "
    "(e.g., 17eeb4ec5aa947708990cb220295e4c4_20260205T181422Z)"
)


def validate_unique_id_format(v: str) -> str:
    """
    Validate unique_id matches expected format: {uuid_hex}_{timestamp}

    Expected format: 32-char hex UUID + underscore + 16-char timestamp (YYYYMMDDTHHMMSSz)
    Example: 17eeb4ec5aa947708990cb220295e4c4_20260205T181422Z

    Args:
        v: The unique_id string to validate

    Returns:
        str: The validated unique_id

    Raises:
        ValueError: If unique_id doesn't match expected format
    """
    if not re.match(UNIQUE_ID_PATTERN, v):
        raise ValueError(UNIQUE_ID_ERROR_MSG)
    return v
