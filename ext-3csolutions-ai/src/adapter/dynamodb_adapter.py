"""
Enhanced DynamoDB Adapter with Comprehensive Status Tracking
Provides detailed step tracking, error metadata, and file-level progress
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from botocore.exceptions import ClientError

from src.utils.config import PipelineConfig
from src.utils.logger import get_logger
from src.utils.exceptions import DynamoDBOperationError
from src.utils.retry import retry_on_throttling
from src.utils.constants import (
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
    STATUS_SUCCESS,
    STATUS_FAILED
)
from src.utils.pii_sanitizer import sanitize_filename, sanitize_text, sanitize_stack_trace

logger = get_logger(__name__)


class DynamoDBAdapter:
    """
    Enhanced adapter for DynamoDB status tracking operations.

    Provides comprehensive tracking with:
    - Detailed step metadata
    - File-level progress tracking
    - Error categorization and stack traces
    - Timestamps for each stage
    - Success/failure counts
    """

    def __init__(self, config: PipelineConfig):
        """
        Initialize DynamoDB adapter with configuration.

        PERFORMANCE OPTIMIZATION: Using DynamoDB client API instead of resource API
        - Client API is 50-100ms faster per call (no abstraction overhead)
        - More control over operations and better debugging
        - Requires manual type conversion but worth the performance gain

        Args:
            config: Pipeline configuration instance
        """
        self.dynamodb_client = config.dynamodb_client
        self.table_name = config.dynamodb_table_name

    @retry_on_throttling(max_attempts=5, backoff_base=2, max_backoff=60)
    def _put_item(self, dynamodb_item: dict) -> None:
        """Execute DynamoDB put_item with application-level retry for throttling."""
        self.dynamodb_client.put_item(
            TableName=self.table_name,
            Item=dynamodb_item
        )

    def update_status(
        self,
        request_id: str,
        stage: str,
        status: str = STATUS_IN_PROGRESS,
        progress: int = 0,
        total: int = 0,
        message: str = "",
        error_details: Optional[Dict[str, Any]] = None,
        file_details: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        folder_name: Optional[str] = None
    ) -> None:
        """
        Update job status in DynamoDB with comprehensive tracking.

        Args:
            request_id: Unique job identifier
            stage: Current pipeline stage (e.g., 'Unzip', 'OCR', 'LLM Extraction')
            status: Stage status (IN_PROGRESS, COMPLETED, SUCCESS, FAILED)
            progress: Number of items completed
            total: Total number of items to process
            message: Human-readable status message
            error_details: Optional error information (type, message, traceback)
            file_details: Optional list of file processing details
            metadata: Optional additional metadata (duration, etc.)

        Raises:
            DynamoDBOperationError: If update fails
        """
        try:
            # Convert to Decimal for DynamoDB compatibility
            progress_decimal = Decimal(str(progress))
            total_decimal = Decimal(str(total))
            timestamp = datetime.utcnow().isoformat() + 'Z'

            # SECURITY: Sanitize message to remove PII
            sanitized_message = sanitize_text(message, max_length=500)

            # Build item data
            item = {
                'request_id': request_id,
                'stage': stage,
                'status': status,
                'progress': progress_decimal,
                'total': total_decimal,
                'message': sanitized_message,
                'last_updated': timestamp
            }

            # Add folder name if provided
            if folder_name:
                item['folder_name'] = folder_name

            # Add progress percentage
            if total > 0:
                percentage = (progress / total) * 100
                item['progress_percentage'] = Decimal(str(round(percentage, 2)))
            else:
                item['progress_percentage'] = Decimal('0')

            # Add error details if present
            if error_details:
                # SECURITY: Sanitize error message and stack trace
                error_msg = error_details.get('error_message', '')
                sanitized_error_msg = sanitize_text(error_msg, max_length=1000)

                item['error_details'] = {
                    'error_type': error_details.get('error_type', 'Unknown'),
                    'error_message': sanitized_error_msg,
                    'error_category': error_details.get('error_category', 'UNKNOWN'),
                    'timestamp': error_details.get('timestamp', timestamp)
                }

                # Add stack trace if available (sanitize and truncate)
                if 'stack_trace' in error_details:
                    raw_trace = error_details['stack_trace']
                    sanitized_trace = sanitize_stack_trace(raw_trace)[:5000]
                    item['error_details']['stack_trace'] = sanitized_trace

                # Add file name if error was file-specific (sanitize)
                if 'file_name' in error_details:
                    raw_filename = error_details['file_name']
                    safe_filename = sanitize_filename(raw_filename)
                    item['error_details']['file_name'] = safe_filename

            # Add file processing details if present
            if file_details:
                # Store only recent file details (last 50) to avoid size limits
                item['file_details'] = file_details[-50:]

            # Add metadata if present
            if metadata:
                item['metadata'] = metadata

            # Calculate duration if start_time is in metadata
            if metadata and 'start_time' in metadata:
                try:
                    start = datetime.fromisoformat(metadata['start_time'].replace('Z', ''))
                    end = datetime.fromisoformat(timestamp.replace('Z', ''))
                    duration = (end - start).total_seconds()
                    item['duration_seconds'] = Decimal(str(round(duration, 2)))
                except Exception:
                    pass  # Ignore duration calculation errors

            # Write to DynamoDB using client API
            # Convert Python types to DynamoDB format
            # NOTE: Use _python_dict_to_dynamodb_item for top-level Item (not _python_to_dynamodb)
            dynamodb_item = self._python_dict_to_dynamodb_item(item)
            self._put_item(dynamodb_item)

            logger.info(
                f"Status updated: {stage} - {status}",
                extra={
                    'request_id': request_id,
                    'stage': stage,
                    'status': status,
                    'progress': progress,
                    'total': total
                }
            )

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"DynamoDB update failed: {e}",
                extra={
                    'request_id': request_id,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise DynamoDBOperationError(
                f"Failed to update status for {request_id}: {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error updating DynamoDB: {e}",
                extra={'request_id': request_id},
                exc_info=True
            )
            raise DynamoDBOperationError(
                f"Unexpected error updating status for {request_id}"
            ) from e

    def update_file_status(
        self,
        request_id: str,
        stage: str,
        file_name: str,
        file_status: str,
        file_message: str = "",
        file_error: Optional[str] = None
    ) -> None:
        """
        Update status for a specific file within a stage.

        Args:
            request_id: Unique job identifier
            stage: Current pipeline stage
            file_name: Name of the file being processed
            file_status: File processing status (SUCCESS, FAILED, IN_PROGRESS)
            file_message: Status message for this file
            file_error: Optional error message if file processing failed
        """
        file_detail = {
            'file_name': file_name,
            'status': file_status,
            'message': file_message,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        if file_error:
            file_detail['error'] = file_error

        # This would typically append to a list, but DynamoDB updates are complex
        # For simplicity, we'll log it and rely on aggregate status updates
        logger.info(
            f"File status: {file_name} - {file_status}",
            extra={
                'request_id': request_id,
                'stage': stage,
                'file_name': file_name,
                'file_status': file_status
            }
        )

    def get_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch job status from DynamoDB using client API.

        Args:
            request_id: Unique job identifier

        Returns:
            Status dictionary or None if not found

        Raises:
            DynamoDBOperationError: If read fails
        """
        try:
            response = self.dynamodb_client.get_item(
                TableName=self.table_name,
                Key={'request_id': {'S': request_id}}
            )

            if 'Item' in response:
                # Parse DynamoDB format to Python dict
                item = self._dynamodb_to_python(response['Item'])
                return item

            return None

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"DynamoDB read failed: {e}",
                extra={
                    'request_id': request_id,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise DynamoDBOperationError(
                f"Failed to read status for {request_id}: {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error reading DynamoDB: {e}",
                extra={'request_id': request_id},
                exc_info=True
            )
            raise DynamoDBOperationError(
                f"Unexpected error reading status for {request_id}"
            ) from e

    def mark_stage_complete(
        self,
        request_id: str,
        stage: str,
        total_processed: int,
        success_count: int,
        error_count: int,
        message: str = "",
        folder_name: Optional[str] = None
    ) -> None:
        """
        Mark a pipeline stage as completed with summary statistics.

        Args:
            request_id: Unique job identifier
            stage: Stage name
            total_processed: Total items processed
            success_count: Number of successful items
            error_count: Number of failed items
            message: Completion message
        """
        metadata = {
            'success_count': success_count,
            'error_count': error_count,
            'total_processed': total_processed
        }

        status = STATUS_COMPLETED if error_count == 0 else STATUS_SUCCESS
        final_message = message or f"{stage} completed: {success_count}/{total_processed} successful"

        self.update_status(
            request_id=request_id,
            stage=stage,
            status=status,
            progress=total_processed,
            total=total_processed,
            message=final_message,
            metadata=metadata,
            folder_name=folder_name
        )

    def mark_stage_failed(
        self,
        request_id: str,
        stage: str,
        error_message: str,
        error_type: str = "Unknown",
        error_category: str = "SYSTEM_ERROR",
        stack_trace: Optional[str] = None,
        file_name: Optional[str] = None,
        folder_name: Optional[str] = None
    ) -> None:
        """
        Mark a pipeline stage as failed with detailed error information.

        Args:
            request_id: Unique job identifier
            stage: Stage name
            error_message: Error description
            error_type: Type of error (exception class name)
            error_category: Error category (VALIDATION, SECURITY, SYSTEM_ERROR, etc.)
            stack_trace: Optional full stack trace
            file_name: Optional file name if error was file-specific
        """
        error_details = {
            'error_type': error_type,
            'error_message': error_message,
            'error_category': error_category,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }

        if stack_trace:
            error_details['stack_trace'] = stack_trace

        if file_name:
            error_details['file_name'] = file_name

        self.update_status(
            request_id=request_id,
            stage=stage,
            status=STATUS_FAILED,
            progress=0,
            total=0,
            message=f"{stage} failed: {error_message}",
            error_details=error_details,
            folder_name=folder_name
        )

    def _python_dict_to_dynamodb_item(self, python_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Python dictionary to DynamoDB Item format (top-level).

        This is specifically for converting the top-level Item parameter
        in put_item() calls. Unlike _python_to_dynamodb(), this does NOT
        wrap the dict in {'M': ...}.

        Args:
            python_dict: Python dictionary to convert

        Returns:
            DynamoDB Item format: {'field1': {'S': 'value'}, 'field2': {'N': '123'}, ...}
        """
        return {k: self._python_to_dynamodb(v) for k, v in python_dict.items()}

    def _python_to_dynamodb(self, python_obj: Any) -> Dict[str, Any]:
        """
        Convert Python object to DynamoDB format (recursive).

        DynamoDB client API requires explicit type annotations:
        - Strings: {'S': 'value'}
        - Numbers: {'N': '123'}
        - Maps: {'M': {...}}
        - Lists: {'L': [...]}
        - etc.

        NOTE: For top-level Item in put_item(), use _python_dict_to_dynamodb_item() instead.

        Args:
            python_obj: Python dict/list/primitive to convert

        Returns:
            DynamoDB-formatted dict
        """
        if python_obj is None:
            return {'NULL': True}
        elif isinstance(python_obj, str):
            return {'S': python_obj}
        elif isinstance(python_obj, (int, float, Decimal)):
            return {'N': str(python_obj)}
        elif isinstance(python_obj, bool):
            return {'BOOL': python_obj}
        elif isinstance(python_obj, dict):
            return {'M': {k: self._python_to_dynamodb(v) for k, v in python_obj.items()}}
        elif isinstance(python_obj, list):
            return {'L': [self._python_to_dynamodb(item) for item in python_obj]}
        else:
            # Fallback: convert to string
            return {'S': str(python_obj)}

    def _dynamodb_to_python(self, dynamodb_obj: Dict[str, Any]) -> Any:
        """
        Convert DynamoDB format to Python object.

        Parses DynamoDB's typed format back to native Python types.

        Args:
            dynamodb_obj: DynamoDB-formatted dict

        Returns:
            Python object (dict/list/primitive)
        """
        if not isinstance(dynamodb_obj, dict):
            return dynamodb_obj

        # DynamoDB format has exactly one key indicating the type
        if len(dynamodb_obj) == 1:
            type_key = list(dynamodb_obj.keys())[0]
            value = dynamodb_obj[type_key]

            if type_key == 'S':  # String
                return value
            elif type_key == 'N':  # Number
                # Convert to int if possible, else float
                return int(value) if '.' not in value else float(value)
            elif type_key == 'BOOL':  # Boolean
                return value
            elif type_key == 'NULL':  # Null
                return None
            elif type_key == 'M':  # Map (dict)
                return {k: self._dynamodb_to_python(v) for k, v in value.items()}
            elif type_key == 'L':  # List
                return [self._dynamodb_to_python(item) for item in value]
            elif type_key == 'SS':  # String Set
                return set(value)
            elif type_key == 'NS':  # Number Set
                return {int(n) if '.' not in n else float(n) for n in value}
            else:
                return value

        # If not a DynamoDB type, assume it's already a Python dict
        return {k: self._dynamodb_to_python(v) if isinstance(v, dict) else v
                for k, v in dynamodb_obj.items()}
