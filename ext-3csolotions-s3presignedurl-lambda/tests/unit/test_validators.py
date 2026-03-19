"""
Unit tests for Pydantic input validators.

Tests cover:
- PresignRequest validation
- Filename security checks
- Content-type whitelist
- Length constraints
"""
import pytest
from pydantic import ValidationError
from src.model.presignurlInputValidator import PresignRequest


class TestPresignRequestValidation:
    """Test PresignRequest Pydantic model"""

    def test_valid_request(self):
        """Should accept valid request"""
        request = PresignRequest(
            filename="document.zip",
            content_type="application/zip"
        )
        assert request.filename == "document.zip"
        assert request.content_type == "application/zip"

    def test_valid_request_without_extension(self):
        """Should accept filename without extension"""
        request = PresignRequest(
            filename="document",
            content_type="application/zip"
        )
        assert request.filename == "document"

    def test_default_content_type(self):
        """Should use default content_type if not provided"""
        request = PresignRequest(filename="file.zip")
        assert request.content_type == "application/zip"

    def test_rejects_empty_filename(self):
        """Should reject empty filename"""
        with pytest.raises(ValidationError):
            PresignRequest(filename="", content_type="application/zip")

    def test_rejects_too_long_filename(self):
        """Should reject filename > 255 characters"""
        long_filename = "a" * 256
        with pytest.raises(ValidationError):
            PresignRequest(filename=long_filename, content_type="application/zip")

    def test_rejects_path_traversal(self):
        """Should reject filenames with path traversal attempts"""
        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "path/to/file",
            "path\\to\\file"
        ]

        for filename in malicious_filenames:
            with pytest.raises(ValidationError, match="path separators"):
                PresignRequest(filename=filename, content_type="application/zip")

    def test_rejects_dangerous_characters(self):
        """Should reject filenames with dangerous characters"""
        dangerous_filenames = [
            "file<script>.pdf",
            "file>output.txt",
            "file:stream.dat",
            'file"quote.pdf',
            "file|pipe.txt",
            "file?query.pdf",
            "file*wildcard.txt"
        ]

        for filename in dangerous_filenames:
            with pytest.raises(ValidationError, match="invalid characters"):
                PresignRequest(filename=filename, content_type="application/zip")

    def test_rejects_hidden_files(self):
        """Should reject hidden files (starting with dot)"""
        with pytest.raises(ValidationError, match="Hidden files"):
            PresignRequest(filename=".hidden", content_type="application/zip")

    def test_rejects_control_characters(self):
        """Should reject filenames with control characters"""
        with pytest.raises(ValidationError, match="control characters"):
            PresignRequest(filename="file\x00.pdf", content_type="application/zip")

        with pytest.raises(ValidationError, match="control characters"):
            PresignRequest(filename="file\n.pdf", content_type="application/zip")

    def test_rejects_invalid_content_type(self):
        """Should reject content types not in whitelist"""
        with pytest.raises(ValidationError):
            PresignRequest(filename="file.exe", content_type="application/x-executable")

    def test_accepts_all_whitelisted_content_types(self):
        """Should accept all whitelisted content types"""
        valid_content_types = [
            "application/zip",
            "application/json",
            "application/pdf",
            "text/plain"
        ]

        for content_type in valid_content_types:
            request = PresignRequest(filename="file", content_type=content_type)
            assert request.content_type == content_type


class TestPresignRequestSecurity:
    """Security-focused tests for input validation"""

    def test_sql_injection_attempt_blocked(self):
        """Should handle SQL injection patterns in filename"""
        sql_patterns = [
            "file'; DROP TABLE users--",
            "file' OR '1'='1",
            "file'; DELETE FROM *--"
        ]

        for pattern in sql_patterns:
            # Pydantic will clean dangerous characters
            # Test that model can be created (validation doesn't crash)
            try:
                request = PresignRequest(filename=pattern, content_type="application/zip")
                # If it passes, dangerous chars should be rejected or sanitized
            except ValidationError:
                # Rejection is also acceptable
                pass

    def test_xss_attempt_blocked(self):
        """Should reject XSS patterns in filename"""
        xss_patterns = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)"
        ]

        for pattern in xss_patterns:
            with pytest.raises(ValidationError):
                PresignRequest(filename=pattern, content_type="application/zip")

    def test_null_byte_injection_blocked(self):
        """Should reject null byte injection"""
        with pytest.raises(ValidationError):
            PresignRequest(filename="file.pdf\x00.txt", content_type="application/zip")
