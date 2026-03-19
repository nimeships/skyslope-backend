"""
Enhanced S3 Adapter with Encryption and Proper Error Handling
Provides secure S3 operations with proper error handling and size validation
"""

import io
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError

from src.utils.config import PipelineConfig
from src.utils.logger import get_logger
from src.utils.exceptions import S3OperationError, FileSizeLimitError
from src.utils.security import validate_file_size, MAX_SINGLE_FILE_SIZE
from src.utils.retry import retry_on_throttling, with_error_context

logger = get_logger(__name__)


class S3Adapter:
    """
    Enhanced adapter for AWS S3 operations with security and error handling.

    Features:
    - Automatic encryption for uploads
    - Size validation
    - Proper error handling with specific exceptions
    - Retry logic for throttling
    - No bare except clauses
    """

    def __init__(self, config: PipelineConfig):
        """
        Initialize S3 adapter with configuration.

        Args:
            config: Pipeline configuration instance
        """
        self.s3_client = config.s3_client
        self.bucket = config.s3_bucket
        self.encryption = config.s3_encryption
        self.kms_key_id = config.s3_kms_key_id

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'upload')
    def upload_file(
        self,
        file_bytes: bytes,
        s3_key: str,
        validate_size: bool = True
    ) -> str:
        """
        Upload file bytes to S3 with encryption.

        Args:
            file_bytes: File content as bytes
            s3_key: S3 object key (path)
            validate_size: Whether to validate file size (default: True)

        Returns:
            S3 key of uploaded object

        Raises:
            FileSizeLimitError: If file exceeds size limit
            S3OperationError: If upload fails
        """
        try:
            # Validate file size
            if validate_size:
                validate_file_size(file_bytes, MAX_SINGLE_FILE_SIZE, "Upload file")

            # Build put_object parameters with encryption
            put_params = {
                'Bucket': self.bucket,
                'Key': s3_key,
                'Body': file_bytes,
                'ServerSideEncryption': self.encryption
            }

            # Add KMS key if specified
            if self.kms_key_id:
                put_params['SSEKMSKeyId'] = self.kms_key_id

            # Upload to S3
            self.s3_client.put_object(**put_params)

            # Verbose success log
            size_kb = round(len(file_bytes) / 1024, 2)
            logger.info(
                f"✓ S3 upload completed: {s3_key} ({size_kb}KB)",
                extra={
                    's3_key': s3_key,
                    'bucket': self.bucket,
                    'size_bytes': len(file_bytes),
                    'size_kb': size_kb,
                    'encryption': self.encryption,
                    'kms_enabled': bool(self.kms_key_id),
                    's3_operation': 'upload',
                    's3_status': 'SUCCESS'
                }
            )

            return s3_key

        except FileSizeLimitError:
            # Re-raise size limit errors
            raise

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"S3 upload failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to upload to S3 key '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error uploading to S3: {e}",
                extra={'s3_key': s3_key},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error uploading to '{s3_key}'"
            ) from e

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'download')
    def download_file(self, s3_key: str, validate_size: bool = True) -> bytes:
        """
        Download file from S3 and return as bytes.

        Args:
            s3_key: S3 object key to download
            validate_size: Whether to validate file size (default: True)

        Returns:
            File content as bytes

        Raises:
            FileSizeLimitError: If file exceeds size limit
            S3OperationError: If download fails
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=s3_key
            )

            file_bytes = response["Body"].read()

            # Validate size after download
            if validate_size:
                validate_file_size(file_bytes, MAX_SINGLE_FILE_SIZE, "Downloaded file")

            # Verbose success log
            size_kb = round(len(file_bytes) / 1024, 2)
            logger.info(
                f"✓ S3 download completed: {s3_key} ({size_kb}KB)",
                extra={
                    's3_key': s3_key,
                    'bucket': self.bucket,
                    'size_bytes': len(file_bytes),
                    'size_kb': size_kb,
                    's3_operation': 'download',
                    's3_status': 'SUCCESS'
                }
            )

            return file_bytes

        except FileSizeLimitError:
            # Re-raise size limit errors
            raise

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')

            # Handle specific error: NoSuchKey
            if error_code == 'NoSuchKey':
                logger.warning(
                    f"S3 object not found: {s3_key}",
                    extra={'s3_key': s3_key}
                )
                raise S3OperationError(f"S3 object not found: {s3_key}") from e

            logger.error(
                f"S3 download failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to download S3 key '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error downloading from S3: {e}",
                extra={'s3_key': s3_key},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error downloading '{s3_key}'"
            ) from e

    def download_file_if_exists(self, s3_key: str) -> Optional[bytes]:
        """
        Download file from S3 if it exists, return None if not found.

        This method is designed for cache lookups and other scenarios where
        a missing file is expected behavior, not an error.

        Args:
            s3_key: S3 object key to download

        Returns:
            File content as bytes if found, None if not found

        Raises:
            S3OperationError: Only for unexpected errors (not NoSuchKey)
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=s3_key
            )
            file_bytes = response["Body"].read()

            logger.debug(
                f"Downloaded file from S3",
                extra={
                    's3_key': s3_key,
                    'size_bytes': len(file_bytes)
                }
            )

            return file_bytes

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')

            # Handle NoSuchKey gracefully - this is expected behavior
            if error_code == 'NoSuchKey':
                logger.debug(
                    f"S3 object not found (expected): {s3_key}",
                    extra={'s3_key': s3_key}
                )
                return None

            # Other errors are unexpected
            logger.error(
                f"S3 download failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to download S3 key '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error downloading from S3: {e}",
                extra={'s3_key': s3_key},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error downloading '{s3_key}'"
            ) from e

    def download_as_stream(self, s3_key: str) -> io.BytesIO:
        """
        Download file from S3 and return as BytesIO stream.

        Args:
            s3_key: S3 object key to download

        Returns:
            BytesIO stream of file content

        Raises:
            S3OperationError: If download fails
        """
        file_bytes = self.download_file(s3_key)
        return io.BytesIO(file_bytes)

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'download_header')
    def download_file_header(self, s3_key: str, header_size: int = 512) -> bytes:
        """
        Download only the file header (first N bytes) from S3.

        PERFORMANCE OPTIMIZATION: For file type detection, we only need the first
        512 bytes (magic bytes) instead of downloading the entire file.
        This saves 99.5%+ bandwidth for large files.

        Example: 100MB PDF detection
        - Before: Download 100MB (100,000,000 bytes)
        - After: Download 512 bytes
        - Savings: 99.9995% bandwidth reduction

        Args:
            s3_key: S3 object key to download
            header_size: Number of bytes to download from start of file (default: 512)

        Returns:
            First N bytes of file content

        Raises:
            S3OperationError: If download fails
        """
        try:
            # Use S3 Range header to download only first N bytes
            # Range format: "bytes=0-511" downloads first 512 bytes
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=s3_key,
                Range=f"bytes=0-{header_size-1}"
            )

            header_bytes = response["Body"].read()

            # Verbose success log
            logger.info(
                f"✓ S3 header download completed: {s3_key} ({len(header_bytes)} bytes)",
                extra={
                    's3_key': s3_key,
                    'bucket': self.bucket,
                    'header_size_bytes': len(header_bytes),
                    'requested_size': header_size,
                    's3_operation': 'download_header',
                    's3_status': 'SUCCESS',
                    'optimization': 'magic_bytes_detection'
                }
            )

            return header_bytes

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')

            # Handle specific error: NoSuchKey
            if error_code == 'NoSuchKey':
                logger.warning(
                    f"S3 object not found: {s3_key}",
                    extra={'s3_key': s3_key}
                )
                raise S3OperationError(f"S3 object not found: {s3_key}") from e

            # Handle InvalidRange error (file smaller than header_size)
            if error_code == 'InvalidRange':
                logger.info(
                    f"File smaller than header size, downloading full file: {s3_key}",
                    extra={'s3_key': s3_key, 'header_size': header_size}
                )
                # Fall back to downloading the entire file
                return self.download_file(s3_key, validate_size=False)

            logger.error(
                f"S3 header download failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code,
                    'header_size': header_size
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to download S3 header for '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error downloading header from S3: {e}",
                extra={'s3_key': s3_key, 'header_size': header_size},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error downloading header for '{s3_key}'"
            ) from e

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'list')
    def list_objects(self, prefix: str) -> List[Dict[str, Any]]:
        """
        List objects with given prefix.

        Args:
            prefix: S3 prefix to filter objects

        Returns:
            List of object metadata dictionaries

        Raises:
            S3OperationError: If listing fails
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix
            )

            objects = response.get("Contents", [])

            # Verbose success log
            logger.info(
                f"✓ S3 list completed: {prefix} ({len(objects)} objects)",
                extra={
                    'prefix': prefix,
                    'bucket': self.bucket,
                    'object_count': len(objects),
                    's3_operation': 'list',
                    's3_status': 'SUCCESS'
                }
            )

            return objects

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"S3 list failed: {e}",
                extra={
                    'prefix': prefix,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to list S3 objects with prefix '{prefix}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error listing S3 objects: {e}",
                extra={'prefix': prefix},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error listing prefix '{prefix}'"
            ) from e

    def file_exists(self, s3_key: str) -> bool:
        """
        Check if file exists in S3.

        Args:
            s3_key: S3 object key to check

        Returns:
            True if object exists, False if not found

        Raises:
            S3OperationError: For errors other than NoSuchKey (e.g., permission denied)
        """
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=s3_key)
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')

            # 404 / NoSuchKey means file doesn't exist
            if error_code in ['404', 'NoSuchKey']:
                return False

            # Other errors (permission denied, etc.) should raise
            logger.error(
                f"S3 head_object failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Error checking existence of S3 key '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error checking S3 object existence: {e}",
                extra={'s3_key': s3_key},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error checking '{s3_key}'"
            ) from e

    def get_object_size(self, s3_key: str) -> int:
        """
        Get the size of an S3 object in bytes.

        Args:
            s3_key: S3 object key

        Returns:
            Size in bytes

        Raises:
            S3OperationError: If operation fails
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket,
                Key=s3_key
            )
            return response['ContentLength']

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise S3OperationError(
                f"Failed to get size of S3 key '{s3_key}': {error_code}"
            ) from e

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'delete')
    def delete_file(self, s3_key: str):
        """
        Delete a single file from S3.

        Args:
            s3_key: S3 object key to delete

        Raises:
            S3OperationError: If deletion fails
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=s3_key
            )

            logger.info(
                f"✓ S3 delete completed: {s3_key}",
                extra={
                    's3_key': s3_key,
                    'bucket': self.bucket,
                    's3_operation': 'delete',
                    's3_status': 'SUCCESS'
                }
            )

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"S3 delete failed: {e}",
                extra={
                    's3_key': s3_key,
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to delete S3 key '{s3_key}': {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error deleting from S3: {e}",
                extra={'s3_key': s3_key},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error deleting '{s3_key}'"
            ) from e

    @retry_on_throttling(max_attempts=3)
    @with_error_context('S3', 'batch_delete')
    def delete_objects(self, s3_keys: List[str]) -> dict:
        """
        Delete multiple files from S3 in a single batch operation.

        AWS limits: Max 1000 keys per request.
        This method automatically chunks larger lists.

        Args:
            s3_keys: List of S3 object keys to delete

        Returns:
            Dictionary with:
                - deleted: List of successfully deleted keys
                - errors: List of failed deletions with error details

        Raises:
            S3OperationError: If batch deletion fails
        """
        if not s3_keys:
            logger.info("No objects to delete")
            return {'deleted': [], 'errors': []}

        all_deleted = []
        all_errors = []

        # AWS delete_objects limit is 1000 keys per request
        BATCH_SIZE = 1000

        try:
            # Process in batches of 1000
            for i in range(0, len(s3_keys), BATCH_SIZE):
                batch = s3_keys[i:i + BATCH_SIZE]

                # Build delete request
                delete_request = {
                    'Objects': [{'Key': key} for key in batch]
                }

                response = self.s3_client.delete_objects(
                    Bucket=self.bucket,
                    Delete=delete_request
                )

                # Track successful deletions
                deleted = response.get('Deleted', [])
                all_deleted.extend([obj['Key'] for obj in deleted])

                # Track errors
                errors = response.get('Errors', [])
                all_errors.extend(errors)

                logger.info(
                    f"✓ S3 batch delete completed: {len(deleted)} deleted, {len(errors)} errors",
                    extra={
                        'bucket': self.bucket,
                        'batch_size': len(batch),
                        'deleted_count': len(deleted),
                        'error_count': len(errors),
                        's3_operation': 'batch_delete',
                        's3_status': 'SUCCESS' if not errors else 'PARTIAL'
                    }
                )

            # Final summary
            logger.info(
                f"Batch delete complete: {len(all_deleted)}/{len(s3_keys)} deleted",
                extra={
                    'total_requested': len(s3_keys),
                    'total_deleted': len(all_deleted),
                    'total_errors': len(all_errors)
                }
            )

            return {
                'deleted': all_deleted,
                'errors': all_errors
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(
                f"S3 batch delete failed: {e}",
                extra={
                    'key_count': len(s3_keys),
                    'error_code': error_code
                },
                exc_info=True
            )
            raise S3OperationError(
                f"Failed to batch delete {len(s3_keys)} objects: {error_code}"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error in batch delete: {e}",
                extra={'key_count': len(s3_keys)},
                exc_info=True
            )
            raise S3OperationError(
                f"Unexpected error deleting {len(s3_keys)} objects"
            ) from e
