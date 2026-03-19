"""
AWS Configuration for Document Extraction Pipeline
Provides dependency injection-friendly configuration management
"""

import os
import boto3
from botocore.config import Config
from dataclasses import dataclass
from typing import Optional
from .constants import (
    DEFAULT_AWS_REGION,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_DYNAMODB_TABLE,
    S3_SERVER_SIDE_ENCRYPTION
)
from .exceptions import ConfigurationError
from .logger import configure_logging, get_logger


# Configure logging at module load
configure_logging(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    structured=os.getenv('STRUCTURED_LOGGING', 'true').lower() == 'true'
)

logger = get_logger(__name__)


@dataclass
class PipelineConfig:
    """
    Configuration container for the pipeline.
    Holds all AWS clients and configuration values.
    Supports dependency injection for better testing.
    """

    # AWS Region
    aws_region: str

    # S3 Configuration
    s3_bucket: str
    s3_encryption: str
    s3_kms_key_id: Optional[str]

    # Bedrock Configuration
    bedrock_model: str

    # DynamoDB Configuration
    dynamodb_table_name: str

    # AWS Clients
    s3_client: any
    textract_client: any
    bedrock_client: any
    dynamodb_client: any  # Changed from resource to client for performance

    @classmethod
    def from_environment(cls) -> 'PipelineConfig':
        """
        Create configuration from environment variables.

        Required Environment Variables:
            - BUCKET_NAME: S3 bucket for file storage

        Optional Environment Variables:
            - AWS_REGION: AWS region (default: us-east-1)
            - BEDROCK_CLAUDE_MODEL: Claude model ID
            - DYNAMODB_TABLE_NAME: DynamoDB table name
            - S3_KMS_KEY_ID: KMS key for S3 encryption

        Returns:
            PipelineConfig instance

        Raises:
            ConfigurationError: If required configuration is missing
        """
        # Required configuration
        s3_bucket = os.getenv("BUCKET_NAME")
        if not s3_bucket:
            raise ConfigurationError("Missing required environment variable: BUCKET_NAME")

        # Optional configuration with defaults
        aws_region = os.getenv("AWS_REGION", DEFAULT_AWS_REGION)
        bedrock_model = os.getenv("BEDROCK_CLAUDE_MODEL", DEFAULT_BEDROCK_MODEL)
        dynamodb_table_name = os.getenv("DYNAMODB_TABLE_NAME", DEFAULT_DYNAMODB_TABLE)
        s3_kms_key_id = os.getenv("S3_KMS_KEY_ID")

        # Initialize AWS clients with optimized configuration
        try:
            # PERFORMANCE FIX: Connection pooling and timeouts for all AWS clients
            # - max_pool_connections=50: Supports Fargate concurrency (default: 10)
            # - connect_timeout=5: Fail fast on connection issues
            # - read_timeout=120: Reasonable for long-running operations
            # - retries with adaptive mode: Smart exponential backoff

            # Standard config for S3 and DynamoDB (fast operations)
            standard_config = Config(
                max_pool_connections=50,
                connect_timeout=5,
                read_timeout=30,
                retries={'max_attempts': 3, 'mode': 'adaptive'}
            )

            # Extended timeout for Textract (async job polling)
            textract_config = Config(
                max_pool_connections=20,  # Lower - fewer concurrent Textract jobs
                connect_timeout=5,
                read_timeout=120,  # Longer for job status polling
                retries={'max_attempts': 3, 'mode': 'adaptive'}
            )

            # Extended timeout for Bedrock (LLM inference)
            bedrock_config = Config(
                max_pool_connections=30,  # Moderate - LLM calls are parallel but limited
                connect_timeout=10,  # LLM endpoints may be slower to connect
                read_timeout=180,  # LLM inference can take 30-60s for large documents
                retries={'max_attempts': 5, 'mode': 'adaptive'}  # More retries for throttling
            )

            # Initialize clients with optimized configs
            s3_client = boto3.client("s3", region_name=aws_region, config=standard_config)
            textract_client = boto3.client("textract", region_name=aws_region, config=textract_config)
            bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region, config=bedrock_config)

            # PERFORMANCE FIX: Use DynamoDB client instead of resource (50-100ms faster)
            dynamodb_client = boto3.client("dynamodb", region_name=aws_region, config=standard_config)

            logger.info(
                "AWS clients initialized successfully with optimized configuration",
                extra={
                    'aws_region': aws_region,
                    's3_bucket': s3_bucket,
                    'dynamodb_table': dynamodb_table_name,
                    'bedrock_model': bedrock_model,
                    's3_pool_size': 50,
                    'textract_pool_size': 20,
                    'bedrock_pool_size': 30,
                    'dynamodb_api': 'client'  # Using client API instead of resource
                }
            )

        except Exception as e:
            raise ConfigurationError(f"Failed to initialize AWS clients: {e}") from e

        return cls(
            aws_region=aws_region,
            s3_bucket=s3_bucket,
            s3_encryption=S3_SERVER_SIDE_ENCRYPTION,
            s3_kms_key_id=s3_kms_key_id,
            bedrock_model=bedrock_model,
            dynamodb_table_name=dynamodb_table_name,
            s3_client=s3_client,
            textract_client=textract_client,
            bedrock_client=bedrock_client,
            dynamodb_client=dynamodb_client
        )

    def validate(self) -> None:
        """
        Validate configuration and AWS client connectivity.

        Raises:
            ConfigurationError: If configuration is invalid or AWS clients cannot connect
        """
        # Validate bucket exists
        try:
            self.s3_client.head_bucket(Bucket=self.s3_bucket)
            logger.info(f"Validated S3 bucket: {self.s3_bucket}")
        except Exception as e:
            raise ConfigurationError(
                f"Cannot access S3 bucket '{self.s3_bucket}': {e}"
            ) from e

        # Validate DynamoDB table exists
        try:
            self.dynamodb_client.describe_table(TableName=self.dynamodb_table_name)
            logger.info(f"Validated DynamoDB table: {self.dynamodb_table_name}")
        except Exception as e:
            raise ConfigurationError(
                f"Cannot access DynamoDB table '{self.dynamodb_table_name}': {e}"
            ) from e


# Singleton instance for backward compatibility
_global_config: Optional[PipelineConfig] = None


def get_config() -> PipelineConfig:
    """
    Get or create the global configuration instance.

    Returns:
        PipelineConfig instance
    """
    global _global_config
    if _global_config is None:
        _global_config = PipelineConfig.from_environment()
    return _global_config


def reset_config() -> None:
    """Reset global configuration (useful for testing)"""
    global _global_config
    _global_config = None
