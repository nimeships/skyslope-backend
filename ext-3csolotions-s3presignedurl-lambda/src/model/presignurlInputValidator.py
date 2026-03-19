# This file will define the input validation parameters for presign URL requests.

from pydantic import BaseModel, Field, validator
from typing import Literal

# Allowed content types (whitelist for security)
ALLOWED_CONTENT_TYPES = Literal[
    "application/zip",
    "application/json",
    "application/pdf",
    "text/plain"
]

class PresignRequest(BaseModel):
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original file name (1-255 characters)"
    )
    content_type: ALLOWED_CONTENT_TYPES = Field(
        "application/zip",
        description="MIME type (must be from allowed list)"
    )

    @validator('filename')
    def validate_filename(cls, v):
        """Comprehensive filename validation for security"""
        # Check for path traversal attempts
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("Filename cannot contain path separators or parent directory references")

        # Check for dangerous characters that could cause issues
        dangerous_chars = ['<', '>', ':', '"', '|', '?', '*', '\0', '\n', '\r']
        if any(char in v for char in dangerous_chars):
            raise ValueError("Filename contains invalid characters")

        # Reject hidden files (starting with dot)
        if v.startswith('.'):
            raise ValueError("Hidden files (starting with '.') are not allowed")

        # NOTE: We do NOT enforce file extensions here because:
        # - Windows users may not include extensions in filenames
        # - File type validation is done via content_type (MIME type)
        # - Actual file content validation happens at processing time

        # Check for null bytes or control characters
        if any(ord(char) < 32 for char in v):
            raise ValueError("Filename contains control characters")

        return v

    class Config:
        # Pydantic v2 configuration
        json_schema_extra = {
            "example": {
                "filename": "document.zip",
                "content_type": "application/zip"
            }
        }
