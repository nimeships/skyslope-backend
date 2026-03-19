"""
Unit tests for S3Helper utility.

Tests cover:
- Filename sanitization (security)
- Path traversal prevention
- Dangerous character filtering
- ZIP extension auto-add
- Unique key generation
"""
import pytest
from src.utils.s3helper import S3Helper


class TestFilenameSanitization:
    """Test filename sanitization logic"""

    def test_sanitize_removes_path_separators(self, mock_logger, mock_env):
        """Should remove forward and backslashes"""
        helper = S3Helper(mock_logger, mock_env)

        assert ".." not in helper.sanitize_filename("../../../etc/passwd")
        assert "/" not in helper.sanitize_filename("path/to/file")
        assert "\\" not in helper.sanitize_filename("path\\to\\file")

    def test_sanitize_removes_dangerous_characters(self, mock_logger, mock_env):
        """Should remove characters that could cause issues"""
        helper = S3Helper(mock_logger, mock_env)

        # Dangerous characters should be replaced with underscore
        result = helper.sanitize_filename("file<>:\"?*|.txt")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_sanitize_preserves_safe_characters(self, mock_logger, mock_env):
        """Should keep letters, numbers, dots, dashes, underscores"""
        helper = S3Helper(mock_logger, mock_env)

        safe_filename = "My-Document_v2.1.pdf"
        result = helper.sanitize_filename(safe_filename)
        assert result == safe_filename

    def test_sanitize_adds_zip_extension_for_zip_content_type(self, mock_logger, mock_env):
        """Should add .zip extension when content_type is application/zip"""
        helper = S3Helper(mock_logger, mock_env)

        result = helper.sanitize_filename("documents", content_type="application/zip")
        assert result == "documents.zip"

    def test_sanitize_does_not_duplicate_zip_extension(self, mock_logger, mock_env):
        """Should not add .zip if already present"""
        helper = S3Helper(mock_logger, mock_env)

        result = helper.sanitize_filename("documents.zip", content_type="application/zip")
        assert result == "documents.zip"
        assert result.count(".zip") == 1

    def test_sanitize_raises_on_empty_filename(self, mock_logger, mock_env):
        """Should raise error if filename is empty after sanitization"""
        helper = S3Helper(mock_logger, mock_env)

        with pytest.raises(ValueError, match="empty"):
            helper.sanitize_filename("")

    def test_sanitize_raises_on_only_invalid_characters(self, mock_logger, mock_env):
        """Should raise error if only invalid chars remain"""
        helper = S3Helper(mock_logger, mock_env)

        with pytest.raises(ValueError):
            helper.sanitize_filename("<>?*|")


class TestUniqueKeyGeneration:
    """Test S3 key generation"""

    def test_build_unique_key_format(self, mock_logger, mock_env):
        """Should generate key in format: input/{uuid}_{timestamp}/{filename}"""
        helper = S3Helper(mock_logger, mock_env)

        key = helper.build_unique_key("document.pdf", "application/pdf")

        # Should start with input/
        assert key.startswith("input/")

        # Should contain UUID (32 hex chars) + underscore + timestamp
        parts = key.split("/")
        assert len(parts) == 3
        assert parts[0] == "input"

        # Second part: {uuid}_{timestamp}
        uuid_timestamp = parts[1]
        assert "_" in uuid_timestamp
        uuid_part, timestamp_part = uuid_timestamp.split("_", 1)
        assert len(uuid_part) == 32  # UUID4 hex
        assert "T" in timestamp_part  # ISO timestamp
        assert timestamp_part.endswith("Z")  # UTC timezone

        # Third part: sanitized filename
        assert parts[2] == "document.pdf"

    def test_build_unique_key_adds_zip_extension(self, mock_logger, mock_env):
        """Should add .zip extension for ZIP files"""
        helper = S3Helper(mock_logger, mock_env)

        key = helper.build_unique_key("archive", "application/zip")

        # Filename should have .zip extension
        assert key.endswith("/archive.zip")

    def test_build_unique_key_uniqueness(self, mock_logger, mock_env):
        """Should generate unique keys for same filename"""
        helper = S3Helper(mock_logger, mock_env)

        key1 = helper.build_unique_key("file.pdf", "application/pdf")
        key2 = helper.build_unique_key("file.pdf", "application/pdf")

        # Keys should be different (different UUID and timestamp)
        assert key1 != key2


class TestSecurityValidation:
    """Test security aspects of filename handling"""

    def test_blocks_path_traversal_attempts(self, mock_logger, mock_env):
        """Should sanitize path traversal attempts"""
        helper = S3Helper(mock_logger, mock_env)

        malicious_filenames = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "../../../../root/.ssh/id_rsa"
        ]

        for filename in malicious_filenames:
            result = helper.sanitize_filename(filename)
            assert ".." not in result
            assert "/" not in result
            assert "\\" not in result

    def test_blocks_null_bytes(self, mock_logger, mock_env):
        """Should remove null bytes"""
        helper = S3Helper(mock_logger, mock_env)

        result = helper.sanitize_filename("file\x00.pdf")
        assert "\x00" not in result

    def test_blocks_newlines_and_control_chars(self, mock_logger, mock_env):
        """Should remove control characters"""
        helper = S3Helper(mock_logger, mock_env)

        result = helper.sanitize_filename("file\n\r\t.pdf")
        assert "\n" not in result
        assert "\r" not in result
