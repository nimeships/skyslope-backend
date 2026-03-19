"""
Pytest configuration and shared fixtures.
"""
import pytest
import logging
from unittest.mock import Mock

# Configure logging for tests
logging.basicConfig(level=logging.INFO)


@pytest.fixture
def mock_logger():
    """Mock logger for testing"""
    logger = Mock(spec=logging.Logger)
    logger.info = Mock()
    logger.error = Mock()
    logger.warning = Mock()
    return logger


@pytest.fixture
def mock_env():
    """Mock environment variables"""
    env = Mock()
    env.aws_region = "us-east-1"
    env.s3_bucket = "test-bucket"
    env.presigned_url_expiration = 3600
    env.dynamodb_table = "TestPipelineStatus"
    env.allowed_origins = ["http://localhost:3000"]
    return env


@pytest.fixture
def sample_request_id():
    """Sample request ID in correct format"""
    return "17eeb4ec5aa947708990cb220295e4c4_20260206T123045Z"


@pytest.fixture
def sample_filename():
    """Sample safe filename"""
    return "document.zip"
