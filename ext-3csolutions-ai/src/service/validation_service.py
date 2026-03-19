# Validation Service - Step 3: Validate and deduplicate documents

import os
import hashlib
import traceback
import time
from src.adapter.s3_adapter import S3Adapter
from src.adapter.dynamodb_adapter import DynamoDBAdapter
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.pii_sanitizer import sanitize_filename
from src.utils.constants import (
    STAGE_VALIDATION,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
    MAX_PDF_SIZE
)
from src.utils.file_type_detector import (
    detect_file_type,
    is_pdf_file,
    get_appropriate_extension,
    get_file_type_description
)

logger = get_logger(__name__)


class ValidationService:
    """
    Service for validating and deduplicating documents.

    Accepted file types:
    - PDF documents
    - Images (PNG, JPEG, GIF, WebP, TIFF, BMP) - processed via Claude's vision API

    File types are detected by content (magic bytes), not filename extensions.
    """

    def __init__(self, config: PipelineConfig):
        self.s3 = S3Adapter(config)
        self.tracker = DynamoDBAdapter(config)

    def calculate_hash(self, file_bytes):
        """Calculate SHA256 hash of file"""
        sha = hashlib.sha256()
        sha.update(file_bytes)
        return sha.hexdigest()

    def _process_single_file(
        self,
        obj: dict,
        request_id: str,
        processed_prefix: str,
        index: int,
        total: int
    ) -> dict:
        """
        Process a single file: detect type, validate, deduplicate, and prepare for extraction.

        Args:
            obj: S3 object metadata dict
            request_id: Pipeline request ID
            processed_prefix: S3 prefix for processed files
            index: Current file index (for logging)
            total: Total number of files

        Returns:
            dict with keys: success, file_key (if success), hash, type, filename, error (if failed)
        """
        key = obj["Key"]
        filename = os.path.basename(key)

        # SECURITY: Sanitize filename to detect and remove PII
        filename = sanitize_filename(filename)

        try:
            start_time = time.time()
            logger.info(f"[{index+1}/{total}] Analyzing: {filename}")

            # Step 1: Download file header (512 bytes) for type detection
            header_bytes = self.s3.download_file_header(key, header_size=512)

            # Detect file type by magic bytes (content), NOT extension
            detected_type = detect_file_type(header_bytes)
            type_description = get_file_type_description(detected_type)

            # Only PDFs are accepted
            if not is_pdf_file(header_bytes):
                logger.warning(
                    f"✗ Skipping non-PDF file: {filename} "
                    f"(detected: {type_description})"
                )
                return {'success': False, 'reason': 'unsupported', 'filename': filename}

            logger.info(f"✓ PDF file detected: {filename} ({type_description})")

            # Step 2: NOW download full file (only if processable)
            file_bytes = self.s3.download_file(key)

            # Enforce size limit before sending to Bedrock
            if len(file_bytes) > MAX_PDF_SIZE:
                size_mb = round(len(file_bytes) / (1024 * 1024), 1)
                limit_mb = MAX_PDF_SIZE // (1024 * 1024)
                logger.warning(
                    f"✗ Skipping oversized PDF: {filename} "
                    f"({size_mb}MB exceeds {limit_mb}MB limit)"
                )
                return {'success': False, 'reason': 'oversized', 'filename': filename}

            # Calculate hash for deduplication
            file_hash = self.calculate_hash(file_bytes)

            # Add appropriate extension based on detected file type
            # This ensures downstream processing works correctly
            appropriate_ext = get_appropriate_extension(file_bytes)
            base_name = os.path.splitext(filename)[0] or filename

            if appropriate_ext and not filename.lower().endswith(appropriate_ext):
                new_filename = f"{base_name}{appropriate_ext}"
                logger.info(f"  → Adding extension: {filename} → {new_filename}")
                filename = new_filename

            # Upload to processed folder
            target_key = f"{processed_prefix}/{filename}"
            self.s3.upload_file(file_bytes, target_key)

            duration = round(time.time() - start_time, 2)
            logger.info(
                f"✓ Processed: {filename} ({type_description}) → {target_key} ({duration}s)",
                extra={'file_name': filename, 'processing_duration_seconds': duration}
            )

            return {
                'success': True,
                'file_key': target_key,
                'hash': file_hash,
                'type': detected_type,
                'filename': filename
            }

        except Exception as e:
            duration = round(time.time() - start_time, 2)
            logger.error(
                f"✗ Error processing {filename}: {e} ({duration}s)",
                exc_info=True,
                extra={'file_name': filename, 'error': str(e), 'processing_duration_seconds': duration}
            )
            return {'success': False, 'reason': 'error', 'filename': filename, 'error': str(e)}

    def process_and_deduplicate(self, input_prefix, folder_name=None):
        """
        Validate, deduplicate, and prepare documents for AI extraction.

        Accepted file types:
        - PDF documents
        - Images (PNG, JPEG, GIF, WebP, TIFF, BMP) - via Claude's vision API

        NOTE: File type detection is CONTENT-BASED (magic bytes), not extension-based.
        This supports files uploaded without extensions (Windows compatibility).

        From: input/{request_id}/
        To: processed/{request_id}/
        """
        # Extract request_id
        parts = input_prefix.split("/")
        request_id = parts[1]
        set_request_context(request_id)

        self.tracker.update_status(
            request_id, STAGE_VALIDATION, STATUS_IN_PROGRESS,
            0, 0, "Scanning for processable documents (content-based detection)...",
            folder_name=folder_name
        )
        logger.info(f"Validating files in {input_prefix} - detecting by content (magic bytes)")

        # List all objects in input folder
        all_objs = self.s3.list_objects(input_prefix)
        total_input = len(all_objs)

        processed_files = []
        seen_hashes = set()
        skipped_count = 0
        file_type_counts = {}
        processed_prefix = f"processed/{request_id}"

        # Sequential execution — one file at a time
        logger.info(f"Processing {total_input} files sequentially...")

        completed_count = 0
        for i, obj in enumerate(all_objs):
            completed_count += 1

            result = self._process_single_file(obj, request_id, processed_prefix, i, total_input)

            # Update progress every 5 files (or on last file) to reduce DynamoDB write pressure
            if completed_count % 5 == 0 or completed_count == total_input:
                try:
                    self.tracker.update_status(
                        request_id, STAGE_VALIDATION, STATUS_IN_PROGRESS,
                        completed_count, total_input,
                        f"Processing files... ({completed_count}/{total_input})",
                        metadata={'completed': completed_count, 'total': total_input},
                        folder_name=folder_name
                    )
                except Exception as e:
                    logger.warning(f"DynamoDB progress update failed (non-fatal): {e}")

            if result['success']:
                # Check for duplicate hash
                file_hash = result['hash']
                if file_hash in seen_hashes:
                    logger.warning(
                        f"✗ Skipping duplicate: {result['filename']} "
                        f"(hash: {file_hash[:16]}...)"
                    )
                    skipped_count += 1
                else:
                    seen_hashes.add(file_hash)
                    processed_files.append(result['file_key'])

                    # Track file type counts
                    detected_type = result['type']
                    file_type_counts[detected_type] = file_type_counts.get(detected_type, 0) + 1
            else:
                # File was skipped (unsupported or error)
                skipped_count += 1

        duplicates_removed = len(seen_hashes) - len(processed_files)
        unsupported_skipped = skipped_count - duplicates_removed

        # Build file type summary
        type_summary = ", ".join(
            f"{count} {file_type}" for file_type, count in file_type_counts.items()
        )

        self.tracker.update_status(
            request_id, STAGE_VALIDATION, STATUS_COMPLETED,
            len(processed_files), len(processed_files),
            f"Ready for extraction: {len(processed_files)} files ({type_summary})",
            metadata={
                'total_scanned': total_input,
                'processable_files': len(processed_files),
                'duplicates_removed': duplicates_removed,
                'unsupported_skipped': unsupported_skipped,
                'file_type_breakdown': file_type_counts,
                'detection_method': 'magic_bytes'
            },
            folder_name=folder_name
        )

        logger.info(
            f"✓ Validation complete: {len(processed_files)} files ready for extraction\n"
            f"  - File types: {type_summary}\n"
            f"  - Duplicates removed: {duplicates_removed}\n"
            f"  - Unsupported files skipped: {unsupported_skipped}"
        )

        return processed_files
