# This adapter file will help to utilize S3 related operations.
#
# PERFORMANCE & RELIABILITY OPTIMIZATIONS:
# - Connection pooling (50 connections for Lambda concurrency)
# - Request timeouts (5s connect, 30s read)
# - Retry policy (3 attempts with exponential backoff)

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from urllib.parse import urlparse

from src.exceptions.custom_exceptions import S3ServiceError


class S3Service:
    def __init__(self, logger, env):
        self.logger = logger
        self.logger.info("S3Service initialized with optimized configuration")
        self.env = env

        # Optimized boto3 configuration for production Lambda
        boto_config = Config(
            # Security: Use S3 Signature Version 4 (required for some regions)
            signature_version='s3v4',

            # Performance: Increase connection pool for Lambda concurrency
            # Default: 10 connections (insufficient for high-traffic Lambda)
            # Optimal: 50 connections (supports burst traffic)
            max_pool_connections=50,

            # Reliability: Configure timeouts to prevent hanging requests
            # connect_timeout: Time to establish TCP connection
            # read_timeout: Time to read response after connection established
            connect_timeout=5,   # 5 seconds to connect (fail fast)
            read_timeout=30,     # 30 seconds to read data (reasonable for large uploads)

            # Reliability: Explicit retry policy with exponential backoff
            retries={
                'max_attempts': 3,        # Retry up to 3 times
                'mode': 'adaptive'        # Adaptive retry mode (smart throttling)
            }
        )

        self.s3_client = boto3.client("s3", region_name=env.aws_region, config=boto_config)
        self.logger.info("S3 client configured: 50 connections, 5s connect timeout, 30s read timeout")

    def list_objects(self, bucket, prefix):
        """List all objects under a given S3 prefix.

        Returns a list of S3 key strings found under the prefix.
        """
        self.logger.info(f"Listing S3 objects in bucket: {bucket}, prefix: {prefix}")
        try:
            response = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            keys = [obj['Key'] for obj in response.get('Contents', [])]
            self.logger.info(f"Found {len(keys)} object(s) under prefix '{prefix}': {keys}")
            return keys
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            self.logger.error(f"S3 ClientError ({error_code}) listing objects under '{prefix}': {e}")
            raise S3ServiceError(
                f"Failed to list objects under prefix '{prefix}': {error_code}",
                original_error=e
            )
        except BotoCoreError as e:
            self.logger.error(f"BotoCore Error listing objects under '{prefix}': {e}")
            raise S3ServiceError(
                "AWS SDK error while listing S3 objects",
                original_error=e
            )
        except Exception as e:
            self.logger.error(f"Unexpected error listing objects under '{prefix}': {e}")
            raise S3ServiceError(
                "Unexpected error while listing S3 objects",
                original_error=e
            )

    def generate_presigned_url(self, params, clientMethod='put_object'):
        self.logger.info(f"Generating presigned URL for bucket: {params['Bucket']}, key: {params['Key']}, content_type: {params.get('ContentType', 'N/A')}")
        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod=clientMethod,
                Params=params,
                ExpiresIn=self.env.presigned_url_expiration,
            )
            # Security: Log only URL path without query params (which contain signed credentials)
            parsed = urlparse(url)
            safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            self.logger.info(f"Generated presigned URL: {safe_url} (expires in {self.env.presigned_url_expiration}s)")
            return url
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            self.logger.error(f"S3 ClientError ({error_code}): {e}")
            raise S3ServiceError(
                f"Failed to generate presigned URL: {error_code}",
                original_error=e
            )
        except BotoCoreError as e:
            self.logger.error(f"BotoCore Error generating presigned URL: {e}")
            raise S3ServiceError(
                "AWS SDK error while generating presigned URL",
                original_error=e
            )
        except Exception as e:
            self.logger.error(f"Unexpected error generating presigned URL: {e}")
            raise S3ServiceError(
                "Unexpected error while generating presigned URL",
                original_error=e
            )