"""
AWS Adapters for the document extraction pipeline.

Provides adapters for:
- S3 (file storage)
- Bedrock (LLM)
- DynamoDB (status tracking)
"""

from .s3_adapter import S3Adapter
from .bedrock_adapter import BedrockAdapter
from .dynamodb_adapter import DynamoDBAdapter

__all__ = [
    'S3Adapter',
    'BedrockAdapter',
    'DynamoDBAdapter',
]
