"""
Integration Tests for Saga Pattern Cleanup

Tests the critical Saga compensation logic that relies on S3 delete operations.
This validates Issue #1 fix - ensuring delete_file() works during cleanup.
"""

import pytest
from moto import mock_s3
import boto3
from src.service.pipeline_saga import PipelineSaga, AutoCompensatingSaga
from src.adapter.s3_adapter import S3Adapter
from src.utils.config import PipelineConfig


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
def test_saga_cleanup_deletes_s3_files():
    """
    Test that Saga compensation successfully deletes S3 files.

    This is the critical test that would have caught Issue #1
    (missing delete_file method).
    """
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload test files that represent intermediate pipeline outputs
    intermediate_files = [
        'input/req-123/file1.pdf',
        'input/req-123/file2.pdf',
        'processed/req-123/extracted1.json'
    ]

    for key in intermediate_files:
        s3.put_object(Bucket=bucket, Key=key, Body=b'test content')

    # Verify files exist
    response = s3.list_objects_v2(Bucket=bucket)
    assert 'Contents' in response
    assert len(response['Contents']) == 3

    # Create S3 adapter
    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )
    s3_adapter = S3Adapter(config)

    # Create saga with compensation functions
    saga = PipelineSaga(request_id='req-123')

    # Register cleanup for each stage
    saga.register_compensation(
        'Unzip',
        lambda: s3_adapter.delete_objects([
            'input/req-123/file1.pdf',
            'input/req-123/file2.pdf'
        ])
    )

    saga.register_compensation(
        'Extraction',
        lambda: s3_adapter.delete_file('processed/req-123/extracted1.json')
    )

    # Mark stages as complete
    saga.mark_stage_complete('Unzip')
    saga.mark_stage_complete('Extraction')

    # Trigger compensation (simulating pipeline failure)
    saga.compensate()

    # Verify all files are deleted
    response = s3.list_objects_v2(Bucket=bucket)
    assert 'Contents' not in response


@mock_s3
def test_auto_compensating_saga_on_exception():
    """Test AutoCompensatingSaga automatically cleans up on exception."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload test file
    test_key = 'temp/req-456/temp-file.txt'
    s3.put_object(Bucket=bucket, Key=test_key, Body=b'temp content')

    # Verify file exists
    response = s3.list_objects_v2(Bucket=bucket, Prefix=test_key)
    assert 'Contents' in response

    # Create S3 adapter
    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )
    s3_adapter = S3Adapter(config)

    # Use AutoCompensatingSaga
    with pytest.raises(RuntimeError):
        with AutoCompensatingSaga(request_id='req-456') as saga:
            # Register cleanup
            saga.register_compensation(
                'TempFiles',
                lambda: s3_adapter.delete_file(test_key)
            )
            saga.mark_stage_complete('TempFiles')

            # Simulate failure
            raise RuntimeError("Pipeline failed!")

    # Verify file was deleted by auto-compensation
    response = s3.list_objects_v2(Bucket=bucket, Prefix=test_key)
    assert 'Contents' not in response


@mock_s3
def test_saga_compensation_continues_on_individual_failure():
    """Test that Saga continues compensating even if one cleanup fails."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    # Upload test files
    file1_key = 'temp/file1.txt'
    file2_key = 'temp/file2.txt'
    s3.put_object(Bucket=bucket, Key=file1_key, Body=b'content1')
    s3.put_object(Bucket=bucket, Key=file2_key, Body=b'content2')

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )
    s3_adapter = S3Adapter(config)

    saga = PipelineSaga(request_id='req-789')

    # Register cleanup that will fail (bad bucket name in lambda)
    def failing_cleanup():
        bad_adapter = S3Adapter(create_test_config(
            s3_bucket='non-existent-bucket',
            object_key='test.pdf',
            use_textract=False,
            aws_region='us-east-1'
        ))
        bad_adapter.delete_file('some-file.txt')

    saga.register_compensation('FailingStage', failing_cleanup)
    saga.register_compensation('WorkingStage', lambda: s3_adapter.delete_file(file2_key))

    saga.mark_stage_complete('FailingStage')
    saga.mark_stage_complete('WorkingStage')

    # Compensate - should not raise exception
    saga.compensate()

    # Verify file2 was still deleted despite file1 cleanup failing
    response = s3.list_objects_v2(Bucket=bucket, Prefix=file2_key)
    assert 'Contents' not in response


def test_saga_no_compensation_without_completed_stages():
    """Test Saga doesn't try to compensate if no stages completed."""
    saga = PipelineSaga(request_id='req-000')

    # Register compensations but don't mark any stages complete
    saga.register_compensation('Stage1', lambda: None)
    saga.register_compensation('Stage2', lambda: None)

    # Should log but not execute compensations
    saga.compensate()

    # Verify no stages were marked complete
    assert len(saga.completed_stages) == 0
