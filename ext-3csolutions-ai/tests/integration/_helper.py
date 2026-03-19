"""Helper to create test config with mock clients."""
import boto3
from src.utils.config import PipelineConfig

def create_test_config(bucket='test-bucket'):
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
