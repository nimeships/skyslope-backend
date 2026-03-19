# Pipeline Ingestion Service - Main orchestrator

import os
import traceback
import time
from src.service.unzip_service import UnzipService
from src.service.validation_service import ValidationService
from src.service.direct_pdf_extraction_service import DirectPDFExtractionService
from src.service.pipeline_saga import PipelineSaga
from src.service.event_bus import PipelineEventBus, StageStartedEvent, StageCompletedEvent, StageFailedEvent
from src.service.result_cache import ResultCache
from src.adapter.dynamodb_adapter import DynamoDBAdapter
from src.adapter.s3_adapter import S3Adapter
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.constants import STATUS_IN_PROGRESS, STATUS_FAILED, STATUS_SKIPPED
from src.utils.file_type_detector import detect_file_type

logger = get_logger(__name__)


class IngestionService:
    """
    Main pipeline orchestrator - runs all steps.

    Accepted file types:
    - PDF documents
    - Images (PNG, JPEG, GIF, WebP, TIFF, BMP) - processed via Claude's vision API

    Processing mode:
    - Direct Mode: Documents/Images → Claude directly (vision-enabled)

    Features:
    - Event-driven architecture (EventBus) for monitoring
    - Saga pattern for automatic cleanup/rollback on failure
    - Result caching to avoid reprocessing duplicates
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.s3 = S3Adapter(config)
        self.unzip_service = UnzipService(config)
        self.validation_service = ValidationService(config)
        self.direct_pdf_service = DirectPDFExtractionService(config)
        self.tracker = DynamoDBAdapter(config)

        # Event bus for monitoring and status updates
        self.event_bus = PipelineEventBus()

        # Subscribe logger to event bus for CloudWatch visibility
        self.event_bus.subscribe(self._log_event)

        # Result cache for avoiding reprocessing
        self.result_cache = ResultCache(config, enable_cache=True)

        logger.info("IngestionService initialized with EventBus, Saga, and ResultCache")

    def _safe_update_status(self, *args, **kwargs):
        """Update DynamoDB status without failing the pipeline if tracking is unavailable."""
        try:
            self.tracker.update_status(*args, **kwargs)
        except Exception as e:
            logger.warning(
                f"DynamoDB status update failed (non-fatal): {e}",
                extra={'error_type': type(e).__name__, 'error': str(e)}
            )

    def run_pipeline(self, zip_s3_key, schema_key, request_id):
        """
        Run the complete document extraction pipeline with Saga pattern.

        Accepts only PDF and image files and extracts structured data
        according to the provided JSON schema.

        Features:
        - Publishes events via EventBus for monitoring
        - Uses Saga pattern for automatic cleanup on failure
        - Caches results to avoid reprocessing

        Args:
            zip_s3_key: S3 key to input ZIP file or document file
            schema_key: S3 key to JSON schema file
            request_id: Unique job identifier
        """
        # Initialize Saga for this pipeline run
        saga = PipelineSaga(request_id)
        start_time = time.time()
        folder_name = None

        try:
            # Set request context for logging
            set_request_context(request_id)

            logger.info(f"Starting pipeline for request_id: {request_id}")
            self._safe_update_status(
                request_id, "Initialization", STATUS_IN_PROGRESS,
                0, 0, "Starting pipeline..."
            )

            # Publish pipeline started event
            self.event_bus.publish(StageStartedEvent(request_id, "Initialization"))


            # Step 2: Determine if file is ZIP or direct document
            # Use CONTENT-BASED detection (magic bytes), not extension
            logger.info(f"Analyzing input file: {zip_s3_key}")

            # Check file type by content
            # First try: check by extension (fast path for most cases)
            # Second try: download and check magic bytes (handles files without extensions)
            is_zip = False
            is_direct_input = zip_s3_key.startswith('input/')

            if zip_s3_key.lower().endswith('.zip'):
                # Fast path: filename has .zip extension
                is_zip = True
                logger.info("ZIP file detected by extension")
            elif not is_direct_input:
                # File doesn't have .zip extension - check content
                logger.info("No .zip extension detected, checking file content...")
                try:
                    file_bytes = self.s3.download_file(zip_s3_key, validate_size=False)
                    detected_type = detect_file_type(file_bytes[:1024])  # Check first 1KB
                    logger.info(f"Detected file type by content: {detected_type}")
                    is_zip = (detected_type == 'zip')
                except Exception as e:
                    logger.warning(f"Failed to detect file type by content: {e}")
                    is_zip = False

            if is_zip:
                # ZIP archive - extract contents
                folder_name = os.path.splitext(os.path.basename(zip_s3_key))[0]
                logger.info("Processing ZIP archive, extracting contents...")
                self.event_bus.publish(StageStartedEvent(request_id, "Unzip"))
                stage_start = time.time()

                input_folder = self.unzip_service.unzip_to_input_folder(zip_s3_key)

                # Register cleanup for extracted files
                saga.register_compensation(
                    "Unzip",
                    lambda: self._cleanup_s3_folder(input_folder)
                )
                saga.mark_stage_complete("Unzip")

                self.event_bus.publish(StageCompletedEvent(
                    request_id, "Unzip",
                    duration_seconds=time.time() - stage_start
                ))
            elif is_direct_input:
                # Single document already in input/ folder
                logger.info("Direct document input detected, skipping unzip step")
                input_folder = '/'.join(zip_s3_key.split('/')[:-1])  # Get folder path
                self._safe_update_status(
                    request_id, "Unzip", STATUS_SKIPPED,
                    1, 1, "Direct document input, no unzip needed"
                )
            else:
                raise ValueError(
                    f"Invalid input file: {zip_s3_key}. "
                    f"Expected: ZIP archive (with/without .zip extension) or document in input/ folder"
                )
            
            # Step 3: Validate and deduplicate (accepts only PDFs and images)
            self.event_bus.publish(StageStartedEvent(request_id, "Validation"))
            stage_start = time.time()

            processed_files = self.validation_service.process_and_deduplicate(input_folder, folder_name=folder_name)

            if not processed_files:
                self._safe_update_status(
                    request_id, "Stopped", STATUS_FAILED,
                    0, 0, "No processable files found (PDFs and images only).",
                    folder_name=folder_name
                )
                logger.warning("No processable files found in input")
                self.event_bus.publish(StageFailedEvent(
                    request_id, "Validation",
                    error_message="No processable files found",
                    error_type="ValidationError"
                ))
                return None

            # Register cleanup for processed files
            processed_prefix = f"processed/{request_id}"
            saga.register_compensation(
                "Validation",
                lambda: self._cleanup_s3_folder(processed_prefix)
            )
            saga.mark_stage_complete("Validation")

            self.event_bus.publish(StageCompletedEvent(
                request_id, "Validation",
                duration_seconds=time.time() - stage_start,
                metadata={'file_count': len(processed_files)}
            ))

            # Step 4: Send documents/images directly to Claude with caching
            self.event_bus.publish(StageStartedEvent(request_id, "Extraction"))
            stage_start = time.time()

            final_key = self.direct_pdf_service.run_extraction(
                processed_files, schema_key, request_id,
                result_cache=self.result_cache,
                folder_name=folder_name
            )

            saga.mark_stage_complete("Extraction")

            self.event_bus.publish(StageCompletedEvent(
                request_id, "Extraction",
                duration_seconds=time.time() - stage_start
            ))

            # Pipeline completed successfully
            total_duration = time.time() - start_time
            logger.info(
                f"Pipeline complete! Output: {final_key}",
                extra={
                    'request_id': request_id,
                    'duration_seconds': round(total_duration, 2),
                    'cache_hits': self.result_cache.hits,
                    'cache_misses': self.result_cache.misses
                }
            )
            return final_key

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)

            # Publish failure event
            self.event_bus.publish(StageFailedEvent(
                request_id, "Pipeline",
                error_message=str(e),
                error_type=type(e).__name__
            ))

            # Execute saga compensation (cleanup)
            logger.warning(f"Executing saga compensation for request {request_id}")
            saga.compensate()

            try:
                self.tracker.mark_stage_failed(
                    request_id, "Error",
                    str(e), type(e).__name__, "SYSTEM_ERROR",
                    stack_trace=traceback.format_exc(),
                    folder_name=folder_name
                )
            except Exception as track_err:
                logger.warning(f"Failed to record pipeline error in DynamoDB (non-fatal): {track_err}")
            raise

    def _cleanup_s3_folder(self, prefix: str):
        """
        Cleanup helper for saga compensation.

        Args:
            prefix: S3 prefix to cleanup
        """
        try:
            logger.info(f"Saga cleanup: Removing S3 folder: {prefix}")
            # List and delete all objects with this prefix
            objects = self.s3.list_objects(prefix)
            if objects:
                for obj in objects:
                    self.s3.delete_file(obj['Key'])
                logger.info(f"Saga cleanup: Removed {len(objects)} objects from {prefix}")
        except Exception as e:
            logger.error(f"Saga cleanup failed for {prefix}: {e}", exc_info=True)

    def _log_event(self, event):
        """
        Event subscriber that logs all pipeline events to CloudWatch.

        Args:
            event: PipelineEvent instance
        """
        event_data = {
            'request_id': event.request_id,
            'event_type': event.event_type,
            'timestamp': event.timestamp
        }

        # Add event-specific data
        if hasattr(event, 'stage_name'):
            event_data['stage'] = event.stage_name
        if hasattr(event, 'progress'):
            event_data['progress'] = event.progress
            event_data['total'] = event.total
        if hasattr(event, 'duration_seconds'):
            event_data['duration_seconds'] = round(event.duration_seconds, 2)
        if hasattr(event, 'error_message'):
            event_data['error_message'] = event.error_message
            event_data['error_type'] = event.error_type
        if hasattr(event, 'metadata') and event.metadata:
            event_data['metadata'] = event.metadata

        # Log based on event type
        if event.event_type == 'STAGE_STARTED':
            logger.info(f"EventBus: Stage started - {event.stage_name}", extra=event_data)
        elif event.event_type == 'STAGE_COMPLETED':
            logger.info(f"EventBus: Stage completed - {event.stage_name}", extra=event_data)
        elif event.event_type == 'STAGE_FAILED':
            logger.error(f"EventBus: Stage failed - {event.stage_name}", extra=event_data)
        elif event.event_type == 'STAGE_PROGRESS':
            logger.info(f"EventBus: Progress - {event.stage_name}", extra=event_data)
        else:
            logger.info(f"EventBus: {event.event_type}", extra=event_data)

