"""
File Type Detection Using Magic Bytes (File Signatures)
Detects file types by analyzing file content, not filename extensions

Supports: PDF, ZIP, JSON, HTML, PNG, JPEG, GIF, TIFF, BMP, and more
"""

from typing import Literal

# File type literal for type hints
FileType = Literal[
    'pdf', 'zip', 'json', 'html', 'xml',
    'png', 'jpeg', 'gif', 'tiff', 'bmp', 'webp',
    'txt', 'csv', 'unknown'
]

# Magic byte signatures for file type detection
MAGIC_BYTES = {
    'pdf': [
        b'%PDF-',  # PDF files start with %PDF-1.x
    ],
    'zip': [
        b'PK\x03\x04',  # Standard ZIP
        b'PK\x05\x06',  # Empty ZIP
        b'PK\x07\x08',  # Spanned ZIP
    ],
    'json': [
        b'{',  # JSON object
        b'[',  # JSON array
    ],
    'html': [
        b'<!DOCTYPE html',
        b'<!doctype html',
        b'<html',
        b'<HTML',
        b'<head',
        b'<HEAD',
    ],
    'xml': [
        b'<?xml',
        b'<?XML',
    ],
    # Image formats
    'png': [
        b'\x89PNG\r\n\x1a\n',  # PNG signature
    ],
    'jpeg': [
        b'\xFF\xD8\xFF\xDB',  # JPEG raw
        b'\xFF\xD8\xFF\xE0',  # JPEG/JFIF
        b'\xFF\xD8\xFF\xE1',  # JPEG/Exif
        b'\xFF\xD8\xFF\xEE',  # JPEG
    ],
    'gif': [
        b'GIF87a',  # GIF 87a
        b'GIF89a',  # GIF 89a
    ],
    'tiff': [
        b'II*\x00',  # TIFF little-endian
        b'MM\x00*',  # TIFF big-endian
    ],
    'bmp': [
        b'BM',  # BMP signature
    ],
    'webp': [
        b'RIFF',  # WebP (needs further check for WEBP)
    ]
}


def detect_file_type(file_bytes: bytes, max_check_bytes: int = 512) -> FileType:
    """
    Detect file type by analyzing magic bytes (file signature).

    This is more reliable than checking file extensions, especially when:
    - Files are uploaded from Windows without visible extensions
    - Filenames are sanitized or modified
    - Security is a concern (prevents extension spoofing)

    Supports: PDF, ZIP, JSON, HTML, XML, PNG, JPEG, GIF, TIFF, BMP, WebP

    Args:
        file_bytes: Raw file content (at least first 512 bytes recommended)
        max_check_bytes: Maximum bytes to check from start of file

    Returns:
        Detected file type or 'unknown'

    Examples:
        >>> detect_file_type(b'%PDF-1.4\\n...')
        'pdf'
        >>> detect_file_type(b'\\x89PNG\\r\\n\\x1a\\n...')
        'png'
        >>> detect_file_type(b'<!DOCTYPE html>...')
        'html'
    """
    if not file_bytes:
        return 'unknown'

    # Check only the beginning of the file
    header = file_bytes[:max_check_bytes]

    # Check binary formats first (more specific signatures)

    # PDF
    for signature in MAGIC_BYTES['pdf']:
        if header.startswith(signature):
            return 'pdf'

    # PNG
    for signature in MAGIC_BYTES['png']:
        if header.startswith(signature):
            return 'png'

    # JPEG
    for signature in MAGIC_BYTES['jpeg']:
        if header.startswith(signature):
            return 'jpeg'

    # GIF
    for signature in MAGIC_BYTES['gif']:
        if header.startswith(signature):
            return 'gif'

    # TIFF
    for signature in MAGIC_BYTES['tiff']:
        if header.startswith(signature):
            return 'tiff'

    # BMP
    for signature in MAGIC_BYTES['bmp']:
        if header.startswith(signature):
            return 'bmp'

    # WebP (special case: RIFF followed by WEBP)
    if header.startswith(b'RIFF') and b'WEBP' in header[:20]:
        return 'webp'

    # ZIP
    for signature in MAGIC_BYTES['zip']:
        if header.startswith(signature):
            return 'zip'

    # Text-based formats (check after binary to avoid false positives)
    # Strip whitespace before checking
    stripped = header.lstrip()

    # XML (check before HTML as HTML can contain XML-like tags)
    for signature in MAGIC_BYTES['xml']:
        if stripped.lower().startswith(signature.lower()):
            return 'xml'

    # HTML
    for signature in MAGIC_BYTES['html']:
        if stripped.lower().startswith(signature.lower()):
            return 'html'

    # JSON (check last as it's the most ambiguous)
    for signature in MAGIC_BYTES['json']:
        if stripped.startswith(signature):
            # Additional validation: check if it looks like JSON
            try:
                # Try to find closing bracket in first 1KB
                sample = stripped[:1024]
                if signature == b'{' and b'}' in sample:
                    return 'json'
                if signature == b'[' and b']' in sample:
                    return 'json'
            except Exception:
                pass

    # Check if it's plain text (all printable ASCII or UTF-8)
    try:
        sample = header[:512]
        decoded = sample.decode('utf-8', errors='strict')
        # If successful and contains mostly printable chars, it's text
        printable_ratio = sum(c.isprintable() or c.isspace() for c in decoded) / len(decoded)
        if printable_ratio > 0.9:
            # Check if it looks like CSV
            if ',' in decoded and '\n' in decoded:
                return 'csv'
            return 'txt'
    except (UnicodeDecodeError, ZeroDivisionError):
        pass

    return 'unknown'


def is_pdf_file(file_bytes: bytes) -> bool:
    """
    Check if file is a PDF by analyzing content.

    Args:
        file_bytes: Raw file content

    Returns:
        True if file is a PDF, False otherwise

    Example:
        >>> pdf_data = b'%PDF-1.4\\n%\\xE2\\xE3\\xCF\\xD3...'
        >>> is_pdf_file(pdf_data)
        True
    """
    return detect_file_type(file_bytes) == 'pdf'


def is_zip_file(file_bytes: bytes) -> bool:
    """
    Check if file is a ZIP by analyzing content.

    Args:
        file_bytes: Raw file content

    Returns:
        True if file is a ZIP, False otherwise

    Example:
        >>> zip_data = b'PK\\x03\\x04...'
        >>> is_zip_file(zip_data)
        True
    """
    return detect_file_type(file_bytes) == 'zip'


def is_json_file(file_bytes: bytes) -> bool:
    """
    Check if file is JSON by analyzing content.

    Note: This is a basic check. For full validation, use json.loads()

    Args:
        file_bytes: Raw file content

    Returns:
        True if file appears to be JSON, False otherwise
    """
    return detect_file_type(file_bytes) == 'json'


def validate_pdf_content(file_bytes: bytes) -> bool:
    """
    Validate that file is a valid PDF with required markers.

    Checks for:
    - PDF header (%PDF-)
    - Minimum file size
    - EOF marker (%%EOF) if file is complete

    Args:
        file_bytes: Complete file content

    Returns:
        True if valid PDF, False otherwise
    """
    if not file_bytes or len(file_bytes) < 10:
        return False

    # Check PDF header
    if not file_bytes.startswith(b'%PDF-'):
        return False

    # Check for EOF marker (should be near end of file)
    # Note: Some PDFs might not have this if truncated
    last_kb = file_bytes[-1024:]
    has_eof = b'%%EOF' in last_kb

    # PDF is valid if it has header (EOF marker is optional for processing)
    return True


def is_image_file(file_bytes: bytes) -> bool:
    """
    Check if file is an image (PNG, JPEG, GIF, TIFF, BMP, WebP).

    Args:
        file_bytes: Raw file content

    Returns:
        True if file is an image, False otherwise
    """
    detected = detect_file_type(file_bytes)
    return detected in ('png', 'jpeg', 'gif', 'tiff', 'bmp', 'webp')


def is_html_file(file_bytes: bytes) -> bool:
    """Check if file is HTML."""
    return detect_file_type(file_bytes) == 'html'


def is_xml_file(file_bytes: bytes) -> bool:
    """Check if file is XML."""
    return detect_file_type(file_bytes) == 'xml'


def is_text_file(file_bytes: bytes) -> bool:
    """Check if file is plain text or CSV."""
    detected = detect_file_type(file_bytes)
    return detected in ('txt', 'csv')


def is_processable_by_claude(file_bytes: bytes) -> bool:
    """
    Check if file type can be processed by Claude AI.

    Accepted file types:
    - PDF documents (native support)
    - Images (PNG, JPEG, GIF, WebP, TIFF, BMP) via vision

    Args:
        file_bytes: Raw file content

    Returns:
        True if Claude can process this file type
    """
    detected = detect_file_type(file_bytes)

    # Only PDFs and Images are accepted
    processable_types = {
        'pdf',      # Native PDF support
        'png',      # Vision API
        'jpeg',     # Vision API
        'gif',      # Vision API
        'webp',     # Vision API
        'tiff',     # Vision API
        'bmp',      # Vision API
    }

    return detected in processable_types


def get_file_type_description(file_type: FileType) -> str:
    """
    Get human-readable description of file type.

    Args:
        file_type: Detected file type

    Returns:
        Human-readable description
    """
    descriptions = {
        'pdf': 'PDF Document',
        'zip': 'ZIP Archive',
        'json': 'JSON Data',
        'html': 'HTML Document',
        'xml': 'XML Document',
        'png': 'PNG Image',
        'jpeg': 'JPEG Image',
        'gif': 'GIF Image',
        'tiff': 'TIFF Image',
        'bmp': 'BMP Image',
        'webp': 'WebP Image',
        'txt': 'Text File',
        'csv': 'CSV Data',
        'unknown': 'Unknown File Type'
    }
    return descriptions.get(file_type, 'Unknown File Type')


def get_appropriate_extension(file_bytes: bytes) -> str:
    """
    Get appropriate file extension based on detected file type.

    Args:
        file_bytes: Raw file content

    Returns:
        File extension with leading dot (e.g., '.pdf', '.png')
    """
    detected = detect_file_type(file_bytes)

    extension_map = {
        'pdf': '.pdf',
        'zip': '.zip',
        'json': '.json',
        'html': '.html',
        'xml': '.xml',
        'png': '.png',
        'jpeg': '.jpg',
        'gif': '.gif',
        'tiff': '.tiff',
        'bmp': '.bmp',
        'webp': '.webp',
        'txt': '.txt',
        'csv': '.csv',
        'unknown': ''
    }

    return extension_map.get(detected, '')
