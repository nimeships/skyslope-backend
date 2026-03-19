"""
Security Utilities for Document Extraction Pipeline
Provides file sanitization, size limits, and security constraints
"""

import os
import re
import unicodedata
import xml.sax.saxutils as saxutils
from typing import Dict, Tuple
from .exceptions import FileSizeLimitError, SecurityError
from .logger import get_logger

logger = get_logger(__name__)


# Security Constants - File Size Limits (in bytes)
MAX_ZIP_SIZE = 500 * 1024 * 1024  # 500MB - Maximum compressed ZIP file size
MAX_EXTRACTED_TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 2GB - Total extracted content limit
MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024  # 100MB - Maximum single file size
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50MB - Maximum PDF file size

# ZIP Bomb Protection
MAX_COMPRESSION_RATIO = 1000  # Maximum allowed compression ratio (1000:1)
# Example: 1MB compressed → 1000MB uncompressed is OK
#          1MB compressed → 1001MB uncompressed is BLOCKED (possible ZIP bomb)

# File Count Limits
MAX_FILES_IN_ZIP = 500  # Maximum number of files allowed in a ZIP
MAX_PDFS_TO_PROCESS = 200  # Maximum number of PDFs to process per job

# String Length Limits
MAX_FILENAME_LENGTH = 255
MAX_TEXT_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB text content limit


class SecurityLimits:
    """Centralized security configuration"""

    MAX_ZIP_SIZE = MAX_ZIP_SIZE
    MAX_EXTRACTED_TOTAL_SIZE = MAX_EXTRACTED_TOTAL_SIZE
    MAX_SINGLE_FILE_SIZE = MAX_SINGLE_FILE_SIZE
    MAX_PDF_SIZE = MAX_PDF_SIZE
    MAX_COMPRESSION_RATIO = MAX_COMPRESSION_RATIO
    MAX_FILES_IN_ZIP = MAX_FILES_IN_ZIP
    MAX_PDFS_TO_PROCESS = MAX_PDFS_TO_PROCESS
    MAX_FILENAME_LENGTH = MAX_FILENAME_LENGTH
    MAX_TEXT_CONTENT_LENGTH = MAX_TEXT_CONTENT_LENGTH


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent security issues.

    Protections:
    - Unicode normalization (prevent homograph attacks)
    - Path traversal prevention
    - Remove dangerous characters
    - Prevent hidden files
    - Length limiting

    Args:
        filename: Original filename from ZIP or user input

    Returns:
        Sanitized filename safe for storage

    Raises:
        SecurityError: If filename cannot be safely sanitized
    """
    if not filename or not isinstance(filename, str):
        logger.error("Security: Invalid filename type", extra={'security_event': 'INVALID_FILENAME_TYPE'})
        raise SecurityError("Filename cannot be empty or non-string")

    original_filename = filename
    security_issues = []

    # Step 1: Normalize unicode to prevent homograph attacks
    filename = unicodedata.normalize('NFKC', filename)

    # Step 2: Remove null bytes and control characters
    has_control_chars = any(ord(char) < 32 for char in filename)
    if has_control_chars:
        security_issues.append('control_characters')
    filename = ''.join(char for char in filename if ord(char) >= 32)

    # Step 3: Prevent hidden files (do this BEFORE path separator replacement)
    if filename.startswith('.'):
        security_issues.append('hidden_file')
        filename = '_' + filename[1:]

    # Step 4: Replace path separators with underscores
    if '/' in filename or '\\' in filename:
        security_issues.append('path_traversal_attempt')
    filename = filename.replace('/', '_').replace('\\', '_')

    # Step 5: Whitelist allowed characters (alphanumeric, dash, underscore, dot)
    before = filename
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    if before != filename:
        security_issues.append('dangerous_characters')

    # Step 6: Prevent files with only dots or underscores
    if set(filename) <= {'.', '_'}:
        logger.error("Security: Invalid filename after sanitization", extra={
            'original_filename': original_filename,
            'security_event': 'INVALID_FILENAME_POST_SANITIZATION'
        })
        raise SecurityError(f"Invalid filename after sanitization: {filename}")

    # Step 7: Limit length
    if len(filename) > MAX_FILENAME_LENGTH:
        security_issues.append('filename_too_long')
        # Keep extension, truncate name
        name, ext = os.path.splitext(filename)
        max_name_len = MAX_FILENAME_LENGTH - len(ext)
        filename = name[:max_name_len] + ext

    # Step 8: Ensure filename is not empty after sanitization
    if not filename or filename == '_':
        logger.error("Security: Empty filename after sanitization", extra={
            'original_filename': original_filename,
            'security_event': 'EMPTY_FILENAME_POST_SANITIZATION'
        })
        raise SecurityError("Filename is empty after sanitization")

    # Log security issues detected
    if security_issues:
        logger.warning(
            f"Security: Filename sanitized",
            extra={
                'security_issues': ', '.join(security_issues),
                'sanitized': original_filename != filename,
                'security_event': 'FILENAME_SANITIZED'
            }
        )

    return filename


def validate_zip_size(zip_bytes: bytes) -> None:
    """
    Validate ZIP file size is within limits.

    Args:
        zip_bytes: ZIP file content as bytes

    Raises:
        FileSizeLimitError: If ZIP exceeds size limit
    """
    zip_size = len(zip_bytes)
    zip_size_mb = zip_size / (1024 * 1024)

    logger.info(
        f"Security: ZIP size check",
        extra={
            'zip_size_mb': f"{zip_size_mb:.2f}",
            'max_size_mb': MAX_ZIP_SIZE // (1024*1024),
            'security_check': 'ZIP_SIZE_VALIDATION'
        }
    )

    if zip_size > MAX_ZIP_SIZE:
        logger.error(
            f"Security: ZIP size limit exceeded",
            extra={
                'zip_size_mb': f"{zip_size_mb:.2f}",
                'max_size_mb': MAX_ZIP_SIZE // (1024*1024),
                'security_event': 'ZIP_SIZE_LIMIT_EXCEEDED'
            }
        )
        raise FileSizeLimitError(
            f"ZIP file size ({zip_size:,} bytes) exceeds maximum allowed size "
            f"({MAX_ZIP_SIZE:,} bytes / {MAX_ZIP_SIZE // (1024*1024)}MB)"
        )


def validate_compression_ratio(
    compressed_size: int,
    uncompressed_size: int,
    filename: str = "file"
) -> None:
    """
    Validate compression ratio to detect ZIP bombs.

    ZIP BOMB ATTACK EXAMPLE:
    - 42.zip: 42KB compressed → 4.5PB (petabytes) uncompressed
    - Compression ratio: 4,500,000,000,000 KB / 42 KB = 107,142,857,142:1

    This function prevents such attacks by enforcing a maximum compression ratio.

    Args:
        compressed_size: Original compressed file size (bytes)
        uncompressed_size: Extracted/uncompressed file size (bytes)
        filename: Filename for error message

    Raises:
        SecurityError: If compression ratio exceeds safe threshold
    """
    if compressed_size == 0:
        # Avoid division by zero
        if uncompressed_size > MAX_SINGLE_FILE_SIZE:
            raise SecurityError(
                f"File '{filename}' has suspicious size: {uncompressed_size:,} bytes "
                f"with 0 bytes compressed size"
            )
        return

    compression_ratio = uncompressed_size / compressed_size

    logger.info(
        f"Security: Compression ratio check",
        extra={
            'file_name': filename,
            'compression_ratio': f"{compression_ratio:.1f}:1",
            'compressed_mb': f"{compressed_size / (1024*1024):.2f}",
            'uncompressed_mb': f"{uncompressed_size / (1024*1024):.2f}",
            'security_check': 'COMPRESSION_RATIO_VALIDATION'
        }
    )

    if compression_ratio > MAX_COMPRESSION_RATIO:
        logger.critical(
            f"Security: ZIP BOMB DETECTED",
            extra={
                'file_name': filename,
                'compression_ratio': f"{compression_ratio:.1f}:1",
                'compressed_mb': f"{compressed_size / (1024*1024):.2f}",
                'uncompressed_mb': f"{uncompressed_size / (1024*1024):.2f}",
                'max_ratio': f"{MAX_COMPRESSION_RATIO}:1",
                'security_event': 'ZIP_BOMB_DETECTED',
                'severity': 'CRITICAL'
            }
        )
        raise SecurityError(
            f"⚠️  ZIP BOMB DETECTED: File '{filename}' has suspicious compression ratio\n"
            f"   Compressed: {compressed_size:,} bytes ({compressed_size / (1024*1024):.2f} MB)\n"
            f"   Uncompressed: {uncompressed_size:,} bytes ({uncompressed_size / (1024*1024):.2f} MB)\n"
            f"   Ratio: {compression_ratio:,.0f}:1 (maximum allowed: {MAX_COMPRESSION_RATIO}:1)\n"
            f"   This is likely a ZIP bomb attack (e.g., 42.zip, 42.tar.bz2)\n"
            f"   Extraction blocked for security."
        )


def validate_file_size(file_bytes: bytes, max_size: int, file_type: str = "file") -> None:
    """
    Validate file size is within limits.

    Args:
        file_bytes: File content as bytes
        max_size: Maximum allowed size in bytes
        file_type: Type of file for error message

    Raises:
        FileSizeLimitError: If file exceeds size limit
    """
    file_size = len(file_bytes)
    if file_size > max_size:
        raise FileSizeLimitError(
            f"{file_type} size ({file_size:,} bytes) exceeds maximum allowed size "
            f"({max_size:,} bytes / {max_size // (1024*1024)}MB)"
        )


def validate_extracted_size(
    file_info,
    total_extracted: int,
    zip_compressed_size: int = None
) -> int:
    """
    Validate extracted file size against limits (for ZIP extraction).

    SECURITY FIX: Now checks compression ratio to detect ZIP bombs.

    Args:
        file_info: ZipInfo object containing file metadata
        total_extracted: Running total of extracted bytes
        zip_compressed_size: Original ZIP file size (for compression ratio check)

    Returns:
        Updated total_extracted size

    Raises:
        FileSizeLimitError: If file or total size exceeds limits
        SecurityError: If compression ratio indicates ZIP bomb
    """
    file_size = file_info.file_size
    compressed_size = file_info.compress_size

    # Check single file limit
    if file_size > MAX_SINGLE_FILE_SIZE:
        raise FileSizeLimitError(
            f"File '{file_info.filename}' ({file_size:,} bytes) exceeds "
            f"maximum single file size ({MAX_SINGLE_FILE_SIZE:,} bytes / "
            f"{MAX_SINGLE_FILE_SIZE // (1024*1024)}MB)"
        )

    # SECURITY: Check compression ratio for this file (ZIP bomb detection)
    validate_compression_ratio(
        compressed_size,
        file_size,
        file_info.filename
    )

    # Check total extraction limit
    total_extracted += file_size
    if total_extracted > MAX_EXTRACTED_TOTAL_SIZE:
        raise FileSizeLimitError(
            f"Total extracted size ({total_extracted:,} bytes) exceeds "
            f"maximum allowed size ({MAX_EXTRACTED_TOTAL_SIZE:,} bytes / "
            f"{MAX_EXTRACTED_TOTAL_SIZE // (1024*1024)}MB). "
            f"Possible zip bomb attack."
        )

    # SECURITY: Also check OVERALL compression ratio (ZIP within ZIP protection)
    if zip_compressed_size and zip_compressed_size > 0:
        overall_ratio = total_extracted / zip_compressed_size
        if overall_ratio > MAX_COMPRESSION_RATIO:
            raise SecurityError(
                f"⚠️  ZIP BOMB DETECTED: Overall compression ratio too high\n"
                f"   ZIP size: {zip_compressed_size:,} bytes\n"
                f"   Total extracted so far: {total_extracted:,} bytes\n"
                f"   Ratio: {overall_ratio:,.0f}:1 (maximum: {MAX_COMPRESSION_RATIO}:1)\n"
                f"   Extraction halted at file: {file_info.filename}"
            )

    return total_extracted


def escape_xml_content(text: str) -> str:
    """
    Escape XML special characters to prevent prompt injection.

    DEPRECATED: Use wrap_in_cdata() instead for better security.

    Used when embedding user-generated content into XML-based prompts.

    Args:
        text: Raw text content

    Returns:
        XML-escaped text safe for embedding in prompts
    """
    if not text:
        return ""

    return saxutils.escape(text, {
        '"': '&quot;',
        "'": '&apos;',
        '<': '&lt;',
        '>': '&gt;',
        '&': '&amp;'
    })


def wrap_in_cdata(text: str, tag_name: str = "DOCUMENT-TEXT") -> str:
    """
    Wrap user-generated text in CDATA section for prompt injection protection.

    SECURITY: CDATA (Character Data) sections tell the XML parser to treat
    content as raw text, preventing ANY interpretation of special characters.
    This is MUCH more secure than XML escaping.

    Protection against:
    - Prompt injection attempts (e.g., "IGNORE ALL PREVIOUS INSTRUCTIONS")
    - XML tag injection (e.g., "</DOCUMENT-TEXT><MALICIOUS>")
    - Entity injection (e.g., "&malicious;")

    Example Output:
    <DOCUMENT-TEXT><![CDATA[
    ... user text here (not interpreted) ...
    ]]></DOCUMENT-TEXT>

    Args:
        text: Raw user-generated text (document content)
        tag_name: XML tag name to wrap content

    Returns:
        XML string with CDATA-wrapped content

    Note:
        If text contains "]]>", it's escaped to "]]]]><![CDATA[>" to prevent
        CDATA escape attacks.
    """
    if not text:
        return f"<{tag_name}></{tag_name}>"

    # SECURITY: Escape CDATA end marker if present in text
    # This prevents the attack: user_text = "]]><INJECT>malicious</INJECT><![CDATA["
    # By replacing ]]> with ]]]]><![CDATA[>, we close and reopen CDATA safely
    safe_text = text.replace("]]>", "]]]]><![CDATA[>")

    return f"<{tag_name}><![CDATA[{safe_text}]]></{tag_name}>"


def detect_prompt_injection(text: str) -> Tuple[bool, str]:
    """
    Detect common prompt injection patterns in user-generated text.

    This is a heuristic check for suspicious content that attempts to
    manipulate LLM behavior. NOT foolproof, but catches obvious attacks.

    Common patterns:
    - "IGNORE PREVIOUS INSTRUCTIONS"
    - "SYSTEM:" (trying to inject system messages)
    - "ASSISTANT:" (trying to inject assistant messages)
    - Tag injection attempts

    Args:
        text: User-generated text to check

    Returns:
        Tuple of (is_suspicious, reason)
        - is_suspicious: True if injection pattern detected
        - reason: Description of detected pattern (empty if clean)

    Example:
        >>> detect_prompt_injection("Please process this document")
        (False, "")
        >>> detect_prompt_injection("IGNORE ALL PREVIOUS INSTRUCTIONS")
        (True, "Detected instruction override attempt")
    """
    if not text or len(text) < 10:
        return (False, "")

    text_upper = text.upper()

    # Pattern 1: Instruction override attempts
    override_patterns = [
        "IGNORE ALL PREVIOUS",
        "IGNORE PREVIOUS INSTRUCTIONS",
        "DISREGARD ALL PREVIOUS",
        "FORGET ALL PREVIOUS",
        "OVERRIDE INSTRUCTIONS"
    ]
    for pattern in override_patterns:
        if pattern in text_upper:
            return (True, f"Detected instruction override attempt: '{pattern}'")

    # Pattern 2: Role injection attempts
    role_patterns = [
        "SYSTEM:",
        "ASSISTANT:",
        "USER:",
        "<SYSTEM>",
        "</SYSTEM>",
        "<ASSISTANT>",
        "</ASSISTANT>"
    ]
    for pattern in role_patterns:
        if pattern in text_upper:
            return (True, f"Detected role injection attempt: '{pattern}'")

    # Pattern 3: Tag injection attempts (closing important tags)
    tag_patterns = [
        "</DOCUMENT-TEXT>",
        "</SYSTEM_INSTRUCTIONS>",
        "</SCHEMA-CONSTRAINTS>",
        "</FIELD-MAPPING-GUIDE>"
    ]
    for pattern in tag_patterns:
        if pattern in text_upper:
            return (True, f"Detected tag injection attempt: '{pattern}'")

    # Pattern 4: Suspicious JSON-like override
    if '"role":' in text.lower() and '"content":' in text.lower():
        return (True, "Detected JSON message injection attempt")

    return (False, "")


def get_safe_log_data(data: dict, safe_fields: set) -> dict:
    """
    Filter dictionary to only include safe-to-log fields.

    Prevents PII leakage in logs.

    Args:
        data: Original data dictionary
        safe_fields: Set of field names safe to log

    Returns:
        Filtered dictionary with only safe fields
    """
    if not isinstance(data, dict):
        return {}

    return {k: v for k, v in data.items() if k in safe_fields}


# Safe fields for logging (no PII)
SAFE_LOG_FIELDS = {
    'request_id',
    'stage',
    'status',
    'progress',
    'total',
    'message',
    'last_updated',
    'file_count',
    'success_count',
    'error_count',
    'start_time',
    'end_time',
    'duration_seconds'
}


def safe_log_dict(data: dict) -> dict:
    """
    Get dictionary safe for logging (wrapper for get_safe_log_data).

    Args:
        data: Dictionary potentially containing PII

    Returns:
        Dictionary with only safe fields
    """
    return get_safe_log_data(data, SAFE_LOG_FIELDS)
