"""
Test fixtures for integration tests.

Provides common setup for creating PipelineConfig with mocked AWS clients.
"""

import pytest
import boto3
from moto import mock_s3, mock_dynamodb
from src.utils.config import PipelineConfig


@pytest.fixture
def mock_aws_config():
    """
    Create a PipelineConfig with mocked AWS clients.

    Returns a properly initialized PipelineConfig for testing.
    """
    # Create mock boto3 clients
    s3_client = boto3.client('s3', region_name='us-east-1')
    textract_client = boto3.client('textract', region_name='us-east-1')
    bedrock_client = boto3.client('bedrock-runtime', region_name='us-east-1')
    dynamodb_client = boto3.client('dynamodb', region_name='us-east-1')

    config = PipelineConfig(
        aws_region='us-east-1',
        s3_bucket='test-bucket',
        s3_encryption='AES256',
        s3_kms_key_id=None,
        bedrock_model='anthropic.claude-sonnet-4',
        dynamodb_table_name='test-pipeline-status',
        s3_client=s3_client,
        textract_client=textract_client,
        bedrock_client=bedrock_client,
        dynamodb_client=dynamodb_client
    )

    return config


@pytest.fixture
def s3_bucket():
    """Create a test S3 bucket."""
    with mock_s3():
        s3 = boto3.client('s3', region_name='us-east-1')
        bucket_name = 'test-bucket'
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name


@pytest.fixture
def dynamodb_table():
    """Create a test DynamoDB table."""
    with mock_dynamodb():
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        table_name = 'test-pipeline-status'

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'request_id', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'request_id', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST'
        )

        yield table_name
