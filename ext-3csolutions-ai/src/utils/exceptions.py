"""
Custom Exception Classes for Document Extraction Pipeline
Provides specific exception types for better error handling and debugging
"""


class PipelineException(Exception):
    """Base exception for all pipeline errors"""
    pass


class ValidationError(PipelineException):
    """Raised when input validation fails"""
    pass


class SecurityError(PipelineException):
    """Raised when security constraints are violated"""
    pass


class ConfigurationError(PipelineException):
    """Raised when configuration is invalid or missing"""
    pass


class S3OperationError(PipelineException):
    """Raised when S3 operations fail"""
    pass


class TextractOperationError(PipelineException):
    """Raised when Textract operations fail"""
    pass


class TextractTimeoutError(TextractOperationError):
    """Raised when Textract job exceeds timeout"""
    pass


class BedrockOperationError(PipelineException):
    """Raised when Bedrock operations fail"""
    pass


class DynamoDBOperationError(PipelineException):
    """Raised when DynamoDB operations fail"""
    pass


class MaxRetriesExceededError(PipelineException):
    """Raised when maximum retry attempts are exceeded"""
    pass


class SchemaValidationError(PipelineException):
    """Raised when JSON schema validation fails"""
    pass


class FileSizeLimitError(SecurityError):
    """Raised when file size exceeds limits"""
    pass


class InvalidFileFormatError(ValidationError):
    """Raised when file format is invalid"""
    pass


class DuplicateFileError(ValidationError):
    """Raised when duplicate file is detected"""
    pass


class CircuitOpenError(PipelineException):
    """Raised when circuit breaker is OPEN (service unavailable)"""
    pass


class CostLimitExceededError(PipelineException):
    """Raised when request exceeds cost limit"""
    pass


class RateLimitExceededError(PipelineException):
    """Raised when request exceeds rate/token limit"""
    pass


class ExtractionError(PipelineException):
    """Raised when document extraction fails"""
    pass


class DirectPDFExtractionError(ExtractionError):
    """Raised when direct PDF extraction fails"""
    pass
