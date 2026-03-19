# This file is to create helper functions for s3 operations.

import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Optional, Dict

class S3Helper:
    def __init__(self, logger, env):
        self.logger = logger
        self.logger.info("S3Helper initialized")
        self.env = env

    def sanitize_filename(self, name: str, content_type: Optional[str] = None) -> str:
        """
        Keep it filesystem/S3-key friendly: letters, digits, dot, dash, underscore.

        Note: Extensions are optional, but ZIP files MUST have .zip extension in S3.
        File type is validated via content_type parameter.

        Args:
            name: Original filename from user
            content_type: MIME type (e.g., "application/zip", "application/pdf")

        Returns:
            Sanitized filename with appropriate extension
        """
        try:
            self.logger.info(f"Sanitizing filename: {name}, content_type: {content_type}")
            # Split off any path the client might pass and keep the basename
            base = name.split("/")[-1].split("\\")[-1]
            # Remove whitespace/control chars
            base = base.strip()
            # Enforce only safe chars
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)

            if not safe:  # Empty after sanitization
                self.logger.error("Filename is empty after sanitization")
                raise ValueError("Filename cannot be empty after sanitization")

            # IMPORTANT: Ensure ZIP files always have .zip extension in S3
            # This is required for the AI extraction pipeline to correctly identify and process ZIP archives
            if content_type == "application/zip" or content_type == "application/x-zip-compressed":
                if not safe.lower().endswith('.zip'):
                    safe = f"{safe}.zip"
                    self.logger.info(f"Added .zip extension for ZIP file: {safe}")

            self.logger.info(f"Sanitized filename: {safe}")
            return safe
        except Exception as e:
            self.logger.error(f"Error in sanitizing filename: {e}")
            raise Exception("Could not sanitize filename")
    
    def build_unique_key(self, filename: str, content_type: Optional[str] = None) -> str:
        """
        Build unique S3 key with timestamp and UUID.

        Args:
            filename: Original filename from user
            content_type: MIME type (e.g., "application/zip", "application/pdf")

        Returns:
            S3 key in format: input/{uuid}_{timestamp}/{sanitized_filename}
        """
        try:
            self.logger.info(f"Building unique S3 key for filename: {filename}, content_type: {content_type}")
            now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            u = uuid4().hex
            safe_file = self.sanitize_filename(filename, content_type)
            key = f"input/{u}_{now}/{safe_file}"
            self.logger.info(f"Generated unique S3 key: {key}")
            return key
        except Exception as e:
            self.logger.error(f"Error in building unique S3 key: {e}")
            raise Exception("Could not build unique S3 key")