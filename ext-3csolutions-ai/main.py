# Document Extraction Pipeline - Fargate Entry Point
# This is the main entry point for the Fargate task

import sys
import signal
import traceback
from typing import Optional
from src.controller.data_controller import DataController
from src.utils.config import PipelineConfig
from src.utils.logger import get_logger, set_request_context
from src.utils.exceptions import (
    ValidationError,
    SecurityError,
    ConfigurationError
)
from src.utils.timeout import TimeoutContext, TimeoutError
from src.utils.constants import REQUEST_TIMEOUT

logger = get_logger(__name__)

# Global reference to controller for shutdown handling
_controller: Optional[DataController] = None
_shutdown_requested = False


def handle_shutdown_signal(signum, frame):
    """
    Handle graceful shutdown on SIGTERM/SIGINT.

    Called when:
    - ECS sends SIGTERM during task stop (30s grace period)
    - User presses Ctrl+C (SIGINT)

    This ensures proper cleanup of resources and compensation of failed stages.
    """
    global _shutdown_requested, _controller

    signal_name = signal.Signals(signum).name
    logger.warning(
        f"Received {signal_name} signal - initiating graceful shutdown",
        extra={
            'signal': signal_name,
            'signal_number': signum
        }
    )

    _shutdown_requested = True

    # If controller exists and has a saga, trigger compensation
    if _controller:
        try:
            logger.info("Triggering saga compensation for graceful shutdown...")
            # Access the saga if it exists (may need to add saga attribute to controller)
            if hasattr(_controller, 'saga'):
                _controller.saga.compensate()
            logger.info("Graceful shutdown cleanup completed")
        except Exception as e:
            logger.error(
                f"Error during shutdown cleanup: {e}",
                extra={'error': str(e)},
                exc_info=True
            )

    logger.warning("Exiting gracefully")
    sys.exit(0)


def lambda_style_entrypoint():
    """
    Main entry point for Fargate task with comprehensive error handling.

    Required Environment Variables:
        - BUCKET_NAME: S3 bucket for file storage
        - OBJECT_KEY: S3 key to input ZIP or PDF file
        - USE_TEXTRACT: Pipeline mode (true/false)

    Optional Environment Variables:
        - AWS_REGION: AWS region (default: us-east-1)
        - SCHEMA_S3_KEY: S3 key to JSON schema
        - DYNAMODB_TABLE_NAME: DynamoDB table name
        - S3_KMS_KEY_ID: KMS key for encryption
    """
    global _controller
    request_id = "UNKNOWN"

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    try:
        logger.info("=" * 70)
        logger.info("Document Extraction Pipeline Starting")
        logger.info("=" * 70)

        # Initialize configuration (validates environment)
        logger.info("Initializing configuration...")
        config = PipelineConfig.from_environment()

        # Optional: Validate AWS connectivity
        # config.validate()  # Uncomment to check bucket/table access

        # Initialize controller
        logger.info("Initializing controller...")
        controller = DataController(config)
        _controller = controller  # Store for signal handler access
        request_id = controller.request_id
        set_request_context(request_id)

        logger.info(f"Starting pipeline for request: {request_id}")

        # Check for shutdown signal before running
        if _shutdown_requested:
            logger.warning("Shutdown requested before pipeline start - exiting")
            sys.exit(0)

        # Run the pipeline with overall timeout
        logger.info(f"Pipeline timeout set to {REQUEST_TIMEOUT}s ({REQUEST_TIMEOUT // 60} minutes)")

        with TimeoutContext(REQUEST_TIMEOUT):
            result = controller.run()

        logger.info("=" * 70)
        logger.info("Pipeline Execution Complete")
        logger.info(f"Result: {result}")
        logger.info("=" * 70)

        return result

    except TimeoutError as e:
        logger.error(
            f"Pipeline Timeout: Request exceeded {REQUEST_TIMEOUT}s limit",
            extra={
                'request_id': request_id,
                'timeout_seconds': REQUEST_TIMEOUT,
                'error_type': 'TimeoutError'
            },
            exc_info=True
        )
        sys.exit(1)

    except ConfigurationError as e:
        logger.error(f"Configuration Error: {e}", exc_info=True)
        sys.exit(1)

    except ValidationError as e:
        logger.error(f"Validation Error: {e}", exc_info=True)
        sys.exit(1)

    except SecurityError as e:
        logger.error(f"Security Error: {e}", exc_info=True)
        sys.exit(1)

    except Exception as e:
        logger.error(
            f"Pipeline Failed: {e}",
            extra={
                'request_id': request_id,
                'error_type': type(e).__name__,
                'stack_trace': traceback.format_exc()
            },
            exc_info=True
        )
        sys.exit(1)


if __name__ == "__main__":
    lambda_style_entrypoint()
