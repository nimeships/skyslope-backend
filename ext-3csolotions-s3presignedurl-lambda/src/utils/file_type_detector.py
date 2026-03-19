"""
File Type Detection Utility

Validates files based on content (magic bytes) rather than file extensions.
This is more secure and handles cases where filenames don't include extensions.
"""

import io
from typing import Literal, Tuple


# Magic bytes for file type detection
MAGIC_BYTES = {
    'zip': [
        b'PK\x03\x04',  # Standard ZIP
        b'PK\x05\x06',  # Empty ZIP
        b'PK\x07\x08',  # Spanned ZIP
    ],
    'pdf': [
        b'%PDF-',  # PDF header
    ],
    'json': [
        b'{',  # JSON object
        b'[',  # JSON array
    ],
}


FileType = Literal['zip', 'pdf', 'json', 'unknown']


def detect_file_type(file_bytes: bytes, max_check_bytes: int = 512) -> FileType:
    """
    Detect file type based on magic bytes (file signature).

    Args:
        file_bytes: File content bytes
        max_check_bytes: Maximum bytes to check from start of file

    Returns:
        FileType: Detected file type ('zip', 'pdf', 'json', or 'unknown')

    Examples:
        >>> detect_file_type(b'PK\\x03\\x04...')
        'zip'
        >>> detect_file_type(b'%PDF-1.4...')
        'pdf'
    """
    if not file_bytes:
        return 'unknown'

    # Check only first portion of file for efficiency
    header = file_bytes[:max_check_bytes]

    # Check ZIP signatures
    for magic in MAGIC_BYTES['zip']:
        if header.startswith(magic):
            return 'zip'

    # Check PDF signature
    for magic in MAGIC_BYTES['pdf']:
        if header.startswith(magic):
            return 'pdf'

    # Check JSON (more lenient - look for opening brace/bracket after whitespace)
    stripped = header.lstrip()
    for magic in MAGIC_BYTES['json']:
        if stripped.startswith(magic):
            return 'json'

    return 'unknown'


def validate_file_type(file_bytes: bytes, expected_type: FileType) -> Tuple[bool, str]:
    """
    Validate that file content matches expected type.

    Args:
        file_bytes: File content bytes
        expected_type: Expected file type

    Returns:
        Tuple of (is_valid, error_message)

    Examples:
        >>> validate_file_type(b'PK\\x03\\x04...', 'zip')
        (True, '')
        >>> validate_file_type(b'PK\\x03\\x04...', 'pdf')
        (False, 'File content is zip but expected pdf')
    """
    detected = detect_file_type(file_bytes)

    if detected == 'unknown':
        return False, f"Unable to detect file type. Expected {expected_type}"

    if detected != expected_type:
        return False, f"File content is {detected} but expected {expected_type}"

    return True, ""


def is_zip_file(file_bytes: bytes) -> bool:
    """Check if file is a ZIP archive based on content."""
    return detect_file_type(file_bytes) == 'zip'


def is_pdf_file(file_bytes: bytes) -> bool:
    """Check if file is a PDF document based on content."""
    return detect_file_type(file_bytes) == 'pdf'


def get_content_type_from_bytes(file_bytes: bytes) -> str:
    """
    Get MIME type based on file content.

    Args:
        file_bytes: File content bytes

    Returns:
        MIME type string
    """
    file_type = detect_file_type(file_bytes)

    mime_types = {
        'zip': 'application/zip',
        'pdf': 'application/pdf',
        'json': 'application/json',
        'unknown': 'application/octet-stream'
    }

    return mime_types.get(file_type, 'application/octet-stream')
