"""
Custom exceptions for error classification.
"""
from typing import Optional


class RetryableError(Exception):
    """Error that may succeed on retry."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """Error that won't succeed on retry."""
    pass


class ValidationError(PermanentError):
    """Validation error - permanent."""
    pass


class AuthenticationError(PermanentError):
    """Authentication error - permanent."""
    pass


class TimeoutError(RetryableError):
    """Timeout error - retryable."""
    pass


class ConnectionError(RetryableError):
    """Connection error - retryable."""
    pass


class ResourceNotFoundError(PermanentError):
    """Resource not found - permanent."""
    pass


class ResourceConflictError(PermanentError):
    """Resource conflict (e.g., already exists) - permanent."""
    pass

