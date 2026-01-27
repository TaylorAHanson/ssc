
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract user identity from headers (Databricks Apps)
    and provide mock defaults for local development.
    """
    async def dispatch(self, request: Request, call_next):
        # Extract headers (case-insensitive usually, but using request.headers.get is safe)
        # Databricks Apps headers:
        # X-Forwarded-Email: IdP email
        # X-Forwarded-Preferred-Username: IdP display/username
        # X-Forwarded-User: IdP user identifier
        
        email = request.headers.get("X-Forwarded-Email", settings.MOCK_USER_EMAIL)
        username = request.headers.get("X-Forwarded-Preferred-Username", settings.MOCK_USER_NAME)
        user_id = request.headers.get("X-Forwarded-User", settings.MOCK_USER_ID)
        
        # Store in request state
        request.state.user = {
            "email": email,
            "username": username,
            "id": user_id
        }
        
        # Log only if different from default (to avoid noise) or on every request for debugging
        if email != settings.MOCK_USER_EMAIL:
             logger.debug(f"AuthMiddleware: User context set for {email}")
        
        response = await call_next(request)
        return response
