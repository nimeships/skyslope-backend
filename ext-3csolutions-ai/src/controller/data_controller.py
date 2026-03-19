# Data Controller - Entry point for Fargate task

import os
import traceback
from src.service.ingestion_service import IngestionService
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.validation import validate_s3_key, validate_schema_key

logger = get_logger(__name__)


class DataController:
    """
    Controller for the document extraction pipeline.
    Reads configuration from environment variables and runs the pipeline.

    Processing mode: Direct PDF/Document extraction (sends files directly to Claude)
    """

    def __init__(self, config: PipelineConfig):
        logger.info("Initializing DataController")

        # Store config
        self.config = config

        # Get configuration from environment variables
        s3_bucket = config.s3_bucket
        zip_s3_key = os.getenv("OBJECT_KEY")

        # Validate and parse S3 key
        validated_key, request_id = validate_s3_key(zip_s3_key)
        self.zip_s3_key = validated_key
        self.request_id = request_id

        # Parse schema key
        schema_raw = os.getenv("SCHEMA_S3_KEY", "schemas/output_schema.json")
        self.schema_key = validate_schema_key(schema_raw)

        # Set request context for logging
        set_request_context(self.request_id)

        logger.info(
            "Configuration loaded",
            extra={
                's3_bucket': s3_bucket,
                'request_id': self.request_id
            }
        )

        # Initialize service with config
        self.ingestion_service = IngestionService(config)
    
    def run(self):
        """Run the document extraction pipeline"""
        logger.info("Starting pipeline")

        try:
            result = self.ingestion_service.run_pipeline(
                zip_s3_key=self.zip_s3_key,
                schema_key=self.schema_key,
                request_id=self.request_id
            )

            if result:
                logger.info(f"Pipeline completed successfully: {result}")
            else:
                logger.warning("Pipeline completed with no output")

            return result

        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}", exc_info=True)
            raise
