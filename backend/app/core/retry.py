"""
Retry utilities and decorators.
"""
from functools import wraps
from typing import Callable, Type, Tuple
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)
from app.core.exceptions import RetryableError


def retry_on_retryable(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 8.0,
    multiplier: float = 2.0
):
    """
    Decorator to retry on RetryableError exceptions.
    
    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
        multiplier: Exponential backoff multiplier
    """
    def decorator(func: Callable):
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(RetryableError),
            reraise=True
        )
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def retry_on_exceptions(
    exceptions: Tuple[Type[Exception], ...],
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 8.0
):
    """
    Decorator to retry on specific exceptions.
    
    Args:
        exceptions: Tuple of exception types to retry on
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
    """
    def decorator(func: Callable):
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=2.0, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(exceptions),
            reraise=True
        )
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator

