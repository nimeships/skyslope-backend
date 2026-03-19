"""
Integration tests for S3Service.

Tests use moto to mock AWS S3 service.
"""
import pytest
from unittest.mock import Mock
from moto import mock_s3
import boto3
from botocore.exceptions import ClientError

from src.adapter.s3service import S3Service
from src.exceptions.custom_exceptions import S3ServiceError


@mock_s3
class TestS3ServiceIntegration:
    """Integration tests with mocked S3"""

    def setup_method(self):
        """Set up mock S3 bucket before each test"""
        # Create mock S3 bucket
        self.s3_client = boto3.client('s3', region_name='us-east-1')
        self.s3_client.create_bucket(Bucket='test-bucket')

    def test_generate_presigned_url_put_object(self):
        """Should generate valid presigned URL for PUT"""
        mock_logger = Mock()
        mock_env = Mock()
        mock_env.aws_region = 'us-east-1'
        mock_env.presigned_url_expiration = 3600

        service = S3Service(mock_logger, mock_env)

        params = {
            'Bucket': 'test-bucket',
            'Key': 'test/file.pdf',
            'ContentType': 'application/pdf'
        }

        url = service.generate_presigned_url(params, clientMethod='put_object')

        # Verify URL format
        assert 'test-bucket' in url
        assert 'test/file.pdf' in url
        assert 'X-Amz-Algorithm' in url  # Signature present
        assert 'X-Amz-Expires=3600' in url  # Expiration set

    def test_generate_presigned_url_get_object(self):
        """Should generate valid presigned URL for GET"""
        mock_logger = Mock()
        mock_env = Mock()
        mock_env.aws_region = 'us-east-1'
        mock_env.presigned_url_expiration = 3600

        service = S3Service(mock_logger, mock_env)

        params = {
            'Bucket': 'test-bucket',
            'Key': 'output/result.json'
        }

        url = service.generate_presigned_url(params, clientMethod='get_object')

        # Verify URL format
        assert 'test-bucket' in url
        assert 'output/result.json' in url
        assert 'X-Amz-Algorithm' in url

    def test_generate_presigned_url_with_nonexistent_bucket(self):
        """Should handle errors gracefully when bucket doesn't exist"""
        mock_logger = Mock()
        mock_env = Mock()
        mock_env.aws_region = 'us-east-1'
        mock_env.presigned_url_expiration = 3600

        service = S3Service(mock_logger, mock_env)

        # Note: Presigned URL generation doesn't validate bucket existence
        # It will generate URL even if bucket doesn't exist
        # Actual error happens when client tries to use the URL
        params = {
            'Bucket': 'nonexistent-bucket',
            'Key': 'file.pdf',
            'ContentType': 'application/pdf'
        }

        # Should generate URL successfully (validation happens at upload time)
        url = service.generate_presigned_url(params, clientMethod='put_object')
        assert 'nonexistent-bucket' in url

    def test_presigned_url_logging_security(self):
        """Should log URL path but not signature query params"""
        mock_logger = Mock()
        mock_env = Mock()
        mock_env.aws_region = 'us-east-1'
        mock_env.presigned_url_expiration = 3600

        service = S3Service(mock_logger, mock_env)

        params = {
            'Bucket': 'test-bucket',
            'Key': 'secure/file.pdf',
            'ContentType': 'application/pdf'
        }

        url = service.generate_presigned_url(params, clientMethod='put_object')

        # Check that logger was called
        assert mock_logger.info.called

        # Verify logged messages don't contain signature
        logged_messages = [call[0][0] for call in mock_logger.info.call_args_list]
        for message in logged_messages:
            if 'Generated presigned URL' in message:
                # Should not contain signature query parameters
                assert 'X-Amz-Signature' not in message
                assert 'X-Amz-Credential' not in message

    def test_boto3_config_applied(self):
        """Should apply boto3 configuration (timeouts, pooling)"""
        mock_logger = Mock()
        mock_env = Mock()
        mock_env.aws_region = 'us-east-1'
        mock_env.presigned_url_expiration = 3600

        service = S3Service(mock_logger, mock_env)

        # Verify config was applied
        client_config = service.s3_client._client_config
        assert client_config.signature_version == 's3v4'
        assert client_config.max_pool_connections == 50
        assert client_config.connect_timeout == 5
        assert client_config.read_timeout == 30
