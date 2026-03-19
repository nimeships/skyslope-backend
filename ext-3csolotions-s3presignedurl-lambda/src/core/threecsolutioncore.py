# This file is the core logic for 3C solutions related operations.

import re

from src.exceptions.custom_exceptions import (
    ThreeCSolutionError,
    NotFoundError,
    ServiceUnavailableError,
    InternalServerError,
    S3ServiceError
)


class ThreeCSolutionCore:
    def __init__(self, logger, env, s3helper, s3service, dynamodbservice=None):
        self.logger = logger
        self.logger.info("ThreeCSolutionCore initialized")
        self.env = env
        self.s3helper = s3helper
        self.s3service = s3service
        self.dynamodbservice = dynamodbservice

    # Add core methods as needed
    async def health_check(self) -> dict:
        self.logger.info("Health check in core")
        return {"status": "healthy"}
    
    def generate_presigned_url(self, request):
        self.logger.info("Generating presigned URL in core")
        try:
            self.logger.info(f"Request received for filename: {request.filename}, content_type: {request.content_type}")

            # Build S3 key with content_type to ensure proper extension handling
            key = self.s3helper.build_unique_key(request.filename, request.content_type)
            self.logger.info(f"Generated S3 key: {key}")

            params = {
                "Bucket": self.env.s3_bucket,
                "Key": key,
                "ContentType": request.content_type,
            }
            self.logger.info(f"Presign URL parameters: {params}")
            url = self.s3service.generate_presigned_url(params, clientMethod='put_object')
            # Note: Full URL returned to client, but detailed logging is done in S3Service
            return {"key": key, "url": url}
        except Exception as e:
            # S3ServiceError will be raised by s3service, just propagate it
            # For any unexpected errors, wrap them
            self.logger.error(f"Error in generating presigned URL: {e}")
            if hasattr(e, 'status_code'):
                # Already a custom exception, re-raise
                raise
            raise InternalServerError("Could not generate presigned URL", original_error=e)

    def _resolve_final_output_key(self, unique_id):
        """Resolve the correct FINAL_OUTPUT file key from S3.

        Resolution priority:
          1. Latest timestamped file  : FINAL_OUTPUT_YYYYMMDD_HHMMSS.json
          2. Plain file (no timestamp): FINAL_OUTPUT.json

        Returns the resolved S3 key string.
        Raises NotFoundError when no matching file is found.
        """
        prefix = f"output/{unique_id}/"
        # Pattern for timestamped variant: FINAL_OUTPUT_20260227_195302.json
        ts_pattern = re.compile(r"FINAL_OUTPUT_(\d{8}_\d{6})\.json$")
        plain_key = f"{prefix}FINAL_OUTPUT.json"

        self.logger.debug(f"[resolve_key] Listing objects under prefix: '{prefix}'")
        try:
            all_keys = self.s3service.list_objects(self.env.s3_bucket, prefix)
        except S3ServiceError as e:
            self.logger.error(
                f"[resolve_key] Failed to list output files for unique_id: {unique_id}. Cause: {e}"
            )
            raise ServiceUnavailableError(
                "The download service is temporarily unavailable. Please try again later."
            )

        self.logger.info(f"[resolve_key] Output file list ({len(all_keys)} object(s)): {all_keys}")

        # Separate timestamped files from the plain file
        timestamped = []
        has_plain = False
        for key in all_keys:
            filename = key.split("/")[-1]
            if ts_pattern.match(filename):
                timestamped.append(key)
            elif filename == "FINAL_OUTPUT.json":
                has_plain = True

        self.logger.debug(
            f"[resolve_key] Timestamped files found: {timestamped} | "
            f"Plain FINAL_OUTPUT.json present: {has_plain}"
        )

        if timestamped:
            # Timestamps are YYYYMMDD_HHMMSS — lexicographic sort gives chronological order
            latest_key = sorted(timestamped)[-1]
            self.logger.info(
                f"[resolve_key] Selected latest timestamped file: '{latest_key}' "
                f"(from {len(timestamped)} candidate(s))"
            )
            return latest_key

        if has_plain:
            self.logger.info(
                f"[resolve_key] No timestamped file found. "
                f"Falling back to plain file: '{plain_key}'"
            )
            return plain_key

        self.logger.error(
            f"[resolve_key] No FINAL_OUTPUT file found under prefix '{prefix}'. "
            f"Full object list: {all_keys}"
        )
        raise NotFoundError(
            "The output file for this request is not ready or does not exist. "
            "Please verify your request ID and try again."
        )

    def generate_download_url(self, request):
        self.logger.info("Generating download URL in core")
        try:
            self.logger.info(f"Request received for unique_id: {request.unique_id}")

            # Resolve the correct output file (timestamped or plain)
            key = self._resolve_final_output_key(request.unique_id)
            self.logger.info(f"Resolved S3 key for download: {key}")

            params = {
                "Bucket": self.env.s3_bucket,
                "Key": key,
            }
            self.logger.info(f"Download URL parameters: {params}")
            try:
                url = self.s3service.generate_presigned_url(params, clientMethod='get_object')
            except S3ServiceError as e:
                self.logger.error(f"Failed to generate presigned download URL for resolved key. Cause: {e}")
                raise ServiceUnavailableError(
                    "Unable to prepare the download link. Please try again later."
                )
            return {"key": key, "url": url}
        except ThreeCSolutionError:
            # Already a safe, user-friendly exception — propagate as-is
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error in generating download URL: {e}")
            raise InternalServerError(
                "An unexpected error occurred while processing your download request. "
                "Please try again or contact support."
            )

    def get_pipeline_status(self, request):
        self.logger.info("Fetching pipeline status in core")
        try:
            self.logger.info(f"Request received for unique_id: {request.unique_id}")

            if not self.dynamodbservice:
                self.logger.error("DynamoDBService not initialized")
                raise ServiceUnavailableError("DynamoDB service not available")

            # Fetch status from DynamoDB
            status_data = self.dynamodbservice.get_pipeline_status(request.unique_id)

            if not status_data:
                self.logger.warning(f"No status found for request_id: {request.unique_id}")
                raise NotFoundError(f"Pipeline status not found for request_id: {request.unique_id}")

            # Calculate percentage if total > 0
            progress = status_data.get('progress', 0)
            total = status_data.get('total', 0)
            percentage = int((progress / total) * 100) if total > 0 else 0

            self.logger.info(f"Pipeline status retrieved: stage={status_data.get('stage')}, status={status_data.get('status')}")

            return {
                "request_id": status_data.get('request_id'),
                "stage": status_data.get('stage'),
                "status": status_data.get('status'),
                "progress": progress,
                "total": total,
                "message": status_data.get('message', ''),
                "last_updated": status_data.get('last_updated', ''),
                "percentage": percentage
            }

        except Exception as e:
            self.logger.error(f"Error fetching pipeline status: {e}")
            if hasattr(e, 'status_code'):
                raise
            raise InternalServerError(f"Could not fetch pipeline status: {str(e)}", original_error=e)

    def get_recent_requests(self, limit=10):
        """
        Fetch the most recent request IDs from DynamoDB.

        Args:
            limit: Number of recent requests to fetch (default: 10)

        Returns:
            dict: Contains list of recent requests and count
        """
        self.logger.info(f"Fetching recent {limit} requests in core")
        try:
            if not self.dynamodbservice:
                self.logger.error("DynamoDBService not initialized")
                raise ServiceUnavailableError("DynamoDB service not available")

            # Fetch recent requests from DynamoDB
            recent_requests = self.dynamodbservice.get_recent_requests(limit)

            self.logger.info(f"Retrieved {len(recent_requests)} recent requests")

            return {
                "requests": recent_requests,
                "count": len(recent_requests)
            }

        except Exception as e:
            self.logger.error(f"Error fetching recent requests: {e}")
            if hasattr(e, 'status_code'):
                raise
            raise InternalServerError(f"Could not fetch recent requests: {str(e)}", original_error=e)