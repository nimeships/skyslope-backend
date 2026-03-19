# Unzip Service - Step 2: Extract ZIP contents to S3

import io
import os
import zipfile
import traceback
from src.adapter.s3_adapter import S3Adapter
from src.adapter.dynamodb_adapter import DynamoDBAdapter
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.security import sanitize_filename, validate_zip_size, validate_extracted_size
from src.utils.constants import STAGE_UNZIP, STATUS_IN_PROGRESS, STATUS_COMPLETED, MAX_FILES_IN_ZIP

logger = get_logger(__name__)


class UnzipService:
    """Service for extracting ZIP files with security validations"""

    def __init__(self, config: PipelineConfig):
        self.s3 = S3Adapter(config)
        self.tracker = DynamoDBAdapter(config)
    
    def unzip_to_input_folder(self, zip_s3_key):
        """
        Extract ZIP from S3 to input folder with security validations
        From: uploads/{request_id}/file.zip
        To: input/{request_id}/
        """
        request_id = None
        folder_name = None
        try:
            # Extract request_id and folder name from key
            parts = zip_s3_key.split("/")
            request_id = parts[1]
            folder_name = os.path.splitext(os.path.basename(zip_s3_key))[0]
            set_request_context(request_id)

            self.tracker.update_status(
                request_id, STAGE_UNZIP, STATUS_IN_PROGRESS,
                0, 0, "Downloading ZIP file...",
                folder_name=folder_name
            )

            input_prefix = f"input/{request_id}"
            logger.info(f"Unzipping to {input_prefix}")

            # Get ZIP from S3
            zip_bytes = self.s3.download_file(zip_s3_key, validate_size=False)

            # Validate ZIP size (prevent zip bombs)
            validate_zip_size(zip_bytes)

            buffer = io.BytesIO(zip_bytes)
            extracted_files = []
            total_extracted = 0

            with zipfile.ZipFile(buffer, 'r') as zf:
                file_list = [f for f in zf.namelist()
                            if not f.startswith("__MACOSX") and not f.endswith("/")]

                # Check file count limit
                if len(file_list) > MAX_FILES_IN_ZIP:
                    raise ValueError(
                        f"ZIP contains {len(file_list)} files, exceeds maximum {MAX_FILES_IN_ZIP}"
                    )

                total_files = len(file_list)

                for i, filename in enumerate(file_list):
                    # SECURITY FIX: Sanitize filename FIRST (before any processing)
                    # This prevents path traversal attacks where filename="../../../etc/passwd"
                    clean_name = sanitize_filename(os.path.basename(filename))
                    if not clean_name:
                        logger.warning(f"Skipping file with invalid name after sanitization: {filename}")
                        continue

                    # Get file info for size validation (now safe to use)
                    file_info = zf.getinfo(filename)

                    # Validate extracted size (prevents zip bomb)
                    total_extracted = validate_extracted_size(file_info, total_extracted)

                    self.tracker.update_status(
                        request_id, STAGE_UNZIP, STATUS_IN_PROGRESS,
                        i + 1, total_files, f"Extracting {clean_name}",
                        metadata={'current_file': clean_name},
                        folder_name=folder_name
                    )

                    target_key = f"{input_prefix}/{clean_name}"
                    self.s3.upload_file(zf.read(filename), target_key)
                    extracted_files.append(target_key)

            self.tracker.update_status(
                request_id, STAGE_UNZIP, STATUS_COMPLETED,
                len(extracted_files), len(extracted_files),
                "Extraction complete",
                metadata={
                    'extracted_count': len(extracted_files),
                    'total_size_bytes': total_extracted
                },
                folder_name=folder_name
            )

            logger.info(f"Successfully extracted {len(extracted_files)} files")
            return input_prefix

        except Exception as e:
            if request_id:
                self.tracker.mark_stage_failed(
                    request_id, STAGE_UNZIP,
                    str(e), type(e).__name__, "UNZIP_ERROR",
                    stack_trace=traceback.format_exc(),
                    folder_name=folder_name
                )
            logger.error(f"Unzip failed: {e}", exc_info=True)
            raise
