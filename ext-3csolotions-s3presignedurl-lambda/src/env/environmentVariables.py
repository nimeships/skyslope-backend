# This file is to define all lambda environmental variables.

import os
class EnvironmentVariables:
    def __init__(self, logger):
        self.logger = logger
        self.logger.info("Loading environment variables")
        self.aws_region = os.getenv("aws_region", "us-east-1")
        self.s3_bucket = os.getenv("s3_bucket", "my-default-bucket")
        self.presigned_url_expiration = int(os.getenv("presign_ttl_seconds", "3600"))  # in seconds
        self.dynamodb_table = os.getenv("dynamodb_table", "PipelineStatus")  # DynamoDB table name for pipeline status
        # CORS Configuration
        allowed_origins_str = os.getenv("allowed_origins", "http://localhost:3000")
        self.allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]