#!/usr/bin/env python3
"""
Health Check Script for Document Extraction Pipeline

Verifies AWS service connectivity:
- S3 bucket accessible
- DynamoDB table accessible
- Bedrock model available (optional)

Exit codes:
- 0: Healthy (all checks passed)
- 1: Unhealthy (one or more checks failed)
"""

import sys
import os
from src.utils.logger import get_logger

logger = get_logger(__name__)


def check_s3_connectivity():
    """
    Check S3 bucket accessibility.

    Returns:
        bool: True if S3 is accessible, False otherwise
    """
    try:
        from src.utils.config import PipelineConfig

        config = PipelineConfig.from_environment()
        bucket = config.s3_bucket

        # Try to list objects in bucket (empty list is fine)
        response = config.s3_client.list_objects_v2(
            Bucket=bucket,
            MaxKeys=1  # Only need to verify access, not list all objects
        )

        logger.info(f"✓ S3 health check PASSED: bucket '{bucket}' accessible")
        return True

    except Exception as e:
        logger.error(f"✗ S3 health check FAILED: {e}")
        return False


def check_dynamodb_connectivity():
    """
    Check DynamoDB table accessibility.

    Returns:
        bool: True if DynamoDB is accessible, False otherwise
    """
    try:
        from src.utils.config import PipelineConfig

        config = PipelineConfig.from_environment()
        table_name = config.dynamodb_table_name

        # Describe table to verify it exists and is active
        response = config.dynamodb_client.describe_table(
            TableName=table_name
        )

        status = response['Table']['TableStatus']

        if status != 'ACTIVE':
            logger.error(f"✗ DynamoDB health check FAILED: table '{table_name}' status is '{status}', expected 'ACTIVE'")
            return False

        logger.info(f"✓ DynamoDB health check PASSED: table '{table_name}' is ACTIVE")
        return True

    except Exception as e:
        logger.error(f"✗ DynamoDB health check FAILED: {e}")
        return False


def check_bedrock_connectivity():
    """
    Check Bedrock model accessibility (optional check).

    Returns:
        bool: True if Bedrock is accessible or check is skipped, False if failed
    """
    try:
        from src.utils.config import PipelineConfig

        config = PipelineConfig.from_environment()
        model_id = config.bedrock_model_id

        # List foundation models to verify Bedrock access
        # Note: This doesn't test the specific model, just Bedrock API access
        response = config.bedrock_client.list_foundation_models(
            byProvider='anthropic'
        )

        model_count = len(response.get('modelSummaries', []))

        logger.info(f"✓ Bedrock health check PASSED: {model_count} Anthropic models available")
        return True

    except Exception as e:
        # Bedrock check is optional - some deployments may not need it for healthcheck
        logger.warning(f"⚠ Bedrock health check SKIPPED: {e}")
        return True  # Don't fail healthcheck if Bedrock unavailable


def run_healthcheck():
    """
    Run all health checks.

    Returns:
        bool: True if all checks passed, False otherwise
    """
    logger.info("=" * 60)
    logger.info("Running Health Checks")
    logger.info("=" * 60)

    checks = {
        'S3': check_s3_connectivity(),
        'DynamoDB': check_dynamodb_connectivity(),
        'Bedrock': check_bedrock_connectivity()
    }

    logger.info("=" * 60)
    logger.info("Health Check Results:")
    for service, status in checks.items():
        status_str = "✓ PASS" if status else "✗ FAIL"
        logger.info(f"  {service}: {status_str}")
    logger.info("=" * 60)

    # Overall health status
    all_passed = all(checks.values())

    if all_passed:
        logger.info("Health Check: HEALTHY")
        return True
    else:
        logger.error("Health Check: UNHEALTHY")
        return False


if __name__ == "__main__":
    try:
        healthy = run_healthcheck()
        sys.exit(0 if healthy else 1)
    except Exception as e:
        logger.error(f"Health check error: {e}", exc_info=True)
        sys.exit(1)
