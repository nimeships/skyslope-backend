"""
Integration Tests for ResultCache Invalidation

Tests the cache invalidation feature that was previously incomplete.
Validates Issue #2 fix.
"""

import pytest
from moto import mock_s3
import boto3
import json
from src.service.result_cache import ResultCache
from src.utils.config import PipelineConfig


@mock_s3
def test_cache_invalidate_removes_from_s3():
    """Test that cache.invalidate() actually deletes from S3."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )

    # Create cache
    cache = ResultCache(config, cache_prefix='cache/test/', enable_cache=True)

    # Store a result in cache
    file_hash = 'abc123def456'
    test_result = {'field1': 'value1', 'field2': 'value2'}
    cache.set(file_hash, test_result)

    # Verify cached file exists in S3
    cache_key = f'cache/test/{file_hash}.json'
    response = s3.list_objects_v2(Bucket=bucket, Prefix=cache_key)
    assert 'Contents' in response
    assert len(response['Contents']) == 1

    # Invalidate cache
    cache.invalidate(file_hash)

    # Verify cached file is deleted from S3
    response = s3.list_objects_v2(Bucket=bucket, Prefix=cache_key)
    assert 'Contents' not in response


@mock_s3
def test_cache_invalidate_nonexistent_file():
    """Test invalidating a cache entry that doesn't exist (should not error)."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )

    cache = ResultCache(config, enable_cache=True)

    # Invalidate non-existent cache entry - should not raise error
    cache.invalidate('nonexistent-hash')


@mock_s3
def test_cache_set_get_invalidate_workflow():
    """Test complete cache lifecycle: set → get → invalidate → get."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )

    cache = ResultCache(config, enable_cache=True)

    file_hash = 'workflow-test-hash'
    test_result = {'extracted': 'data', 'confidence': 0.95}

    # 1. Set cache
    cache.set(file_hash, test_result)

    # 2. Get cache - should hit
    cached = cache.get(file_hash)
    assert cached is not None
    assert cached == test_result
    assert cache.hits == 1

    # 3. Invalidate
    cache.invalidate(file_hash)

    # 4. Get cache again - should miss
    cached = cache.get(file_hash)
    assert cached is None
    assert cache.misses == 1


@mock_s3
def test_cache_invalidate_with_file_bytes():
    """Test cache invalidation using file bytes (calculates hash internally)."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )

    cache = ResultCache(config, enable_cache=True)

    file_bytes = b'Test PDF content here'
    test_result = {'field': 'value'}

    # Store using file bytes
    cache.set_with_file_bytes(file_bytes, test_result)

    # Verify it's cached
    cached = cache.get_with_file_bytes(file_bytes)
    assert cached == test_result

    # Invalidate using hash
    file_hash = cache.calculate_hash(file_bytes)
    cache.invalidate(file_hash)

    # Verify it's gone
    cached = cache.get_with_file_bytes(file_bytes)
    assert cached is None


@mock_s3
def test_cache_invalidate_when_disabled():
    """Test that invalidation is no-op when cache is disabled."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    bucket = 'test-bucket'
    s3.create_bucket(Bucket=bucket)

    config = create_test_config(
        s3_bucket=bucket,
        object_key='test.pdf',
        use_textract=False,
        aws_region='us-east-1'
    )

    # Create cache with caching DISABLED
    cache = ResultCache(config, enable_cache=False)

    # Invalidate should be no-op
    cache.invalidate('some-hash')
