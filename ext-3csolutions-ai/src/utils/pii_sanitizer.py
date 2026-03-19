"""
PII Sanitization for Logging and Data Storage
Prevents sensitive information leakage in logs and DynamoDB
"""

import re
from typing import Optional, Tuple, List
from .logger import get_logger

logger = get_logger(__name__)


# PII Patterns (regex)
# Note: Using (?<!\d) and (?<!\w) instead of \b to handle cases like "SSN_123-45-6789"
# where PII appears after underscores or other word characters
SSN_PATTERN = re.compile(r'(?<!\d)\d{3}[-\s]?\d{2}[-\s]?\d{4}(?!\d)')
CREDIT_CARD_PATTERN = re.compile(r'(?<!\d)\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}(?!\d)')
LICENSE_PATTERN = re.compile(r'(?<![A-Z])[A-Z]{1,2}\d{6,8}(?!\d)')
EMAIL_PATTERN = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}')
PHONE_PATTERN = re.compile(r'(?<!\d)(?:\+1[-\s]?)?(?:\(\d{3}\)|\d{3})[-\s]?\d{3}[-\s]?\d{4}(?!\d)')

# AWS Credentials
AWS_ACCESS_KEY_PATTERN = re.compile(r'(?<![A-Z0-9])(AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])')
AWS_SECRET_KEY_PATTERN = re.compile(r'(?i)(aws_secret_access_key|aws_secret_key|secret_key)\s*[=:]\s*["\']?([A-Za-z0-9/+=]{40})')

# Common sensitive field names
# NOTE: email, phone, mobile are NOT included - clients need contact information
SENSITIVE_FIELDS = {
    'password', 'secret', 'token', 'api_key', 'apikey', 'private_key',
    'ssn', 'social_security', 'credit_card', 'card_number', 'cvv', 'pin',
    'license', 'passport', 'tax_id'
}


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to remove PII patterns.

    Common real estate filename PII:
    - "John_Smith_SSN_123-45-6789.pdf" -> "John_Smith_SSN_***-**-****.pdf"
    - "Jane_Doe_License_DL12345678.pdf" -> "Jane_Doe_License_LICENSE_REDACTED.pdf"

    NOTE: Emails and phone numbers are NOT redacted - clients need contact information.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename with sensitive PII redacted (SSN, credit cards, licenses)
    """
    if not filename:
        return filename

    sanitized = filename
    pii_found = []

    # Redact SSN
    if SSN_PATTERN.search(sanitized):
        pii_found.append('SSN')
        sanitized = SSN_PATTERN.sub('***-**-****', sanitized)

    # Redact credit cards
    if CREDIT_CARD_PATTERN.search(sanitized):
        pii_found.append('Credit Card')
        sanitized = CREDIT_CARD_PATTERN.sub('****-****-****-****', sanitized)

    # Redact license numbers
    if LICENSE_PATTERN.search(sanitized):
        pii_found.append('License Number')
        sanitized = LICENSE_PATTERN.sub('LICENSE_REDACTED', sanitized)

    # Log PII detection and sanitization
    if pii_found:
        logger.warning(
            f"PII detected and sanitized in filename",
            extra={
                'pii_types': ', '.join(pii_found),
                'sanitized': sanitized != filename,
                'security_event': 'PII_SANITIZED'
            }
        )

    return sanitized


def sanitize_text(text: str, max_length: Optional[int] = None) -> str:
    """
    Sanitize arbitrary text to remove PII.

    NOTE: Emails and phone numbers are NOT redacted - clients need contact information.

    Args:
        text: Text to sanitize
        max_length: Optional max length (for truncation)

    Returns:
        Sanitized text with sensitive PII redacted (SSN, credit cards, licenses, AWS keys)
    """
    if not text:
        return text

    sanitized = text
    pii_found = []

    # Redact sensitive PII patterns
    if SSN_PATTERN.search(sanitized):
        pii_found.append('SSN')
        sanitized = SSN_PATTERN.sub('***-**-****', sanitized)

    if CREDIT_CARD_PATTERN.search(sanitized):
        pii_found.append('Credit Card')
        sanitized = CREDIT_CARD_PATTERN.sub('****-****-****-****', sanitized)

    if LICENSE_PATTERN.search(sanitized):
        pii_found.append('License')
        sanitized = LICENSE_PATTERN.sub('LICENSE_***', sanitized)

    # Redact AWS credentials
    if AWS_ACCESS_KEY_PATTERN.search(sanitized):
        pii_found.append('AWS Access Key')
        sanitized = AWS_ACCESS_KEY_PATTERN.sub('AKIA****************', sanitized)

    if AWS_SECRET_KEY_PATTERN.search(sanitized):
        pii_found.append('AWS Secret Key')
        sanitized = AWS_SECRET_KEY_PATTERN.sub(r'\1=********', sanitized)

    # Log critical security event if AWS credentials detected
    if any(key in pii_found for key in ['AWS Access Key', 'AWS Secret Key']):
        logger.critical(
            "AWS credentials detected and redacted in text",
            extra={
                'pii_types': ', '.join(pii_found),
                'security_event': 'AWS_CREDENTIALS_REDACTED',
                'severity': 'CRITICAL'
            }
        )
    elif pii_found:
        logger.warning(
            f"PII detected and sanitized in text",
            extra={
                'pii_types': ', '.join(pii_found),
                'security_event': 'PII_SANITIZED'
            }
        )

    # Truncate if needed
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + '...'

    return sanitized


def sanitize_dict(data: dict, recursive: bool = True) -> dict:
    """
    Sanitize dictionary values to remove PII from sensitive fields.

    Redacts values for keys matching SENSITIVE_FIELDS.

    Args:
        data: Dictionary to sanitize
        recursive: Whether to recursively sanitize nested dicts

    Returns:
        Sanitized dictionary
    """
    if not isinstance(data, dict):
        return data

    sanitized = {}
    for key, value in data.items():
        key_lower = key.lower()

        # Check if field is sensitive
        is_sensitive = any(sensitive in key_lower for sensitive in SENSITIVE_FIELDS)

        if is_sensitive:
            # Redact sensitive field
            if isinstance(value, str):
                sanitized[key] = '***REDACTED***'
            elif isinstance(value, (int, float)):
                sanitized[key] = 0
            else:
                sanitized[key] = None
        elif recursive and isinstance(value, dict):
            # Recursively sanitize nested dict
            sanitized[key] = sanitize_dict(value, recursive=True)
        elif recursive and isinstance(value, list):
            # Sanitize list items
            sanitized[key] = [
                sanitize_dict(item, recursive=True) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, str):
            # Sanitize string values (for PII patterns)
            sanitized[key] = sanitize_text(value)
        else:
            # Keep other values as-is
            sanitized[key] = value

    return sanitized


def sanitize_for_logging(filename: str, include_hash: bool = True) -> str:
    """
    Sanitize filename for safe logging.

    Args:
        filename: Original filename
        include_hash: Whether to include filename hash for tracking

    Returns:
        Safe filename for logging
    """
    sanitized = sanitize_filename(filename)

    if include_hash:
        # Add hash for tracking without exposing original name
        import hashlib
        file_hash = hashlib.sha256(filename.encode()).hexdigest()[:8]
        return f"{sanitized} [hash:{file_hash}]"

    return sanitized


def is_pii_detected(text: str) -> Tuple[bool, List[str]]:
    """
    Check if text contains sensitive PII patterns.

    NOTE: Emails and phone numbers are NOT considered PII - clients need contact information.

    Args:
        text: Text to check

    Returns:
        Tuple of (has_sensitive_pii, list_of_detected_patterns)
    """
    if not text:
        return (False, [])

    detected = []

    if SSN_PATTERN.search(text):
        detected.append('SSN')

    if CREDIT_CARD_PATTERN.search(text):
        detected.append('Credit Card')

    if LICENSE_PATTERN.search(text):
        detected.append('License Number')

    # Emails and phone numbers are NOT flagged as PII

    if AWS_ACCESS_KEY_PATTERN.search(text):
        detected.append('AWS Access Key')

    if AWS_SECRET_KEY_PATTERN.search(text):
        detected.append('AWS Secret Key')

    return (len(detected) > 0, detected)


def sanitize_stack_trace(stack_trace: str) -> str:
    """
    Sanitize stack trace to remove sensitive information.

    Args:
        stack_trace: Raw stack trace string

    Returns:
        Sanitized stack trace
    """
    if not stack_trace:
        return stack_trace

    sanitized = stack_trace

    # Redact environment variables
    sanitized = re.sub(
        r'(AWS_SECRET_ACCESS_KEY|SECRET_KEY|API_KEY|PASSWORD)\s*=\s*[\'"]?[^\s\'"]+',
        r'\1=***REDACTED***',
        sanitized,
        flags=re.IGNORECASE
    )

    # Redact file paths containing usernames
    sanitized = re.sub(r'/home/[^/\s]+/', '/home/***/', sanitized)
    sanitized = re.sub(r'C:\\Users\\[^\\]+\\', r'C:\\Users\\***\\', sanitized)

    # Redact AWS credentials in URLs
    sanitized = AWS_ACCESS_KEY_PATTERN.sub('AKIA****************', sanitized)

    return sanitized
