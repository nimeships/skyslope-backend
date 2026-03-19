"""
Integration Tests for S3Adapter Delete Operations

Tests the critical delete_file() and delete_objects() methods
that are used for Saga compensation cleanup.
"""

import pytest
from moto import mock_s3
import boto3
from src.adapter.s3_adapter import S3Adapter
from src.utils.config import PipelineConfig
from src.utils.exceptions import S3OperationError


def create_test_config(bucket='test-bucket'):
    """Helper to create test config with mock clients."""
    s3_client = boto3.client('s3', region_name='us-east-1')
    return PipelineConfig(
        aws_region='us-east-1',
        s3_bucket=bucket,
        s3_encryption='AES256',
        s3_kms_key_id=None,
        bedrock_model='anthropic.claude-sonnet-4',
        dynamodb_table_name='test-table',
        s3_client=s3_client,
        textract_client=boto3.client('textract', region_name='us-east-1'),
        bedrock_client=boto3.client('bedrock-runtime', region_name='us-east-1'),
        dynamodb_client=boto3.client('dynamodb', region_name='us-east-1')
    )


@mock_s3
def test_delete_file_success():
    """Test successful deletion of a single S3 file."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload a test file
    test_key = 'test-folder/test-file.txt'
    s3.put_object(Bucket=bucket, Key=test_key, Body=b'test content')

    # Verify file exists
    response = s3.list_objects_v2(Bucket=bucket, Prefix=test_key)
    assert 'Contents' in response
    assert len(response['Contents']) == 1

    # Create adapter
    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Test delete
    adapter.delete_file(test_key)

    # Verify file is deleted
    response = s3.list_objects_v2(Bucket=bucket, Prefix=test_key)
    assert 'Contents' not in response


@mock_s3
def test_delete_file_nonexistent():
    """Test deleting a non-existent file (should not raise error)."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Delete non-existent file - should not raise error (S3 delete is idempotent)
    adapter.delete_file('non-existent-file.txt')


@mock_s3
def test_delete_objects_batch():
    """Test batch deletion of multiple files."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload 10 test files
    test_keys = [f'test-folder/file-{i}.txt' for i in range(10)]
    for key in test_keys:
        s3.put_object(Bucket=bucket, Key=key, Body=f'content-{key}'.encode())

    # Verify all files exist
    response = s3.list_objects_v2(Bucket=bucket, Prefix='test-folder/')
    assert 'Contents' in response
    assert len(response['Contents']) == 10

    # Create adapter
    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Test batch delete
    result = adapter.delete_objects(test_keys)

    # Verify result
    assert len(result['deleted']) == 10
    assert len(result['errors']) == 0

    # Verify all files are deleted
    response = s3.list_objects_v2(Bucket=bucket, Prefix='test-folder/')
    assert 'Contents' not in response


@mock_s3
def test_delete_objects_large_batch():
    """Test batch deletion with >1000 files (tests chunking)."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload 1500 files (more than AWS 1000 limit)
    test_keys = [f'test-folder/file-{i}.txt' for i in range(1500)]
    for key in test_keys:
        s3.put_object(Bucket=bucket, Key=key, Body=b'content')

    # Create adapter
    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Test batch delete (should chunk into 2 batches: 1000 + 500)
    result = adapter.delete_objects(test_keys)

    # Verify all deleted
    assert len(result['deleted']) == 1500
    assert len(result['errors']) == 0

    # Verify all files are deleted
    response = s3.list_objects_v2(Bucket=bucket, Prefix='test-folder/')
    assert 'Contents' not in response


@mock_s3
def test_delete_objects_empty_list():
    """Test batch deletion with empty list."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Test with empty list
    result = adapter.delete_objects([])

    assert result['deleted'] == []
    assert result['errors'] == []


@mock_s3
def test_delete_objects_mixed_existing_nonexisting():
    """Test batch deletion with mix of existing and non-existing files."""
    # Setup
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload only some files
    existing_keys = [f'test-folder/exists-{i}.txt' for i in range(5)]
    for key in existing_keys:
        s3.put_object(Bucket=bucket, Key=key, Body=b'content')

    # Mix existing and non-existing
    all_keys = existing_keys + [f'test-folder/missing-{i}.txt' for i in range(5)]

    config = create_test_config(bucket)
    adapter = S3Adapter(config)

    # Test delete - S3 delete_objects is idempotent, won't error on missing files
    result = adapter.delete_objects(all_keys)

    # All keys should be in deleted (even non-existent ones)
    assert len(result['deleted']) == 10
    assert len(result['errors']) == 0
