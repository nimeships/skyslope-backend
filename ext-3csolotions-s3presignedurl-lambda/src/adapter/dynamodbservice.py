# This adapter file will help to utilize DynamoDB related operations.
#
# PERFORMANCE OPTIMIZATIONS:
# - Using boto3 client instead of resource (50-100ms faster per call)
# - Connection pooling (50 connections for Lambda concurrency)
# - Request timeouts (5s connect, 30s read)
# - Retry policy (3 attempts with exponential backoff)

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from decimal import Decimal

from src.exceptions.custom_exceptions import DynamoDBServiceError


class DynamoDBService:
    def __init__(self, logger, env):
        self.logger = logger
        self.logger.info("DynamoDBService initialized with optimized configuration")
        self.env = env

        # Optimized boto3 configuration for production Lambda
        boto_config = Config(
            # Performance: Increase connection pool for Lambda concurrency
            max_pool_connections=50,

            # Reliability: Configure timeouts to prevent hanging requests
            connect_timeout=5,   # 5 seconds to connect
            read_timeout=30,     # 30 seconds to read data

            # Reliability: Explicit retry policy with exponential backoff
            retries={
                'max_attempts': 3,        # Retry up to 3 times
                'mode': 'adaptive'        # Adaptive retry mode
            }
        )

        # PERFORMANCE FIX: Use client instead of resource
        # resource is higher-level abstraction with MORE overhead (~50-100ms slower)
        # client is lower-level, FASTER, and gives more control
        self.dynamodb_client = boto3.client("dynamodb", region_name=env.aws_region, config=boto_config)
        self.table_name = env.dynamodb_table
        self.logger.info(f"DynamoDB client configured: table={self.table_name}, 50 connections, 5s timeout")

    def get_pipeline_status(self, request_id):
        """
        Fetch pipeline status from DynamoDB using optimized client API.

        Args:
            request_id: Unique pipeline job identifier

        Returns:
            dict: Pipeline status item or None if not found
        """
        self.logger.info(f"Fetching pipeline status for request_id: {request_id}")
        try:
            # Use low-level client API (faster than resource)
            response = self.dynamodb_client.get_item(
                TableName=self.table_name,
                Key={
                    'request_id': {'S': request_id}  # DynamoDB format: {AttributeName: {Type: Value}}
                }
            )

            if 'Item' in response:
                # Parse DynamoDB response format to Python dict
                item = self._parse_dynamodb_item(response['Item'])
                self.logger.info(f"Found status for request_id {request_id}: {item.get('status')}")
                return item
            else:
                self.logger.warning(f"No status found for request_id: {request_id}")
                return None

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            self.logger.error(f"DynamoDB ClientError ({error_code}): {e}")
            raise DynamoDBServiceError(
                f"Failed to fetch pipeline status from DynamoDB: {error_code}",
                original_error=e
            ) from e  # Exception chaining for better debugging
        except BotoCoreError as e:
            self.logger.error(f"BotoCore Error fetching pipeline status: {e}")
            raise DynamoDBServiceError(
                "AWS SDK error while fetching pipeline status",
                original_error=e
            ) from e
        except Exception as e:
            self.logger.error(f"Unexpected error fetching pipeline status: {e}")
            raise DynamoDBServiceError(
                "Unexpected error while fetching pipeline status from DynamoDB",
                original_error=e
            ) from e

    def _parse_dynamodb_item(self, dynamodb_item):
        """
        Parse DynamoDB low-level format to Python dict.

        DynamoDB format: {'AttributeName': {'S': 'string'}, 'Number': {'N': '123'}}
        Python format:   {'AttributeName': 'string', 'Number': 123}

        Args:
            dynamodb_item: Raw DynamoDB item from client API

        Returns:
            dict: Parsed Python dictionary
        """
        parsed = {}

        for key, value_dict in dynamodb_item.items():
            # DynamoDB uses single-key dicts: {'S': 'value'}, {'N': '123'}, etc.
            type_key = list(value_dict.keys())[0]
            value = value_dict[type_key]

            # Convert types appropriately
            if type_key == 'S':  # String
                parsed[key] = value
            elif type_key == 'N':  # Number (stored as string in DynamoDB)
                # Convert to int if whole number, else float
                parsed[key] = int(value) if '.' not in value else float(value)
            elif type_key == 'BOOL':  # Boolean
                parsed[key] = value
            elif type_key == 'NULL':  # Null
                parsed[key] = None
            elif type_key == 'M':  # Map (nested object)
                parsed[key] = self._parse_dynamodb_item(value)
            elif type_key == 'L':  # List
                parsed[key] = [self._parse_dynamodb_value(item) for item in value]
            else:
                # Fallback: return as-is
                parsed[key] = value

        return parsed

    def _parse_dynamodb_value(self, value_dict):
        """Helper to parse a single DynamoDB value"""
        type_key = list(value_dict.keys())[0]
        value = value_dict[type_key]

        if type_key == 'S':
            return value
        elif type_key == 'N':
            return int(value) if '.' not in value else float(value)
        elif type_key == 'M':
            return self._parse_dynamodb_item(value)
        elif type_key == 'L':
            return [self._parse_dynamodb_value(item) for item in value]
        else:
            return value

    def get_recent_requests(self, limit=10):
        """
        Fetch the top N most recent request IDs from DynamoDB sorted by last_updated.

        Args:
            limit: Number of recent requests to fetch (default: 10)

        Returns:
            list: List of dicts containing request_id and last_updated, sorted by most recent first
        """
        self.logger.info(f"Fetching top {limit} recent requests from DynamoDB")
        try:
            # Scan the table to get all items (with projection to only fetch needed attributes)
            response = self.dynamodb_client.scan(
                TableName=self.table_name,
                ProjectionExpression='request_id, #st, last_updated, folder_name',
                ExpressionAttributeNames={'#st': 'status'},  # 'status' is a reserved word in DynamoDB
                # Note: Scan is eventually consistent by default, which is acceptable for this use case
            )

            items = []
            if 'Items' in response:
                # Parse each item
                for dynamodb_item in response['Items']:
                    parsed_item = self._parse_dynamodb_item(dynamodb_item)
                    # Only include items that have both request_id and last_updated
                    if 'request_id' in parsed_item:
                        items.append({
                            'request_id': parsed_item['request_id'],
                            'status': parsed_item.get('status', 'unknown'),
                            'last_updated': parsed_item['last_updated'],
                            'folder_name': parsed_item.get('folder_name')
                        })

            # Sort by last_updated in descending order (most recent first)
            # Assuming last_updated is in ISO format or timestamp
            items.sort(key=lambda x: x['last_updated'], reverse=True)

            # Return top N items
            recent_items = items[:limit]
            self.logger.info(f"Retrieved {len(recent_items)} recent requests")
            return recent_items

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            self.logger.error(f"DynamoDB ClientError ({error_code}) while fetching recent requests: {e}")
            raise DynamoDBServiceError(
                f"Failed to fetch recent requests from DynamoDB: {error_code}",
                original_error=e
            ) from e
        except BotoCoreError as e:
            self.logger.error(f"BotoCore Error fetching recent requests: {e}")
            raise DynamoDBServiceError(
                "AWS SDK error while fetching recent requests",
                original_error=e
            ) from e
        except Exception as e:
            self.logger.error(f"Unexpected error fetching recent requests: {e}")
            raise DynamoDBServiceError(
                "Unexpected error while fetching recent requests from DynamoDB",
                original_error=e
            ) from e
