"""
Custom Exception Classes for 3C Solutions Lambda

This module defines a hierarchy of custom exceptions that map to specific
HTTP status codes and provide better error context than generic exceptions.
"""

from typing import Optional


class ThreeCSolutionError(Exception):
    """Base exception for all 3C Solution errors"""
    def __init__(self, message: str, status_code: int = 500, original_error: Optional[Exception] = None):
        self.message = message
        self.status_code = status_code
        self.original_error = original_error
        super().__init__(self.message)


# 4xx Client Errors

class NotFoundError(ThreeCSolutionError):
    """Raised when a requested resource is not found (404)"""
    def __init__(self, message: str = "Resource not found", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=404, original_error=original_error)


class ValidationError(ThreeCSolutionError):
    """Raised when input validation fails (400)"""
    def __init__(self, message: str = "Validation failed", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=400, original_error=original_error)


class UnauthorizedError(ThreeCSolutionError):
    """Raised when authentication is required but missing/invalid (401)"""
    def __init__(self, message: str = "Unauthorized", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=401, original_error=original_error)


class ForbiddenError(ThreeCSolutionError):
    """Raised when user lacks permission for the requested action (403)"""
    def __init__(self, message: str = "Forbidden", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=403, original_error=original_error)


# 5xx Server Errors

class S3ServiceError(ThreeCSolutionError):
    """Raised when S3 operations fail (500)"""
    def __init__(self, message: str = "S3 service error", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=500, original_error=original_error)


class DynamoDBServiceError(ThreeCSolutionError):
    """Raised when DynamoDB operations fail (500)"""
    def __init__(self, message: str = "DynamoDB service error", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=500, original_error=original_error)


class ServiceUnavailableError(ThreeCSolutionError):
    """Raised when a dependent service is unavailable (503)"""
    def __init__(self, message: str = "Service unavailable", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=503, original_error=original_error)


class InternalServerError(ThreeCSolutionError):
    """Raised for unexpected internal errors (500)"""
    def __init__(self, message: str = "Internal server error", original_error: Optional[Exception] = None):
        super().__init__(message, status_code=500, original_error=original_error)
