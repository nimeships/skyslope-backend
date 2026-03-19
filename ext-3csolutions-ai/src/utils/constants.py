"""
Pipeline Constants and Configuration Values
Centralized configuration to eliminate magic numbers
"""

# ====================
# AWS Configuration
# ====================
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_DYNAMODB_TABLE = "PipelineStatus"
DEFAULT_SCHEMA_KEY = "schemas/output_schema.json"

# ====================
# Retry Configuration
# ====================
MAX_RETRY_ATTEMPTS = 5  # AWS SDK default retry limit
EXPONENTIAL_BACKOFF_BASE = 2  # Standard exponential backoff
MAX_BACKOFF_TIME = 60  # Maximum wait time between retries (seconds)

# ====================
# Timeout Configuration
# ====================
REQUEST_TIMEOUT = 1800  # Overall request timeout (30 minutes)
VALIDATION_TIMEOUT = 300  # Validation stage timeout (5 minutes)
THREAD_TIMEOUT = 60  # Default timeout for individual threads (1 minute)

# ====================
# Textract Configuration
# ====================
TEXTRACT_POLL_INTERVAL_INITIAL = 5  # Initial poll interval (seconds)
TEXTRACT_POLL_INTERVAL_MAX = 30  # Maximum poll interval (seconds)
TEXTRACT_JOB_TIMEOUT = 600  # Textract job timeout (10 minutes)
TEXTRACT_POLL_BACKOFF_MULTIPLIER = 1.5  # Exponential backoff multiplier

# ====================
# Concurrency Configuration
# ====================
VALIDATION_WORKER_POOL_SIZE = 10  # Parallel file validation workers
PDF_WORKER_POOL_SIZE = 3  # Lower due to larger payloads
LLM_WORKER_POOL_SIZE = 5  # Parallel LLM extraction workers
BATCH_SIZE = 10  # Number of files to process per batch

# ====================
# File Size Limits (bytes)
# ====================
MAX_ZIP_SIZE = 500 * 1024 * 1024  # 500MB
MAX_EXTRACTED_TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024  # 100MB
MAX_PDF_SIZE = 100 * 1024 * 1024  # 100MB
MAX_SCHEMA_SIZE = 1 * 1024 * 1024  # 1MB - JSON schema should be small

# ====================
# File Count Limits
# ====================
MAX_FILES_IN_ZIP = 500  # Maximum files allowed in ZIP
MAX_PDFS_TO_PROCESS = 200  # Maximum PDFs per job

# ====================
# LLM Configuration
# ====================
LLM_MAX_TOKENS = 5000  # Maximum tokens for LLM response
LLM_TEMPERATURE = 0  # Temperature for deterministic output
MAX_TEXT_CONTENT_LENGTH = 150000  # Maximum characters to send to LLM

# ====================
# S3 Configuration
# ====================
S3_SERVER_SIDE_ENCRYPTION = 'aws:kms'  # Encryption method
# Note: Set S3_KMS_KEY_ID in environment for custom KMS key

# ====================
# DynamoDB Stage Names
# ====================
STAGE_INITIALIZATION = "Initialization"
STAGE_UNZIP = "Unzip"
STAGE_VALIDATION = "Validation"
STAGE_EXTRACTION = "Extracting Data"
STAGE_COMPLETE = "Complete"
STAGE_ERROR = "Error"
STAGE_FAILED = "Failed"

# DynamoDB Status Values
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

# ====================
# File Extensions
# ====================
VALID_PDF_EXTENSIONS = {'.pdf', '.PDF'}
VALID_ZIP_EXTENSIONS = {'.zip', '.ZIP'}
VALID_JSON_EXTENSIONS = {'.json', '.JSON'}

# ====================
# Regex Patterns
# ====================
VALID_S3_KEY_PATTERN = r'^input/([a-zA-Z0-9_-]{10,128})/[a-zA-Z0-9_.-]{1,255}\.(zip|pdf|PDF|json|JSON)$'
VALID_REQUEST_ID_PATTERN = r'^[a-zA-Z0-9_-]{10,128}$'

# ====================
# Safe Logging Fields (No PII)
# ====================
SAFE_LOG_FIELDS = {
    'request_id',
    'stage',
    'status',
    'progress',
    'total',
    'message',
    'last_updated',
    'file_count',
    'success_count',
    'error_count',
    'start_time',
    'end_time',
    'duration_seconds',
    'file_name',  # Filename without content is safe
    'error_type',
    'error_category'
}
