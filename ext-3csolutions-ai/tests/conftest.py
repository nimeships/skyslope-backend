"""
Shared test fixtures for pytest
Provides mocked AWS clients and common test data
"""

import pytest
import boto3
from moto import mock_s3, mock_dynamodb, mock_textract
from src.utils.config import PipelineConfig


@pytest.fixture
def mock_aws_credentials(monkeypatch):
    """Mock AWS credentials for testing."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for pipeline configuration."""
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_CLAUDE_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
    monkeypatch.setenv("OBJECT_KEY", "input/test-request-123/test.zip")
    monkeypatch.setenv("SCHEMA_S3_KEY", "schemas/output_schema.json")
    monkeypatch.setenv("USE_TEXTRACT", "false")


@pytest.fixture
def mock_s3_client(mock_aws_credentials):
    """Create mock S3 client."""
    with mock_s3():
        s3 = boto3.client("s3", region_name="us-east-1")
        # Create test bucket
        s3.create_bucket(Bucket="test-bucket")
        yield s3


@pytest.fixture
def mock_dynamodb_client(mock_aws_credentials):
    """Create mock DynamoDB client."""
    with mock_dynamodb():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        # Create test table
        dynamodb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )
        yield dynamodb


@pytest.fixture
def sample_pdf_bytes():
    """Sample PDF file bytes for testing."""
    # Minimal valid PDF
    return b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\n0000000000 65535 f\ntrailer\n<< /Root 1 0 R >>\n%%EOF'


@pytest.fixture
def sample_zip_bytes():
    """Sample ZIP file bytes for testing."""
    # Minimal valid ZIP (PK signature)
    return b'PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00test.txtPK\x01\x02\x14\x00\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00test.txtPK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x006\x00\x00\x00&\x00\x00\x00\x00\x00'


@pytest.fixture
def sample_json_schema():
    """Sample extraction schema for testing."""
    return {
        "PropertyInfo": [
            {
                "Property_Address1": None,
                "Property_City": None,
                "ListPrice": None
            }
        ],
        "CustomerInfo": [
            {
                "Customer_FirstName": None,
                "Customer_LastName": None,
                "Customer_Email": None
            }
        ]
    }


@pytest.fixture
def sample_textract_response():
    """Sample Textract response for testing."""
    return {
        "Blocks": [
            {"BlockType": "LINE", "Text": "Property Address: 123 Main St"},
            {"BlockType": "LINE", "Text": "List Price: $500,000"},
            {"BlockType": "WORD", "Text": "Property"},
            {"BlockType": "WORD", "Text": "Address:"}
        ]
    }
