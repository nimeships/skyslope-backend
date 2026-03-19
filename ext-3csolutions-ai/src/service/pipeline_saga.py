"""
Saga Pattern Implementation for Pipeline Cleanup
Ensures compensation/rollback of completed stages if later stage fails
"""

from typing import Callable, List, Tuple, Optional
from ..utils.logger import get_logger

logger = get_logger(__name__)


class PipelineSaga:
    """
    Saga pattern for multi-stage pipeline with automatic cleanup.

    Manages transactional guarantees across multiple stages by registering
    compensation (cleanup) functions and executing them in reverse order
    if the pipeline fails.

    Example:
        saga = PipelineSaga(request_id="req-123")

        # Register cleanup functions
        saga.register_compensation('Unzip', lambda: cleanup_s3(f"input/{req_id}/"))
        saga.register_compensation('Validation', lambda: cleanup_s3(f"processed/{req_id}/"))

        # Mark stages as they complete
        unzip_files(...)
        saga.mark_stage_complete('Unzip')

        validate_files(...)
        saga.mark_stage_complete('Validation')

        # If exception occurs, call saga.compensate() to rollback
    """

    def __init__(self, request_id: str):
        """
        Initialize saga for a pipeline execution.

        Args:
            request_id: Unique identifier for this pipeline run
        """
        self.request_id = request_id
        self.compensations: List[Tuple[str, Callable[[], None]]] = []
        self.completed_stages: List[str] = []

    def register_compensation(self, stage_name: str, cleanup_func: Callable[[], None]):
        """
        Register a cleanup function for a pipeline stage.

        Args:
            stage_name: Name of the stage (e.g., "Unzip", "OCR")
            cleanup_func: Function to call for cleanup (takes no arguments)
        """
        self.compensations.append((stage_name, cleanup_func))
        logger.info(
            f"Saga: Registered compensation for stage '{stage_name}'",
            extra={
                'request_id': self.request_id,
                'stage': stage_name,
                'total_compensations': len(self.compensations)
            }
        )

    def mark_stage_complete(self, stage_name: str):
        """
        Mark a stage as successfully completed.

        Args:
            stage_name: Name of the completed stage
        """
        self.completed_stages.append(stage_name)
        logger.info(
            f"Saga: Stage completed - {stage_name}",
            extra={
                'request_id': self.request_id,
                'stage': stage_name,
                'completed_count': len(self.completed_stages)
            }
        )

    def compensate(self):
        """
        Execute compensation (rollback) for all completed stages.

        Runs compensations in REVERSE order (LIFO) to properly unwind state.
        Continues compensating remaining stages even if one compensation fails.

        This method should be called in exception handlers when the pipeline fails.
        """
        if not self.completed_stages:
            logger.info(
                f"Saga: No stages to compensate for request {self.request_id}",
                extra={'request_id': self.request_id}
            )
            return

        logger.warning(
            f"Saga: Starting compensation for {len(self.completed_stages)} completed stages",
            extra={
                'request_id': self.request_id,
                'stages': self.completed_stages,
                'compensation_count': len(self.completed_stages)
            }
        )

        # Build lookup map for compensations
        compensation_map = {name: func for name, func in self.compensations}

        # Execute in reverse order (LIFO)
        compensation_results = []
        for stage_name in reversed(self.completed_stages):
            cleanup_func = compensation_map.get(stage_name)

            if not cleanup_func:
                logger.warning(
                    f"Saga: No compensation registered for completed stage '{stage_name}'",
                    extra={'request_id': self.request_id, 'stage': stage_name}
                )
                compensation_results.append({'stage': stage_name, 'status': 'NO_COMPENSATION'})
                continue

            try:
                logger.info(
                    f"Saga: Compensating stage '{stage_name}'...",
                    extra={'request_id': self.request_id, 'stage': stage_name}
                )

                cleanup_func()

                logger.info(
                    f"Saga: ✓ Compensation successful for '{stage_name}'",
                    extra={'request_id': self.request_id, 'stage': stage_name}
                )
                compensation_results.append({'stage': stage_name, 'status': 'SUCCESS'})

            except Exception as e:
                # Log error but continue compensating other stages
                logger.error(
                    f"Saga: ✗ Compensation FAILED for '{stage_name}': {e}",
                    extra={
                        'request_id': self.request_id,
                        'stage': stage_name,
                        'error': str(e),
                        'error_type': type(e).__name__
                    },
                    exc_info=True
                )
                compensation_results.append({
                    'stage': stage_name,
                    'status': 'FAILED',
                    'error': str(e)
                })

        # Summary
        success_count = sum(1 for r in compensation_results if r['status'] == 'SUCCESS')
        failed_count = sum(1 for r in compensation_results if r['status'] == 'FAILED')

        logger.warning(
            f"Saga: Compensation complete - {success_count} succeeded, {failed_count} failed",
            extra={
                'request_id': self.request_id,
                'total': len(compensation_results),
                'success': success_count,
                'failed': failed_count,
                'results': compensation_results
            }
        )

    def get_status(self) -> dict:
        """
        Get current saga status (for monitoring/debugging).

        Returns:
            Dictionary with saga state
        """
        return {
            'request_id': self.request_id,
            'completed_stages': self.completed_stages,
            'registered_compensations': [name for name, _ in self.compensations]
        }


class AutoCompensatingSaga(PipelineSaga):
    """
    Saga that automatically compensates on context manager exit if exception occurred.

    Example:
        with AutoCompensatingSaga(request_id) as saga:
            saga.register_compensation('Stage1', cleanup1)

            do_stage1()
            saga.mark_stage_complete('Stage1')

            do_stage2()  # If this raises, cleanup1() is called automatically
            saga.mark_stage_complete('Stage2')
    """

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit context manager.

        If exception occurred (exc_type is not None), automatically compensate.
        """
        if exc_type is not None:
            logger.error(
                f"Saga: Exception detected, triggering automatic compensation",
                extra={
                    'request_id': self.request_id,
                    'exception_type': exc_type.__name__ if exc_type else None,
                    'exception': str(exc_val) if exc_val else None
                }
            )
            self.compensate()

        # Don't suppress the exception - let it propagate
        return False
