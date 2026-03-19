"""
Unit tests for file type detection (magic bytes)
"""

import pytest
from src.utils.file_type_detector import (
    detect_file_type,
    is_pdf_file,
    is_zip_file,
    is_processable_by_claude,
    get_appropriate_extension,
    get_file_type_description
)


@pytest.mark.unit
class TestFileTypeDetection:
    """Test file type detection using magic bytes"""

    def test_detect_pdf_by_magic_bytes(self):
        """Test PDF detection via %PDF- signature"""
        pdf_data = b'%PDF-1.4\nrest of pdf...'
        assert detect_file_type(pdf_data) == 'pdf'

    def test_detect_zip_by_magic_bytes(self):
        """Test ZIP detection via PK signature"""
        zip_data = b'PK\x03\x04\x14\x00...'
        assert detect_file_type(zip_data) == 'zip'

    def test_detect_json_by_content(self):
        """Test JSON detection"""
        json_data = b'{"key": "value", "number": 123}'
        assert detect_file_type(json_data) == 'json'

    def test_detect_html_by_doctype(self):
        """Test HTML detection"""
        html_data = b'<!DOCTYPE html><html><body>Test</body></html>'
        assert detect_file_type(html_data) == 'html'

    def test_detect_png_by_magic_bytes(self):
        """Test PNG detection"""
        png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...'
        assert detect_file_type(png_data) == 'png'

    def test_detect_jpeg_by_magic_bytes(self):
        """Test JPEG detection"""
        jpeg_data = b'\xFF\xD8\xFF\xE0\x00\x10JFIF...'
        assert detect_file_type(jpeg_data) == 'jpeg'

    def test_detect_unknown_file_type(self):
        """Test unknown file type detection"""
        unknown_data = b'\x00\x01\x02\x03\x04\x05...'
        assert detect_file_type(unknown_data) == 'unknown'

    def test_empty_file_returns_unknown(self):
        """Test empty file returns unknown"""
        assert detect_file_type(b'') == 'unknown'


@pytest.mark.unit
class TestFileTypeHelpers:
    """Test helper functions"""

    def test_is_pdf_file(self):
        """Test is_pdf_file helper"""
        assert is_pdf_file(b'%PDF-1.4\n...') == True
        assert is_pdf_file(b'PK\x03\x04...') == False

    def test_is_zip_file(self):
        """Test is_zip_file helper"""
        assert is_zip_file(b'PK\x03\x04...') == True
        assert is_zip_file(b'%PDF-1.4\n...') == False

    def test_is_processable_by_claude(self):
        """Test Claude-compatible file detection"""
        assert is_processable_by_claude(b'%PDF-1.4\n...') == True  # PDF
        assert is_processable_by_claude(b'\x89PNG\r\n\x1a\n...') == True  # PNG
        assert is_processable_by_claude(b'{"key": "value"}') == True  # JSON
        assert is_processable_by_claude(b'PK\x03\x04...') == False  # ZIP (not processable)

    def test_get_appropriate_extension(self):
        """Test extension generation from file type"""
        assert get_appropriate_extension(b'%PDF-1.4\n...') == '.pdf'
        assert get_appropriate_extension(b'PK\x03\x04...') == '.zip'
        assert get_appropriate_extension(b'\x89PNG\r\n\x1a\n...') == '.png'
        assert get_appropriate_extension(b'\xFF\xD8\xFF\xE0...') == '.jpg'

    def test_get_file_type_description(self):
        """Test human-readable file type descriptions"""
        assert get_file_type_description('pdf') == 'PDF Document'
        assert get_file_type_description('zip') == 'ZIP Archive'
        assert get_file_type_description('png') == 'PNG Image'
        assert get_file_type_description('unknown') == 'Unknown File Type'
